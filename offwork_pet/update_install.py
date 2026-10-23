"""Bounded ZIP staging and reversible portable installation swaps."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import zipfile

from .update_format import UpdateError

APP_ID = 'offwork-pet'
MAX_UNPACKED = 1536 * 1024 * 1024


def linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def relative_file(name):
    if not isinstance(name, str) or '\\' in name or ':' in name or '\x00' in name:
        raise UpdateError('更新包包含非法路径')
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in ('..', '.') or part.endswith((' ', '.'))
            or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?', part, re.I) for part in path.parts):
        raise UpdateError('更新包包含非法路径')
    return path


def marker(folder):
    try:
        data = json.loads((folder / 'release-info.json').read_text(encoding='utf-8'))
        if data['appId'] != APP_ID or not isinstance(data['files'], list) or len(data['files']) > 20000:
            raise ValueError()
        for name in data['files']:
            relative_file(name)
        return data
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise UpdateError('当前目录不是可自动更新的完整程序，请重新解压最新版') from error


def validate_install(folder):
    folder = Path(folder).resolve()
    forbidden = {Path(folder.anchor), Path.home().resolve()}
    forbidden.update((Path.home() / name).resolve() for name in ('Desktop', 'Documents', 'Downloads'))
    if folder in forbidden or not (folder / 'OffworkPet.exe').is_file():
        raise UpdateError('请把程序完整解压到独立文件夹后再更新')
    marker(folder)
    if not folder.parent.is_dir():
        raise UpdateError('当前程序目录不可更新')
    return folder


def owned_path(install, token, kind):
    if not re.fullmatch(r'[0-9a-f]{32}', token) or kind not in ('stage', 'old', 'failed'):
        raise UpdateError('更新任务标识不正确')
    install = Path(install).resolve()
    return install.parent / ('.OffworkPet-' + kind + '-' + token)


def remove_owned(folder, install, token, kind):
    expected = owned_path(install, token, kind)
    if Path(folder).absolute() != expected or expected.resolve().parent != Path(install).resolve().parent or linked(expected):
        raise UpdateError('拒绝清理更新目录之外的文件')
    if expected.exists():
        shutil.rmtree(expected)


def clean_job(job, root):
    job, root = Path(job), Path(root).resolve()
    if job.resolve().parent != root or linked(job) or not re.fullmatch(r'[0-9a-f]{32}', job.name):
        raise UpdateError('更新缓存路径不正确')
    # Try the running helper first; if it is still locked, retain the result for a later cleanup.
    for name in ('OffworkPetUpdater.exe', 'release.zip', 'plan.json', 'helper-ready', 'ready.json', 'result.json'):
        path = job / name
        if path.is_symlink():
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return False
    try:
        job.rmdir()
    except OSError:
        return False
    return True


def check_archive(archive, release):
    with Path(archive).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if Path(archive).stat().st_size != release['size'] or digest != release['sha256']:
        raise UpdateError('更新包不完整或校验失败，请重新下载')


def stage_release(archive, install, token, release, progress=lambda value: None):
    install = validate_install(install)
    stage = owned_path(install, token, 'stage')
    if stage.exists():
        raise UpdateError('更新任务目录已存在，请重新检查更新')
    stage.mkdir()
    try:
        check_archive(archive, release)
        with zipfile.ZipFile(archive) as bundle:
            files, seen, total = [], set(), 0
            for entry in bundle.infolist():
                if entry.is_dir():
                    continue
                path = relative_file(entry.filename)
                if path.parts[0] != 'OffworkPet' or len(path.parts) < 2:
                    raise UpdateError('更新包目录结构不正确')
                relative = PurePosixPath(*path.parts[1:])
                key = str(relative).casefold()
                if key in seen or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                    raise UpdateError('更新包包含重复或不支持的文件')
                seen.add(key)
                total += entry.file_size
                files.append((entry, relative))
            if total > MAX_UNPACKED or len(files) > 20000:
                raise UpdateError('更新包解压后超过限制')
            if shutil.disk_usage(install.parent).free < total + 64 * 1024 * 1024:
                raise UpdateError('磁盘空间不足，请清理后重试')
            written = 0
            for entry, relative in files:
                target = stage.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, target.open('wb') as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
                written += entry.file_size
                progress(int(written * 100 / max(1, total)))
        metadata = marker(stage)
        if metadata.get('version') != release['version'] or not (stage / 'OffworkPet.exe').is_file() or not (stage / '_internal').is_dir():
            raise UpdateError('更新包版本或运行文件不正确')
        if {str(relative) for _, relative in files} != set(metadata['files']):
            raise UpdateError('更新包文件清单不完整')
        return stage
    except Exception:
        remove_owned(stage, install, token, 'stage')
        raise


def clean_backup(backup, install, token):
    if backup != owned_path(install, token, 'old') or linked(backup):
        raise UpdateError('备份路径不正确')
    metadata = marker(backup)
    for name in metadata['files']:
        target = backup.joinpath(*relative_file(name).parts)
        if target.resolve().is_relative_to(backup.resolve()) and target.is_file() and not target.is_symlink():
            target.unlink()
    # Leave user-created files intact. Only empty directories and known release files disappear.
    directories = []
    for root, names, _ in os.walk(backup, followlinks=False):
        names[:] = [name for name in names if not linked(Path(root) / name)]
        directories.extend(Path(root) / name for name in names)
    for directory in reversed(directories):
        if directory.resolve().is_relative_to(backup.resolve()):
            try:
                directory.rmdir()
            except OSError:
                pass
    try:
        backup.rmdir()
    except OSError:
        pass


def swap_release(install, stage, token, launch, wait_ready, terminate):
    """The caller already waits for the old process to exit. All mutations stay in siblings."""
    install = validate_install(install)
    if stage != owned_path(install, token, 'stage') or linked(stage):
        raise UpdateError('更新暂存目录不正确')
    marker(stage)
    backup = owned_path(install, token, 'old')
    if backup.exists():
        raise UpdateError('更新备份目录已存在')
    os.replace(install, backup)
    process = None
    try:
        os.replace(stage, install)
        process = launch(install / 'OffworkPet.exe', True)
        if not wait_ready(process):
            raise UpdateError('新版启动未成功，已恢复旧版')
    except Exception:
        if process is not None:
            terminate(process)
        if install.exists():
            failed = owned_path(install, token, 'failed')
            os.replace(install, failed)
        os.replace(backup, install)
        launch(install / 'OffworkPet.exe', False)
        raise
    try:
        clean_backup(backup, install, token)
    except OSError:
        # A locked old DLL may be cleaned on a later start; never roll back a healthy new process.
        return backup
    return backup if backup.exists() else None
