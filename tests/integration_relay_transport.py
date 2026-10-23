"""Exercise the real server entry point and proxy with isolated test identities.

The default run starts a local server. Deployment tooling may call smoke() with
the product endpoint and must clean up only identities supplied to on_register.
Tokens stay in memory and are never printed or written to disk.
"""
from contextlib import ExitStack
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import Request, urlopen
import uuid

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
CONNECT_OPTIONS = {'proxy': None} if 'proxy' in inspect.signature(connect).parameters else {}


def smoke(endpoint, on_register=lambda identity: None):
    def register(protocol):
        request = Request(endpoint + f'/v{protocol}/devices', data=b'', method='POST')
        with urlopen(request, timeout=10) as response:
            identity = json.load(response)
        on_register(identity['device_id'])
        return identity

    def receive(ws, kind, request_id=None):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            message = json.loads(ws.recv(timeout=max(.1, deadline-time.monotonic())))
            if message['type'] == kind and (request_id is None or message.get('request_id') == request_id):
                return message
        raise AssertionError('Expected relay response: ' + kind)

    def send(ws, kind, **data):
        rid = uuid.uuid4().hex
        ws.send(json.dumps(dict(type=kind, request_id=rid, **data), ensure_ascii=False))
        return rid

    def command(ws, kind, **data):
        result = receive(ws, 'result', send(ws, kind, **data))
        assert result['ok'], 'Relay command failed: ' + result.get('code', '')
        return result['data']

    def caps(package, revision, large=False):
        entries = [{'id': 'greet', 'label': '打招呼', 'order': 0}]
        if large:
            entries = [{'id': f'entry{i:02d}' + 'x'*89, 'label': '舒'*64, 'order': i} for i in range(64)]
        return dict(packageId=package, packageVersion='1.1.0', displayName=package,
                    digest='a'*64, revision=revision, interactions=entries)

    identities = [register(2), register(1)]
    wsroot = endpoint.replace('http://', 'ws://').replace('https://', 'wss://')
    for protocol in (2, 1):
        with ExitStack() as stack:
            clients = [stack.enter_context(connect(wsroot + f'/v{protocol}/connect',
                       additional_headers={'Authorization': 'Bearer ' + identity['token']},
                       open_timeout=10, close_timeout=3, **CONNECT_OPTIONS)) for identity in identities]
            a, b = clients
            for ws in clients:
                receive(ws, 'state')
            if protocol == 2:
                # A valid 64-entry Unicode capability snapshot exceeds the old
                # 16 KiB uvicorn transport cap, while staying below 32 KiB.
                large = caps('codex-lumei', 'large-1', True)
                raw = json.dumps(dict(type='profile', request_id='large', paused=False, visible=True,
                                      capabilities=large), ensure_ascii=False).encode()
                assert 16384 < len(raw) < 32768
                command(b, 'profile', capabilities=large, paused=False, visible=True)
                profiles = [dict(capabilities=caps('noah-lulu', 'noah-1')),
                            dict(capabilities=caps('codex-lumei', 'lumei-1'))]
            else:
                profiles = [dict(character='lulu'), dict(character='lumei')]
            for ws, profile in zip(clients, profiles):
                command(ws, 'profile', **profile, paused=False, visible=True)
            code = command(a, 'invite')['code']
            relation = command(b, 'redeem', code=code)['relation']['id']
            state = command(a, 'profile', **profiles[0], paused=False, visible=True)
            if protocol == 2:
                assert state['relation']['capabilities']['packageId'] == 'codex-lumei'
            aid = uuid.uuid4().hex
            action = (dict(interactionId='greet', capabilityRevision='lumei-1') if protocol == 2
                      else dict(action='pinch', count=1))
            rid = send(a, 'action', relation_id=relation, action_id=aid, **action)
            event = receive(b, 'action')
            assert event['action_id'] == aid
            if protocol == 2:
                assert event['interactionId'] == 'greet'
            b.send(json.dumps(dict(type='ack', relation_id=relation, action_id=aid,
                                  accepted=True, status='started')))
            assert receive(a, 'result', rid)['ok']
            command(b, 'profile', **profiles[1], paused=True, visible=True)
            rid = send(a, 'action', relation_id=relation, action_id=uuid.uuid4().hex, **action)
            assert receive(a, 'result', rid)['code'] == 'paused'
            command(a, 'unlink', relation_id=relation)
        print(f'PASS v{protocol}: registration, authenticated WebSocket, pairing, action, ack, pause, unlink', flush=True)
    # Reconnect with the same credentials, checking identities survived.
    with connect(wsroot + '/v2/connect', additional_headers={'Authorization': 'Bearer ' + identities[0]['token']},
                 open_timeout=10, close_timeout=3, **CONNECT_OPTIONS) as ws:
        assert receive(ws, 'state')['relation'] is None
        command(ws, 'profile', capabilities=caps('noah-lulu', 'reconnect'), paused=False, visible=True)
    print('PASS reconnect with existing identity; 16-32 KiB capability transport', flush=True)


def local_main():
    with TemporaryDirectory() as temporary, socket.socket() as free:
        free.bind(('127.0.0.1', 0))
        port = free.getsockname()[1]
        free.close()
        endpoint = f'http://127.0.0.1:{port}'
        with open(Path(temporary)/'server.log', 'w') as log:
            process = subprocess.Popen([sys.executable, '-m', 'server', '--port', str(port)], cwd=ROOT,
                       env=dict(os.environ, OFFWORK_DB=str(Path(temporary)/'db.sqlite3')),
                       stdout=log, stderr=subprocess.STDOUT)
            try:
                for attempt in range(100):
                    try:
                        with urlopen(endpoint+'/health', timeout=.2) as response:
                            assert json.load(response)['protocol'] == 2
                        break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Local server failed to start')
                smoke(endpoint)
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    local_main()
