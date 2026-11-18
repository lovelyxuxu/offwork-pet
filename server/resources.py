"""Self-hosted, read-only character catalog. Publication is an operator CLI action."""
import hashlib
import json
import logging
from itertools import islice
from threading import Lock
import time
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from offwork_pet.package_limits import MAX_PACKAGE_ARCHIVE


def directory():
    return Path(os.getenv('OFFWORK_RESOURCE_DIR', 'data/resources'))


def publish(archive):
    from offwork_pet.packages import PackageLibrary
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root) as temp:
        staged = Path(temp)/'published'
        staged.mkdir()
        # Bound even the snapshot copy; a changing or oversized source must not fill the volume.
        size = 0
        with Path(archive).open('rb') as source, (staged/'character.petpack').open('xb') as destination:
            while chunk := source.read(1024**2):
                size += len(chunk)
                if size > MAX_PACKAGE_ARCHIVE:
                    raise ValueError('角色压缩包超过大小限制')
                destination.write(chunk)
        package = PackageLibrary(Path(temp)/'library').install(staged/'character.petpack')
        manifest = package.manifest
        target = root/package.id
        shutil.copyfile(package.asset(manifest['thumbnail']), staged/'thumbnail')
        with (staged/'character.petpack').open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        row = {'id': package.id, 'name': package.name, 'version': manifest['version'],
               'author': manifest.get('author', ''), 'description': manifest.get('description', ''),
               'credits': manifest.get('credits', []), 'format_version': 1,
               'size': (staged/'character.petpack').stat().st_size, 'sha256': digest,
               'download_url': f'/api/catalog/characters/{package.id}/download',
               'thumbnail_url': f'/api/catalog/characters/{package.id}/thumbnail'}
        (staged/'metadata.json').write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
        # Publication is immutable in v1; no reader can observe a partial replacement.
        if target.exists() or target.is_symlink():
            raise ValueError('该角色已经发布；第一版请使用新的包标识，禁止覆盖线上资源')
        staged.rename(target)
        return row


class Catalog:
    """Cache immutable publication metadata; refresh on root changes or after five seconds."""
    def __init__(self):
        self.lock = Lock()
        self.key = None
        self.expires = 0
        self.payload = b'{"items":[]}'

    def read(self):
        root = directory().resolve()
        with self.lock:
            key = root, root.stat().st_mtime_ns if root.exists() else None
            now = time.monotonic()
            if key == self.key and now < self.expires:
                return self.payload
            children = list(islice(root.iterdir(), 4097)) if root.exists() else []
            if len(children) > 4096:
                raise HTTPException(503, '角色目录超出当前容量限制')
            items = []
            for child in sorted(children):
                path = child/'metadata.json'
                if child.is_symlink() or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', child.name):
                    continue
                try:
                    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
                        continue
                    with path.open('rb') as stream:
                        raw = stream.read(65537)
                    if len(raw) > 65536:
                        continue
                    row = json.loads(raw)
                    if not isinstance(row, dict) or row.get('id') != child.name:
                        continue
                    items.append(row)
                except (OSError, ValueError, RecursionError):
                    logging.getLogger(__name__).warning('Skipping invalid catalog entry: %s', child.name)
            payload = json.dumps({'items': items}, ensure_ascii=False).encode('utf-8')
            if len(payload) > 1024**2:
                raise HTTPException(503, '角色目录超出客户端响应预算')
            self.payload, self.key, self.expires = payload, key, now+5
            return payload


def install(app):
    cache = Catalog()

    @app.get('/api/catalog/characters')
    def catalog():
        return Response(cache.read(), media_type='application/json')

    @app.get('/api/catalog/characters/{identifier}/{asset}')
    def resource(identifier: str, asset: str):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', identifier) or asset not in ('download', 'thumbnail'):
            raise HTTPException(404)
        path = directory()/identifier/('character.petpack' if asset == 'download' else 'thumbnail')
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(directory().resolve()):
            raise HTTPException(404)
        return FileResponse(path)
