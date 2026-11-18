"""Public character browsing and bounded downloads, shared with the native shop UI."""
from concurrent.futures import ThreadPoolExecutor
import io
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from PIL import Image

from .packages import PackageError
from .package_limits import MAX_PACKAGE_ARCHIVE
from .buddy import SERVICE_ENDPOINT

ORIGIN = SERVICE_ENDPOINT
PAGE_SIZE = 12
MAX_DOWNLOAD = MAX_PACKAGE_ARCHIVE


def source_url(value, origin=None):
    base = ORIGIN if origin is None else origin
    url = urljoin(base, value)
    parts = urlsplit(url)
    origin = urlsplit(base)
    if (parts.scheme != origin.scheme or parts.netloc != origin.netloc
            or parts.username or parts.password):
        raise PackageError('角色站返回了不支持的下载地址')
    return url


def _chunks(value, limit, cancelled, progress, origin):
    if cancelled():
        raise PackageError('已取消')
    request = Request(source_url(value, origin), headers={
        'User-Agent': 'Mozilla/5.0 OffworkPet', 'Accept': '*/*'})
    count = 0
    with urlopen(request, timeout=12) as response:
        source_url(response.url, origin)
        while True:
            if cancelled():
                raise PackageError('已取消')
            chunk = response.read(65536)
            if not chunk:
                break
            count += len(chunk)
            if count > limit:
                raise PackageError('角色站返回的文件过大')
            progress(count)
            yield chunk
    if cancelled():
        raise PackageError('已取消')


def fetch(value, limit, cancelled=lambda: False, progress=lambda _: None, *, origin=None):
    return b''.join(_chunks(value, limit, cancelled, progress, origin))


def download(value, target, limit, cancelled=lambda: False, progress=lambda _: None, *, origin=None):
    """Stream an archive to disk, calculating its identity without a full byte buffer."""
    digest, count = hashlib.sha256(), 0
    try:
        with target.open('wb') as stream:
            for chunk in _chunks(value, limit, cancelled, progress, origin):
                stream.write(chunk)
                digest.update(chunk)
                count += len(chunk)
        return count, digest.hexdigest()
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def catalog(query='', page=1, cancelled=lambda: False, *, origin=None):
    # The first-party website returns its complete catalog. Search and pagination
    # use that same list, rather than assuming an unrelated site's API contract.
    document = json.loads(fetch('/api/catalog/characters', 1024**2, cancelled, **({'origin': origin} if origin else {})))
    rows = document.get('items') if isinstance(document, dict) else None
    if not isinstance(rows, list) or len(rows) > 4096:
        raise PackageError('角色列表格式不正确，请稍后重试')
    entries = []
    query = query[:120].strip().casefold()
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,95}', str(row.get('id', ''))):
            continue
        if row.get('format_version') != 1:
            continue
        search_text = ' '.join(str(row.get(k, '')) for k in ('name', 'author', 'description', 'tags')).casefold()
        if query and query not in search_text:
            continue
        if (type(row.get('size')) is not int or not 0 < row['size'] <= MAX_DOWNLOAD
                or not re.fullmatch('[0-9a-f]{64}', str(row.get('sha256', '')))):
            raise PackageError('角色下载信息不完整，请稍后重试')
        entry = {target: str(row.get(source, ''))[:2000] for target, source in
                 [('id', 'id'), ('displayName', 'name'), ('ownerName', 'author'),
                  ('description', 'description'), ('version', 'version')]}
        entry['downloadUrl'] = source_url(row['download_url'], origin)
        entry['posterUrl'] = source_url(row['thumbnail_url'], origin)
        entry['size'], entry['sha256'] = row['size'], row['sha256']
        entry['creditsText'] = '\n'.join(
            f"{credit.get('author', '')}：{credit.get('source', '')}\n{credit.get('license', '')}"
            for credit in row.get('credits', [])[:8] if isinstance(credit, dict))[:1200]
        entries.append(entry)
    pages = max(1, math.ceil(len(entries)/PAGE_SIZE))
    start = (max(1, page)-1)*PAGE_SIZE
    entries = entries[start:start+PAGE_SIZE]
    def preview(entry):
        try:
            raw = fetch(entry['posterUrl'], 1024**2, cancelled, **({'origin': origin} if origin else {}))
            with Image.open(io.BytesIO(raw)) as image:
                if image.width*image.height > 4_000_000:
                    return
                image.thumbnail((160, 160))
                output = io.BytesIO()
                image.save(output, format='PNG')
                entry['preview'] = output.getvalue()
        except Exception:
            pass  # A failed preview does not prevent browsing/installing.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(preview, entries))
    return entries, pages


def prepare(entry, folder, cancelled=lambda: False, progress=lambda _: None, *, origin=None):
    """Validate in isolation. Installing/activating is always done on the GUI thread."""
    from .packages import PackageLibrary
    folder = Path(folder)
    target = folder/'download.petpack'
    size, digest = entry['size'], entry['sha256']
    if (type(size) is not int or not 0 < size <= MAX_DOWNLOAD
            or not re.fullmatch('[0-9a-f]{64}', str(digest))):
        raise PackageError('角色下载信息不正确')
    try:
        count, actual_digest = download(entry['downloadUrl'], target, size, cancelled, progress, origin=origin)
        if count != size or actual_digest != digest:
            raise PackageError('角色包大小或摘要与网站信息不一致，请重试')
        if cancelled():
            raise PackageError('已取消')
        package = PackageLibrary(folder/'validation').install(target)
        if package.id != entry['id'] or package.manifest['version'] != entry['version']:
            raise PackageError('角色标识或版本与所选条目不一致')
        if cancelled():
            raise PackageError('已取消')
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise
