"""Opt-in Qt + two real Compose/PostgreSQL servers. Loopback endpoints only."""
import argparse
from contextlib import closing
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from offwork_pet.buddy import BuddyClient
from offwork_pet.core import Config
from offwork_pet.credentials import IdentityStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--first', default='http://127.0.0.1:8765')
    parser.add_argument('--second', default='http://127.0.0.1:8767')
    args = parser.parse_args()
    for url in (args.first, args.second):
        if urlsplit(url).hostname not in ('127.0.0.1', 'localhost'):
            raise ValueError('This verification only operates on loopback test instances')
    env = dict(line.split('=', 1) for line in (Path(__file__).resolve().parents[1]/'.env').read_text().splitlines() if '=' in line)
    app = QApplication.instance() or QApplication([])

    def until(condition):
        deadline = monotonic()+15
        while not condition() and monotonic() < deadline:
            app.processEvents()
            sleep(.005)
        assert condition(), 'Qt operation did not complete'

    def administrator(endpoint):
        client = httpx.Client(base_url=endpoint, timeout=10)
        client.post('/admin/login', json={'username': env['ADMIN_USER'], 'password': env['ADMIN_PASSWORD']}).raise_for_status()
        return client

    def code(admin):
        result = admin.post('/admin/api/access-invites', json={'uses': 1, 'hours': 1})
        result.raise_for_status()
        return result.json()['code']

    with TemporaryDirectory() as temp, closing(administrator(args.first)) as first, closing(administrator(args.second)) as second:
        assert first.get('/api/server/info').json()['instance_id'] != second.get('/api/server/info').json()['instance_id']
        clients = []
        try:
            for index in range(2):
                directory = Path(temp)/str(index)
                with patch('offwork_pet.buddy.IdentityStore', side_effect=lambda endpoint: IdentityStore(endpoint, directory)):
                    client = BuddyClient(Config(server_url=args.first), persist=False)
                    client.access_code = code(first)
                    client.start()
                    until(lambda: client.ready)
                clients.append(client)
            a, b = clients
            a.rally_create('selfhost-verification')
            until(lambda: bool(a.rally))
            b.rally_join(a.rally['code'])
            until(lambda: bool(b.rally))
            received, result = [], []
            b.chat_event.connect(received.append)
            a.rally_chat('selfhost message', result.append)
            until(lambda: bool(received) and bool(result))
            assert result[0]['ok'] and received[0]['text'] == 'selfhost message'
            original_token = a.identity.token
            headers = {'authorization': 'Bearer '+original_token}
            assert first.get('/v2/chat/history', headers=headers).json()['items'][0]['body'] == 'selfhost message'
            assert second.get('/v2/chat/history', headers=headers).status_code == 401
            a.change_server(args.second, code(second))
            assert not a.rally and not a.relation and not a.ready
            with patch('offwork_pet.buddy.IdentityStore', side_effect=lambda endpoint: IdentityStore(endpoint, Path(temp)/'0')):
                a.start()
                until(lambda: a.ready)
            assert a.identity.token != original_token
            assert first.get('/v2/chat/history', headers={'authorization': 'Bearer '+a.identity.token}).status_code == 401
            second.post('/admin/api/hosting/devices/'+a.identity.record['device_id']+'/disabled', json={'disabled': True}).raise_for_status()
            until(lambda: not a.enabled)
            assert second.get('/v2/chat/history', headers={'authorization': 'Bearer '+a.identity.token}).status_code == 401
            print('PASS: two PostgreSQL instances, real Qt enrollment/WebSocket/chat/history, server switch, token isolation and device ban')
        finally:
            for client in clients:
                client.stop()
                client.deadlines.stop()
                client.deleteLater()
            app.processEvents()


if __name__ == '__main__':
    main()
