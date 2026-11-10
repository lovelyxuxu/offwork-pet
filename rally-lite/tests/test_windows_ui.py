import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

import pytest
from PIL import ImageGrab, Image
from websockets.sync.client import connect
from test_interop import relay, recv_until, ROOT

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows desktop test')
user32 = ctypes.windll.user32
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetDlgItem.restype = wintypes.HWND
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = wintypes.LPARAM
user32.PostMessageW.argtypes = user32.SendMessageW.argtypes
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowEnabled.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND


def until(predicate, message, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        threading.Event().wait(0.03)
    raise AssertionError(message)


def text(handle):
    result = ctypes.create_unicode_buffer(2048)
    user32.SendMessageW(handle, 0x000D, len(result), ctypes.addressof(result))
    return result.value


def windows(class_name):
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(handle, _):
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(handle, name, len(name))
        if name.value == class_name:
            found.append(handle)
        return True

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows(visit, 0)
    return found


def click(main, identifier):
    user32.SendMessageW(user32.GetDlgItem(main, identifier), 0x00F5, 0, 0)


def fill(handle, value):
    buffer = ctypes.create_unicode_buffer(value)
    user32.SendMessageW(handle, 0x000C, 0, ctypes.addressof(buffer))
    assert text(handle) == value


def test_global_hotkey_and_passive_edge_on_real_desktop(relay, tmp_path):
    assert not windows('RallyLiteMain'), 'Close the standalone test client before running desktop validation'
    home = tmp_path / 'ui'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'nickname': '独立桌面', 'hotkeys': ['Ctrl+Alt+F9', 'Ctrl+Alt+F10', 'Ctrl+Alt+F11']}), encoding='utf-8')
    identity = json.load(urlopen(Request(relay + '/v2/devices', data=b'{}', headers={'Content-Type': 'application/json'})))
    process = None
    dummy = None
    try:
        with connect(relay.replace('http://', 'ws://') + '/v2/connect', additional_headers={'Authorization': 'Bearer ' + identity['token']}) as peer:
            peer.send(json.dumps({'type': 'profile', 'request_id': 'p', 'paused': False, 'visible': True, 'capabilities': None}))
            recv_until(peer, lambda m: m.get('request_id') == 'p')
            peer.send(json.dumps({'type': 'rally_create', 'request_id': 'c', 'name': '桌面验收集会', 'member_name': '桌宠'}))
            group = recv_until(peer, lambda m: m.get('request_id') == 'c')['data']['rally']
            process = subprocess.Popen([os.environ.get('RALLY_LITE_TEST_EXE', str(ROOT / 'build/rally-lite/RallyLite.exe'))], env=dict(os.environ, RALLY_LITE_HOME=str(home), RALLY_LITE_ENDPOINT=relay))
            main = until(lambda: user32.FindWindowW('RallyLiteMain', None), 'Main window did not open')
            until(lambda: user32.IsWindowEnabled(user32.GetDlgItem(main, 100)), 'Client did not connect')
            fill(user32.GetDlgItem(main, 11), group['code'])
            click(main, 100)
            until(lambda: user32.IsWindowEnabled(user32.GetDlgItem(main, 200)), 'Client did not join')
            fill(user32.GetDlgItem(main, 300), 'Ctrl+Alt+F10')
            click(main, 102)
            assert json.loads((home / 'settings.json').read_text(encoding='utf-8'))['hotkeys'][0] == 'Ctrl+Alt+F9', 'Duplicate keys must not be persisted'
            fill(user32.GetDlgItem(main, 300), 'Ctrl+Alt+F9')
            click(main, 102)
            rect = wintypes.RECT()
            user32.GetWindowRect(main, ctypes.byref(rect))
            output = ROOT / 'outputs'
            output.mkdir(exist_ok=True)
            user32.RedrawWindow(main, None, None, 0x0185)
            threading.Event().wait(0.35)
            ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).save(output / 'rally-lite-main.png')
            user32.PostMessageW(main, 0x0010, 0, 0)
            until(lambda: not user32.IsWindowVisible(main), 'Closing settings should hide it')
            width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
            dummy = user32.CreateWindowExW(0, 'STATIC', 'RallyLite desktop validation', 0x90000006, 0, 0, width, height, None, None, None, None)
            assert dummy
            user32.RedrawWindow(dummy, None, None, 0x0185)
            user32.UpdateWindow(dummy)
            user32.SetForegroundWindow(dummy)
            until(lambda: user32.GetForegroundWindow() == dummy, 'Test foreground window did not activate')
            try:
                for key in (0x11, 0x12, 0x7A):
                    user32.keybd_event(key, 0, 0, 0)
            finally:
                for key in (0x7A, 0x12, 0x11):
                    user32.keybd_event(key, 0, 2, 0)
            alert = recv_until(peer, lambda m: m.get('type') == 'rally_alert')
            assert alert['level'] == 'danger' and alert['from'] == '独立桌面'
            peer.send(json.dumps({'type': 'rally_alert', 'request_id': 'a', 'alert_id': 'desktop-edge', 'level': 'warning', 'label': '开会啦', 'color': '#7964CB'}))
            layers = until(lambda: (found if len(found := windows('RallyLiteEdge')) >= 5 else None), 'No complete set of edge overlays appeared')
            for layer in layers:
                style = user32.GetWindowLongW(layer, -20)
                assert style & 0x20 and style & 0x08000000, 'Overlay must be click-through and nonactivating'
            assert user32.GetForegroundWindow() == dummy, 'Overlay stole focus'
            until(lambda: any(user32.IsWindowVisible(layer) for layer in windows('RallyLiteEdge')), 'Edge did not become visible')
            threading.Event().wait(0.6)
            screen = ImageGrab.grab()
            sample = Image.new('RGB', (640, 100), 'white')
            sample.paste(screen.crop((0, 0, 640, 32)), (0, 0))
            sample.paste(screen.crop((width - 340, height - 100, width, height - 32)), (300, 32))
            sample.save(output / 'rally-lite-edge.png')
            until(lambda: not windows('RallyLiteEdge'), 'Edge did not disappear after five seconds', timeout=7)
            assert process.poll() is None and not user32.IsWindowVisible(main)
            user32.PostMessageW(main, 0x0111, 103, 0)
            process.wait(timeout=15)
            assert process.returncode == 0
    finally:
        if dummy:
            user32.DestroyWindow(dummy)
        if process and process.poll() is None:
            main = user32.FindWindowW('RallyLiteMain', None)
            if main:
                user32.PostMessageW(main, 0x0111, 103, 0)
            process.wait(timeout=15)
