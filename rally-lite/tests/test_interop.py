import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

import pytest
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
EXE = Path(os.environ.get('RALLY_PROBE', ROOT / 'build/rally-lite/rally_probe.exe'))


class Probe:
    def __init__(self, endpoint, home):
        self.events = queue.Queue()
        self.process = subprocess.Popen([str(EXE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                        env=dict(os.environ, RALLY_LITE_ENDPOINT=endpoint, RALLY_LITE_HOME=str(home)))
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        for line in self.process.stdout:
            self.events.put(json.loads(line))

    def send(self, **message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def until(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        history = []
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            history.append(event)
            if predicate(event):
                return event
        raise AssertionError(f'Native event missing; observed={history}, process={self.process.poll()}')

    def close(self):
        if self.process.poll() is None:
            self.send(type='quit')
            self.process.wait(timeout=15)
        assert self.process.returncode == 0, self.process.stderr.read()
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.stderr.close()
        self.thread.join(timeout=2)


def recv_until(ws, predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = json.loads(ws.recv(timeout=max(0.1, deadline - time.monotonic())))
        if predicate(value):
            return value
    raise AssertionError('Peer event missing')


@pytest.fixture
def relay(tmp_path):
    assert EXE.exists(), 'Native client probe must be built before interoperability tests'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    endpoint = f'http://127.0.0.1:{port}'
    log = (tmp_path / 'server.log').open('w')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server.app:create_app', '--factory', '--host', '127.0.0.1', '--port', str(port)],
                               cwd=ROOT, env=dict(os.environ, OFFWORK_DB=str(tmp_path / 'relay.sqlite3')),
                               stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                with urlopen(endpoint + '/health', timeout=0.3):
                    break
            except OSError:
                if process.poll() is not None:
                    pytest.fail('Temporary relay exited: ' + (tmp_path / 'server.log').read_text())
                threading.Event().wait(0.05)
        else:
            pytest.fail('Temporary relay did not start')
        yield endpoint
    finally:
        process.terminate()
        process.wait(timeout=8)
        log.close()


def test_native_joins_existing_rally_and_exchanges_signals(relay, tmp_path):
    identity = json.load(urlopen(Request(relay + '/v2/devices', data=b'{}', headers={'Content-Type': 'application/json'})))
    with connect(relay.replace('http://', 'ws://') + '/v2/connect', additional_headers={'Authorization': 'Bearer ' + identity['token']}) as peer:
        peer.send(json.dumps({'type': 'profile', 'request_id': 'profile', 'paused': False, 'visible': True, 'capabilities': None}))
        recv_until(peer, lambda m: m.get('request_id') == 'profile')
        peer.send(json.dumps({'type': 'rally_create', 'request_id': 'create', 'name': '现有桌宠集会', 'member_name': '桌宠'}))
        rally = recv_until(peer, lambda m: m.get('request_id') == 'create')['data']['rally']
        native = Probe(relay, tmp_path / 'native')
        try:
            native.until(lambda m: m.get('ready'))
            native.send(type='join', code='BAD', name='轻量客户端')
            native.until(lambda m: '8 位集会码' in m.get('status', ''))
            native.send(type='join', code=rally['code'].lower(), name='轻量客户端')
            native.until(lambda m: m.get('rally_id') == rally['id'])
            for index, level in enumerate(('safe', 'warning', 'danger')):
                native.send(type='alert', index=index)
                received = recv_until(peer, lambda m: m.get('type') == 'rally_alert')
                assert received['level'] == level
                assert received['from'] == '轻量客户端'
                assert received['rally_id'] == rally['id']
                peer.send(json.dumps({'type': 'rally_alert', 'request_id': f'alert-{index}', 'alert_id': f'pet-{index}', 'level': level,
                                      'label': '开会啦', 'color': '#7964CB'}))
                event = native.until(lambda m: m.get('type') == 'flash' and m.get('from') == '桌宠')
                assert event['level'] == level and event['label'] == '开会啦' and event['color'] == '#7964CB'
            native.close()
            native = Probe(relay, tmp_path / 'native')
            native.until(lambda m: m.get('ready') and m.get('rally_id') == rally['id'])
            identity_file = (tmp_path / 'native' / 'identity.json').read_text(encoding='utf-8')
            assert 'protected_token' in identity_file and '"token"' not in identity_file
            native.send(type='leave')
            native.until(lambda m: m.get('ready') and not m.get('rally_id'))
        finally:
            native.close()


def test_interoperates_with_actual_pet_client(relay, tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('OFFWORK_HOME', str(tmp_path / 'pet'))
    sys.path.insert(0, str(ROOT))
    from PySide6.QtWidgets import QApplication
    from offwork_pet.buddy import BuddyClient
    from offwork_pet.alert import AlertController
    from offwork_pet.core import Config

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    config = Config(rally_name='原版桌宠', alert_seconds=2)
    pet = BuddyClient(config, endpoint=relay)
    alerts = AlertController(config, pet, enable_hotkeys=False)
    native = None

    def until(predicate):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            threading.Event().wait(0.01)
        raise AssertionError('Existing pet did not reach expected state: ' + pet.connection_text)

    try:
        pet.start()
        until(lambda: pet.ready)
        pet.rally_create('真实桌宠互通')
        until(lambda: pet.rally)
        native = Probe(relay, tmp_path / 'native-with-pet')
        native.until(lambda m: m.get('ready'))
        native.send(type='join', code=pet.rally['code'], name='独立工具')
        native.until(lambda m: m.get('rally_id') == pet.rally['id'])
        native.send(type='alert', index=2)
        until(lambda: alerts.overlay.isVisible() and alerts.overlay.source == '独立工具')
        assert alerts.overlay.level == 'danger'
        alerts.trigger('warning')
        until(lambda: not pet.callbacks)
        event = native.until(lambda m: m.get('type') == 'flash' and m.get('from') == '原版桌宠')
        assert event['level'] == 'warning'
    finally:
        if native:
            native.close()
        alerts.shutdown()
        pet.stop()
        pet.deadlines.stop()
        app.processEvents()
