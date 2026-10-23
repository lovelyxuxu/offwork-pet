"""File-level patches: copy verified local files into a fresh, complete release."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import zipfile

from .update_format import UpdateError
from .update_install import (MAX_UNPACKED, check_archive, linked, marker, owned_path,
    relative_file, remove_owned, validate_install)

MAX_INDEX = 8 * 1024 * 1024


def file_path(name):
    path = relative_file(name)
    if path.as_posix() != name:
        raise UpdateError('增量更新文件路径不正确')
    return path


def validate_names(names):
    seen = set()
    for name in names:
        key = file_path(name).as_posix().casefold()
        if key in seen:
            raise UpdateError('增量更新包含重复文件')
        seen.add(key)
    for name in seen:
        if any(parent.as_posix() in seen for parent in file_path(name).parents if parent.parts):
            raise UpdateError('增量更新文件路径冲突')


def release_entries(bundle, delta=False):
    files = {}
    total = 0
    index = None
    for entry in bundle.infolist():
        if entry.is_dir():
            continue
        if entry.flag_bits & 1 or stat.S_ISLNK(entry.external_attr >> 16):
            raise UpdateError('更新包包含不支持的文件')
        path = file_path(entry.filename)
        if delta and entry.filename == 'delta-info.json':
            if index is not None or entry.file_size > MAX_INDEX:
                raise UpdateError('增量更新文件清单不正确')
            index = entry
            continue
        if len(path.parts) < 2 or path.parts[0] != 'OffworkPet':
            raise UpdateError('更新包目录结构不正确')
        name = '/'.join(path.parts[1:])
        if name in files:
            raise UpdateError('更新包包含重复文件')
        files[name] = entry
        total += entry.file_size
    if len(files) > 20000 or total > MAX_UNPACKED:
        raise UpdateError('更新包解压后超过限制')
    validate_names(files)
    return files, index


def delta_index(bundle, release, delta):
    entries, index = release_entries(bundle, delta=True)
    if index is None:
        raise UpdateError('增量更新文件清单缺失')
    try:
        data = json.loads(bundle.read(index))
        if (data['format'] != 1 or data['version'] != release['version']
                or data['fromVersion'] != delta['fromVersion']):
            raise ValueError()
        files = data['files']
        if not isinstance(files, dict) or not 0 < len(files) <= 20000:
            raise ValueError()
        total = 0
        for info in files.values():
            if (not isinstance(info, dict) or type(info.get('size')) is not int or info['size'] < 0
                    or not isinstance(info.get('sha256'), str)
                    or not re.fullmatch('[a-f0-9]{64}', info['sha256'])):
                raise ValueError()
            total += info['size']
        if total > MAX_UNPACKED or not set(entries) <= set(files):
            raise ValueError()
        if any(entry.file_size != files[name]['size'] for name, entry in entries.items()):
            raise ValueError()
        validate_names(files)
        if not {'OffworkPet.exe', 'OffworkPetUpdater.exe', 'release-info.json'} <= set(files):
            raise ValueError()
        if not any(name.startswith('_internal/') for name in files):
            raise ValueError()
    except (ValueError, KeyError, TypeError) as error:
        raise UpdateError('增量更新文件清单不正确') from error
    return files, entries, total


def local_file(root, name):
    """Reject links/junctions in every component before reusing installed bytes."""
    path = root
    for part in file_path(name).parts:
        path = path / part
        if linked(path):
            raise UpdateError('本地程序文件存在链接，无法使用增量更新')
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise UpdateError('本地程序文件缺失，无法使用增量更新')
    return path


def verify_stream(source, info, destination=None, progress=lambda size: None):
    digest, count = hashlib.sha256(), 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        count += len(chunk)
        if count > info['size']:
            raise UpdateError('增量更新文件大小不正确')
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
        progress(len(chunk))
    if count != info['size'] or digest.hexdigest() != info['sha256']:
        raise UpdateError('增量更新文件校验失败')


def verify_marker(stage, release, files):
    data = marker(stage)
    if data.get('version') != release['version'] or set(data['files']) != set(files):
        raise UpdateError('增量更新目标版本或文件清单不正确')


def stage_delta(archive, install, token, release, delta, progress=lambda value: None):
    install = validate_install(install)
    if marker(install).get('version') != delta['fromVersion']:
        raise UpdateError('本地版本与增量更新不匹配')
    stage = owned_path(install, token, 'stage')
    if stage.exists():
        raise UpdateError('更新任务目录已存在，请重新检查更新')
    check_archive(archive, delta)
    stage.mkdir()
    try:
        with zipfile.ZipFile(archive) as bundle:
            files, entries, total = delta_index(bundle, release, delta)
            if shutil.disk_usage(install.parent).free < total + 64 * 1024 * 1024:
                raise UpdateError('磁盘空间不足，请清理后重试')
            written = 0
            def advanced(size):
                nonlocal written
                written += size
                progress(int(written * 100 / max(1, total)))
            for name, info in files.items():
                target = stage.joinpath(*file_path(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = (bundle.open(entries[name]) if name in entries
                    else local_file(install, name).open('rb'))
                with source, target.open('wb') as output:
                    verify_stream(source, info, output, advanced)
            verify_marker(stage, release, files)
        return stage
    except Exception:
        remove_owned(stage, install, token, 'stage')
        raise


def verify_delta_stage(archive, stage, release, delta):
    """The independent helper rechecks the assembled release before replacing files."""
    check_archive(archive, delta)
    if linked(stage):
        raise UpdateError('更新暂存目录不正确')
    with zipfile.ZipFile(archive) as bundle:
        files, _, _ = delta_index(bundle, release, delta)
        actual = set()
        for path in Path(stage).rglob('*'):
            if linked(path):
                raise UpdateError('更新暂存目录包含链接')
            if path.is_file():
                actual.add(path.relative_to(stage).as_posix())
        if actual != set(files):
            raise UpdateError('更新暂存目录文件清单不完整')
        for name, info in files.items():
            with local_file(stage, name).open('rb') as stream:
                verify_stream(stream, info)
        verify_marker(stage, release, files)
