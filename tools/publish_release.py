"""Publish a signed ZIP and update feed to the product server. Password arrives on stdin."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sys
import time
import uuid
from urllib.parse import urlsplit

import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from offwork_pet.update_format import verify_manifest


def release_artifacts(archive, release):
    """Verify every download before connecting; publish the signed feed only after all exist."""
    artifacts, seen = [], set()
    for index, info in enumerate([release, *release.get('deltas', [])]):
        name = Path(urlsplit(info['url']).path).name
        path = archive if index == 0 else archive.parent / name
        if path.name != name or name in seen:
            raise ValueError('Release artifact name does not match signed manifest or is repeated.')
        seen.add(name)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != info['sha256'] or path.stat().st_size != info['size']:
            raise ValueError('Release ZIP does not match signed manifest: ' + name)
        artifacts.append((path, info, f'artifact-{index}.zip'))
    return artifacts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    document = json.loads(args.manifest.read_text(encoding='utf-8'))
    release = verify_manifest(document)
    artifacts = release_artifacts(args.archive, release)
    password = sys.stdin.readline().rstrip('\r\n')
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('106.54.27.8', username='ubuntu', password=password, timeout=15, allow_agent=False, look_for_keys=False)
    ssh.get_transport().set_keepalive(15)
    password = None
    identifier = uuid.uuid4().hex
    staging = '/tmp/offwork-publish-' + identifier
    destination = '/var/www/offwork-pet-updates'
    quote = shlex.quote
    def run(command):
        _, output, errors = ssh.exec_command(command, timeout=45)
        result, error = output.read().decode(), errors.read().decode()
        if output.channel.recv_exit_status() != 0:
            raise RuntimeError(error or result or 'Server command failed')
        return result
    try:
        print(run('df -h /var/www'), flush=True)
        run('mkdir -m 700 ' + quote(staging))
        with ssh.open_sftp() as sftp:
            sftp.get_channel().settimeout(45)
            last = [-1]
            def progress(done, total):
                percent = int(done * 100 / total)
                if percent // 10 != last[0]:
                    last[0] = percent // 10
                    print(f'Upload {percent}%', flush=True)
            for path, _, temporary in artifacts:
                last[0] = -1
                print('Uploading ' + path.name, flush=True)
                # Wait for each write acknowledgement instead of accumulating an
                # unbounded queue of pipelined requests on a slow connection.
                with path.open('rb') as source, sftp.file(staging + '/' + temporary, 'wb') as target:
                    done, total = 0, path.stat().st_size
                    while chunk := source.read(128 * 1024):
                        target.write(chunk)
                        done += len(chunk)
                        progress(done, total)
            sftp.put(str(args.manifest), staging + '/latest.json')
        run('sudo -n install -d -m 755 ' + quote(destination + '/releases'))
        for path, info, temporary in artifacts:
            remote_digest = run('sha256sum ' + quote(staging + '/' + temporary)).split()[0]
            if remote_digest != info['sha256']:
                raise RuntimeError('Uploaded ZIP checksum failed: ' + path.name)
            remote_archive = destination + '/releases/' + path.name
            exists = run('if test -f ' + quote(remote_archive) + '; then sha256sum ' + quote(remote_archive) + '; fi').strip()
            if exists and exists.split()[0] != info['sha256']:
                raise RuntimeError('This version already exists with different contents. Increase the version first.')
            if not exists:
                run('sudo -n install -m 644 ' + quote(staging + '/' + temporary) + ' ' + quote(remote_archive))
        snippet_path = '/etc/nginx/snippets/offwork-pet.conf'
        original = run('sudo -n cat ' + quote(snippet_path))
        if '# Offwork Pet signed updates' not in original:
            snippet = original + '''
# Offwork Pet signed updates
location = /offwork-pet/updates/latest.json {
    alias /var/www/offwork-pet-updates/latest.json;
    default_type application/json;
    add_header Cache-Control "no-store" always;
}
location ^~ /offwork-pet/releases/ {
    alias /var/www/offwork-pet-updates/releases/;
    default_type application/zip;
    autoindex off;
    add_header Cache-Control "public, max-age=31536000, immutable";
}
'''
            backup = '/opt/offwork-pet/backups/nginx-before-updates-' + identifier + '.conf'
            with ssh.open_sftp() as sftp:
                with sftp.file(staging + '/nginx.conf', 'w') as target:
                    target.write(snippet)
            run('sudo -n cp ' + quote(snippet_path) + ' ' + quote(backup))
            run('sudo -n install -m 644 ' + quote(staging + '/nginx.conf') + ' ' + quote(snippet_path))
            try:
                run('sudo -n nginx -t')
                run('sudo -n systemctl reload nginx')
            except Exception:
                run('sudo -n cp ' + quote(backup) + ' ' + quote(snippet_path))
                raise
        # Publish the manifest last so clients never see a release whose ZIP is not available.
        run('sudo -n install -m 644 ' + quote(staging + '/latest.json') + ' ' + quote(destination + '/latest.json.next'))
        run('sudo -n mv ' + quote(destination + '/latest.json.next') + ' ' + quote(destination + '/latest.json'))
        print('PUBLISHED ' + release['url'], flush=True)
        print('Feed: http://106.54.27.8/offwork-pet/updates/latest.json', flush=True)
        # Only remove the individually named uploaded files, never recurse over a computed target.
        for name in [temporary for _, _, temporary in artifacts] + ['latest.json', 'nginx.conf']:
            run('rm -f -- ' + quote(staging + '/' + name))
        run('rmdir -- ' + quote(staging))
    finally:
        ssh.close()


if __name__ == '__main__':
    main()
