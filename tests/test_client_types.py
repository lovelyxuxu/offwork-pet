import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from server.app import create_app
from server.store import Store


class ClientTypeTests(unittest.TestCase):
    def test_old_database_is_migrated_idempotently_and_keeps_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE devices(id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,character TEXT NOT NULL DEFAULT 'lulu',relation_id TEXT,ended_id TEXT NOT NULL DEFAULT '')")
                db.execute("INSERT INTO devices VALUES('old','hash','cat',NULL,'')")
            db.close()
            for _ in range(2):
                store = Store(path)
                self.assertEqual(store.device('old')['client_type'], 'desktop')
                self.assertEqual(store.device('old')['character'], 'cat')
                self.assertEqual(store.device('old')['token_hash'], 'hash')
                store.close()

    def test_registration_and_profile_types_persist_and_admin_reports_offline(self):
        with patch.dict(os.environ, OFFWORK_ADMIN_USER='admin', OFFWORK_ADMIN_PASSWORD='local-test-only'):
            with TestClient(create_app(':memory:')) as client:
                desktop = client.post('/v2/devices').json()
                mobile = client.post('/v2/devices', headers={'X-Offwork-Client-Type': 'mobile'}).json()
                self.assertEqual(client.post('/v2/devices', headers={'X-Offwork-Client-Type': 'invalid'}).status_code, 400)
                # Updating an existing identity works even when no header was sent at registration.
                with client.websocket_connect('/v2/connect', headers={'Authorization': 'Bearer ' + desktop['token']}) as ws:
                    ws.send_json({'type': 'profile', 'request_id': 'profile', 'paused': False, 'visible': True,
                                  'capabilities': None, 'client_type': 'mobile'})
                    while True:
                        msg = ws.receive_json()
                        if msg.get('request_id') == 'profile': break
                    self.assertTrue(msg['ok'])
                    ws.send_json({'type': 'profile', 'request_id': 'invalid', 'paused': False, 'visible': True,
                                  'capabilities': None, 'client_type': 'phone'})
                    while True:
                        msg = ws.receive_json()
                        if msg.get('request_id') == 'invalid': break
                    self.assertFalse(msg['ok'])
                client.post('/admin/login', json={'username': 'admin', 'password': 'local-test-only'})
                devices = client.get('/admin/api/devices').json()
                self.assertEqual(len(devices), 2)
                self.assertTrue(all(row['client_type'] == 'mobile' for row in devices))
                self.assertTrue(all(row['status'] == 'offline' for row in devices))
                response = client.get('/admin/api/devices').text
                self.assertNotIn(mobile['token'], response)
