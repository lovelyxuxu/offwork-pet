"""Out-of-process Windows updater; the running application never deletes itself."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .core import data_dir
from .update_format import UpdateError, select_delta, verify_manifest
from .update_install import check_archive, owned_path, swap_release, validate_install, remove_owned
from .update_delta import verify_delta_stage


def wait_old_process(pid, expected, timeout=35, before_wait=lambda: None):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # Already exited.
            before_wait()
            return
        raise UpdateError('无法等待旧程序退出，请手动退出后重试')
    try:
        size = wintypes.DWORD(32768)
        name = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)) or Path(name.value).resolve() != expected.resolve():
            raise UpdateError('更新进程与当前程序不匹配')
        before_wait()
        if kernel.WaitForSingleObject(handle, timeout * 1000) != 0:
            raise UpdateError('旧程序尚未退出，更新已取消')
    finally:
        kernel.CloseHandle(handle)


def run_plan(path):
    path = Path(path).resolve()
    token = path.parent.name
    if not re.fullmatch(r'[0-9a-f]{32}', token) or path != (data_dir() / 'updates' / token / 'plan.json').resolve():
        raise UpdateError('更新任务路径不正确')
    job = path.parent
    plan = json.loads(path.read_text(encoding='utf-8'))
    release = verify_manifest(plan['manifest'])
    install = validate_install(plan['install'])
    archive, ready = job / 'release.zip', job / 'ready.json'
    stage = owned_path(install, token, 'stage')
    if 'deltaFrom' in plan:
        delta = select_delta(release, plan['deltaFrom'])
        if delta is None:
            raise UpdateError('增量更新任务与签名信息不匹配')
        verify_delta_stage(archive, stage, release, delta)
    else:
        check_archive(archive, release)
    if ready.exists():
        ready.unlink()
    pid = plan['pid']
    if type(pid) is not int or pid <= 0:
        raise UpdateError('旧程序标识不正确')
    # The parent waits for this acknowledgement before exiting, so helper launch failure is harmless.
    wait_old_process(pid, install / 'OffworkPet.exe', before_wait=lambda:
        (job / 'helper-ready').write_text('ready', encoding='ascii'))

    children = []
    def launch(executable, upgraded):
        arguments = [str(executable)]
        if upgraded:
            arguments += ['--update-ready', token]
        child = subprocess.Popen(arguments, cwd=executable.parent,
            env=dict(os.environ, OFFWORK_HOME=str(data_dir())), creationflags=subprocess.CREATE_NO_WINDOW)
        children.append(child)
        return child

    def wait_ready(process):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline and process.poll() is None:
            if ready.exists():
                try:
                    if json.loads(ready.read_text(encoding='utf-8')).get('version') == release['version']:
                        return True
                except (OSError, ValueError):
                    pass
            time.sleep(.15)
        return False

    def terminate(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)

    try:
        leftover = swap_release(install, stage, token, launch, wait_ready, terminate)
    except Exception as error:
        (job / 'result.json').write_text(json.dumps({'ok': False, 'message': str(error)}, ensure_ascii=False), encoding='utf-8')
        raise
    (job / 'result.json').write_text(json.dumps({'ok': True, 'version': release['version'],
        'backup': str(leftover) if leftover else '', 'pid': children[-1].pid}), encoding='utf-8')
    archive.unlink(missing_ok=True)
    failed = owned_path(install, token, 'failed')
    if failed.exists():
        remove_owned(failed, install, token, 'failed')


def main():
    try:
        if len(sys.argv) != 2 or sys.platform != 'win32':
            raise UpdateError('请从下班搭子内启动更新')
        run_plan(sys.argv[1])
    except Exception as error:
        if sys.platform == 'win32':
            ctypes.windll.user32.MessageBoxW(None, '更新未完成。' + str(error), '下班搭子 · 更新', 0x10)
        return 1
    return 0
