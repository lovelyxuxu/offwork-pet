"""Per-service device credentials, protected with Windows current-user DPAPI."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from .core import data_dir


def normalize_endpoint(value):
    value = value.strip().rstrip('/')
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError:
        raise ValueError('服务端口不正确')
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError('请输入 HTTP 或 HTTPS 根地址，例如 http://服务器IP:8765')
    return parsed.geturl()


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    if sys.platform != 'win32':
        raise OSError('此客户端的设备凭证需要 Windows DPAPI')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    # CRYPTPROTECT_UI_FORBIDDEN: never display a credential prompt.
    if not api(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class IdentityStore:
    def __init__(self, endpoint, directory=None):
        self.endpoint = normalize_endpoint(endpoint)
        key = hashlib.sha256(self.endpoint.encode()).hexdigest()[:24]
        self.path = Path(directory or data_dir()) / f'identity-{key}.json'
        self.record = {'relation_id': '', 'pending_unlink': '', 'ended_seen': ''}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(loaded, dict):
                raise ValueError('设备身份文件格式不正确')
            self.record.update(loaded)
        self.token = ''
        if self.record.get('protected_token'):
            self.token = crypt(base64.b64decode(self.record['protected_token'], validate=True), decrypt=True).decode()

    def set_identity(self, data):
        device, token = data.get('device_id'), data.get('token')
        if not isinstance(device, str) or not isinstance(token, str) or not device or not token:
            raise ValueError('服务返回了无效设备身份')
        self.token = token
        self.record.update(device_id=device, protected_token=base64.b64encode(crypt(token.encode())).decode())
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.record, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
