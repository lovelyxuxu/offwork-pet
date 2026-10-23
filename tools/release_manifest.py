"""Generate a local DPAPI-protected signing key and sign a built release."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from offwork_pet.credentials import crypt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key', type=Path, required=True)
    parser.add_argument('--init-key', action='store_true')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--notes-file', type=Path, default=ROOT / 'release-notes.txt')
    parser.add_argument('--base-archive', type=Path, action='append', default=None,
        help='Create a direct delta from this full ZIP (repeatable; default: latest three local releases).')
    args = parser.parse_args()
    if args.init_key:
        if args.key.exists():
            raise SystemExit('Signing key already exists; do not rotate it accidentally.')
        key = Ed25519PrivateKey.generate()
        args.key.parent.mkdir(parents=True, exist_ok=True)
        args.key.write_bytes(crypt(key.private_bytes_raw()))
        (ROOT / 'offwork_pet/update_public_key.py').write_text(
            '"""Pinned public release key. The private key never ships with the application."""\n'
            + f'PUBLIC_KEY = "{key.public_key().public_bytes_raw().hex()}"\n', encoding='utf-8')
        print('Created DPAPI-protected release key and pinned public key.')
        return
    from offwork_pet import __version__
    from offwork_pet.update_format import canonical, verify_manifest, UPDATE_ORIGIN, PUBLIC_KEY
    key = Ed25519PrivateKey.from_private_bytes(crypt(args.key.read_bytes(), decrypt=True))
    if key.public_key().public_bytes_raw().hex() != PUBLIC_KEY:
        raise SystemExit('Release signing key does not match the pinned public key.')
    with args.archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    release = {'version': __version__, 'url': UPDATE_ORIGIN + '/offwork-pet/releases/' + args.archive.name,
               'size': args.archive.stat().st_size, 'sha256': digest,
               'publishedAt': datetime.now(timezone.utc).isoformat(),
               'notes': args.notes_file.read_text(encoding='utf-8').strip() if args.notes_file.exists() else '新版本已发布，请查看程序内的版本说明。'}
    from tools.delta_release import build_delta, recent_bases
    bases = args.base_archive if args.base_archive is not None else recent_bases(args.archive, __version__)
    release['deltas'] = []
    for base in bases:
        delta = build_delta(base, args.archive, args.archive.parent)
        if delta['size'] < release['size']:
            release['deltas'].append(delta)
            print(f'Delta from {delta["fromVersion"]}: {delta["size"] / 1048576:.1f} MB')
        else:
            print(f'Delta from {delta["fromVersion"]} is not smaller; clients will use the full ZIP.')
    document = {'release': release, 'signature': base64.b64encode(key.sign(canonical(release))).decode()}
    verify_manifest(document)
    if len(json.dumps(document, ensure_ascii=False).encode('utf-8')) > 60000:
        raise SystemExit('Signed manifest exceeds the client size limit.')
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Signed release:', args.output)


if __name__ == '__main__':
    main()
