"""Consistent Compose backup/restore. Restore requires a fresh database and resources."""
import argparse
import hashlib
import json
from pathlib import PurePosixPath
import tarfile
from pathlib import Path
import shutil
import subprocess


FILES = ('database.dump', 'resources.tar.gz', 'instance.env')


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024**2):
            digest.update(chunk)
    return digest.hexdigest()


def validate_archive(path):
    root_seen = False
    with tarfile.open(path, 'r|gz') as archive:
        for entry in archive:
            name = PurePosixPath(entry.name)
            if (name.is_absolute() or not name.parts or name.parts[0] != 'resources'
                    or '..' in name.parts or '\\' in entry.name
                    or (name.parts == ('resources',) and not entry.isdir())
                    or not (entry.isfile() or entry.isdir())):
                raise ValueError('Resource backup contains an unsafe path or entry type')
            root_seen |= name.parts == ('resources',)
    if not root_seen:
        raise ValueError('Resource backup is missing its resources directory')


def write_manifest(folder):
    validate_archive(folder/'resources.tar.gz')
    manifest = {name: {'size': (folder/name).stat().st_size, 'sha256': file_digest(folder/name)} for name in FILES}
    (folder/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (folder/'COMPLETE').write_text('offwork-backup-v2\n', encoding='utf-8')


def validate_backup(folder):
    if (folder/'COMPLETE').read_text(encoding='utf-8').strip() != 'offwork-backup-v2':
        raise ValueError('Unsupported or incomplete backup; generate a new v2 backup')
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or set(manifest) != set(FILES):
        raise ValueError('Invalid backup manifest')
    for name in FILES:
        path = folder/name
        if (path.is_symlink() or not path.is_file() or not isinstance(manifest[name], dict)
                or manifest[name].get('size') != path.stat().st_size
                or manifest[name].get('sha256') != file_digest(path)):
            raise ValueError(f'Backup integrity check failed: {name}')
    with (folder/'database.dump').open('rb') as stream:
        if stream.read(5) != b'PGDMP':
            raise ValueError('Invalid PostgreSQL custom dump')
    validate_archive(folder/'resources.tar.gz')


def backup(folder, root, run, was_running):
    folder.mkdir(parents=True, exist_ok=False)
    folder.chmod(0o700)
    try:
        if was_running:
            run('stop', 'app')
        with (folder/'database.dump').open('xb') as stream:
            run('exec', '-T', 'db', 'pg_dump', '-U', 'offwork', '-d', 'offwork', '-Fc', stdout=stream)
        with (folder/'resources.tar.gz').open('xb') as stream:
            run('run', '--rm', '--no-deps', '--entrypoint', 'tar', 'app', '-C', '/data', '-czf', '-', 'resources', stdout=stream)
        shutil.copyfile(root/'.env', folder/'instance.env')
        (folder/'instance.env').chmod(0o600)
        write_manifest(folder)
    finally:
        if was_running:
            run('start', 'app')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('backup', 'restore'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--project', required=True, help='Explicit Compose project to operate on')
    parser.add_argument('--file', action='append', help='Compose files, in the same order used for deployment')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = ['docker', 'compose', '-p', args.project]
    for file in args.file or []:
        command.extend(['-f', file])

    def run(*parts, **kwargs):
        return subprocess.run(command+list(parts), cwd=root, check=True, **kwargs)

    folder = args.directory.resolve()
    if args.operation == 'restore':
        # Validate all inputs before touching the destination database or volume.
        validate_backup(folder)
    container = run('ps', '-q', '--all', 'app', capture_output=True).stdout.decode().strip()
    was_running = False
    if container:
        state = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', container], check=True, capture_output=True)
        was_running = state.stdout.strip() == b'true'
    if args.operation == 'backup':
        backup(folder, root, run, was_running)
    else:
        if was_running:
            raise ValueError('Restore requires a stopped application')
        # Do not overwrite an existing instance, even if it currently has no users.
        result = run('exec', '-T', 'db', 'psql', '-U', 'offwork', '-d', 'offwork', '-Atc',
                     "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'", capture_output=True)
        if result.stdout.strip() != b'0':
            raise ValueError('Restore requires a fresh database with no application tables')
        result = run('run', '--rm', '--no-deps', '--entrypoint', 'find', 'app', '/data/resources', '-mindepth', '1', '-print', '-quit', capture_output=True)
        if result.stdout.strip():
            raise ValueError('Restore requires an empty resource directory')
        # Only restore trusted backups created by this tool, never arbitrary tar archives.
        with (folder/'database.dump').open('rb') as stream:
            run('exec', '-T', 'db', 'pg_restore', '-U', 'offwork', '-d', 'offwork', '--exit-on-error', '--single-transaction', stdin=stream)
        with (folder/'resources.tar.gz').open('rb') as stream:
            run('run', '--rm', '--no-deps', '--entrypoint', 'tar', 'app', '-C', '/data', '-xzf', '-', stdin=stream)
        run('up', '-d', 'app')
    print(f'{args.operation} completed: {folder}')


if __name__ == '__main__':
    main()
