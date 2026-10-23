"""Signed product releases. The server distributes files, never executable instructions."""
import base64
import json
import re
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .update_public_key import PUBLIC_KEY

UPDATE_ORIGIN = 'http://106.54.27.8'
MANIFEST_URL = UPDATE_ORIGIN + '/offwork-pet/updates/latest.json'
MAX_ARCHIVE = 512 * 1024 * 1024


class UpdateError(ValueError):
    pass


def version_key(value):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-preview\.(\d+))?', value) if isinstance(value, str) else None
    if not match or len(value) > 48:
        raise UpdateError('更新版本号不正确')
    major, minor, patch, preview = match.groups()
    return (int(major), int(minor), int(patch), preview is None, int(preview or 0))


def canonical(release):
    return json.dumps(release, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def verify_manifest(document, public_key=PUBLIC_KEY):
    try:
        release = document['release']
        signature = base64.b64decode(document['signature'], validate=True)
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(signature, canonical(release))
    except (InvalidSignature, ValueError, TypeError, KeyError, OverflowError) as error:
        raise UpdateError('更新信息校验失败，请稍后重试') from error
    if not isinstance(release, dict):
        raise UpdateError('更新信息格式不正确')
    version_key(release.get('version'))
    verify_download(release)
    if not isinstance(release.get('notes', ''), str) or len(release.get('notes', '')) > 8000:
        raise UpdateError('版本说明格式不正确')
    deltas = release.get('deltas', [])
    if not isinstance(deltas, list) or len(deltas) > 32:
        raise UpdateError('增量更新信息不正确')
    seen = set()
    for delta in deltas:
        if not isinstance(delta, dict):
            raise UpdateError('增量更新信息不正确')
        base = delta.get('fromVersion')
        if version_key(base) >= version_key(release['version']) or base in seen:
            raise UpdateError('增量更新基础版本不正确')
        seen.add(base)
        verify_download(delta)
    return release


def verify_download(release):
    if type(release.get('size')) is not int or not 0 < release['size'] <= MAX_ARCHIVE:
        raise UpdateError('更新包大小不正确')
    if not isinstance(release.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', release['sha256']):
        raise UpdateError('更新包校验信息不正确')
    url = release.get('url', '')
    parsed, origin = urlsplit(url), urlsplit(UPDATE_ORIGIN)
    if (parsed.scheme != origin.scheme or parsed.netloc != origin.netloc
            or parsed.query or parsed.fragment or parsed.username
            or not re.fullmatch(r'/offwork-pet/releases/[A-Za-z0-9_.-]+\.zip', parsed.path)):
        raise UpdateError('更新下载地址不正确')


def select_delta(release, installed_version):
    """Only choose a direct patch for this version when it saves download bytes."""
    return next((item for item in release.get('deltas', [])
        if item['fromVersion'] == installed_version and item['size'] < release['size']), None)
