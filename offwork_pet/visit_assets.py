"""Data-only visitor assets, isolated from the user's installed characters."""
import hashlib
import os
from pathlib import Path
import re
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

from .packages import PackageError, PackageLibrary, load_package, require
from .package_limits import MAX_PACKAGE_ARCHIVE

MAX_ARCHIVE = MAX_PACKAGE_ARCHIVE
CACHE_BUDGET = 512 * 1024**2


def matches(package, capabilities):
    return package.snapshot(capabilities['revision']) == capabilities


def _archive_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def export_package(package, destination):
    destination = Path(destination)
    try:
        require(load_package(package.root).digest == package.digest, '串门', '角色素材已变化')
        root = package.root.resolve()
        with ZipFile(destination, 'w', ZIP_DEFLATED) as zipped:
            for path in sorted(package.root.rglob('*')):
                if path.is_file():
                    require(not path.is_symlink() and path.resolve().is_relative_to(root), '串门', '不支持链接素材')
                    zipped.write(path, path.relative_to(package.root).as_posix())
                    require(destination.stat().st_size <= MAX_ARCHIVE, '串门', '角色包超过 64 MiB')
        size = destination.stat().st_size
        require(size <= MAX_ARCHIVE, '串门', '角色包超过 64 MiB')
        return {'sha256': _archive_digest(destination), 'size': size}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


class VisitorCache:
    def __init__(self, root, budget=CACHE_BUDGET):
        self.root = Path(root)
        self.budget = budget
        self.root.mkdir(parents=True, exist_ok=True)

    def slot(self, capabilities):
        digest = capabilities.get('digest')
        require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), '串门', '角色摘要不正确')
        path = self.root / digest
        require(not path.is_symlink() and path.resolve().is_relative_to(self.root.resolve()), '串门', '缓存目录越界')
        return path

    def find(self, capabilities):
        slot = self.slot(capabilities)
        if not (slot/'catalog.json').exists():
            return None
        try:
            package = PackageLibrary(slot).load(capabilities['packageId'])
            if matches(package, capabilities):
                os.utime(slot, None)
                return package
        except (PackageError, OSError):
            pass
        return None

    def install(self, archive, visit):
        archive = Path(archive)
        require(0 < archive.stat().st_size == visit['size'] <= MAX_ARCHIVE, '串门', '下载大小不正确')
        require(_archive_digest(archive) == visit['sha256'], '串门', '下载校验失败')
        capabilities = visit['capabilities']
        slot = self.slot(capabilities)
        cached = self.find(capabilities)
        if cached:
            return cached
        # Each digest owns an independent validated library, including packages
        # with the same id/version but different contents on the two desktops.
        self.prune(keep=slot, reserve=256 * 1024**2)
        library = PackageLibrary(slot)
        try:
            package = library.install(archive)
            require(matches(package, capabilities), '串门', '角色内容与搭子声明不一致')
        except BaseException:
            self.remove(slot)
            raise
        os.utime(slot, None)
        self.prune(keep=slot)
        return package

    def remove(self, path):
        path = Path(path)
        require(path.parent.resolve() == self.root.resolve() and re.fullmatch('[0-9a-f]{64}', path.name)
                and not path.is_symlink(), '串门', '缓存清理目录越界')
        shutil.rmtree(path, ignore_errors=True)

    def prune(self, keep=None, reserve=0):
        slots = [p for p in self.root.iterdir() if p.is_dir() and not p.is_symlink() and re.fullmatch('[0-9a-f]{64}', p.name)]
        sizes = {p: sum(f.stat().st_size for f in p.rglob('*') if f.is_file() and not f.is_symlink()) for p in slots}
        total = sum(sizes.values())
        for path in sorted(slots, key=lambda p: p.stat().st_mtime):
            if total+reserve <= self.budget and len(sizes) <= 3:
                break
            if path != keep:
                self.remove(path)
                total -= sizes.pop(path)
        require(total+reserve <= self.budget, '串门', '访客缓存空间不足')
