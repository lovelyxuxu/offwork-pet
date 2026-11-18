import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PySide6.QtWidgets import QApplication

from offwork_pet.buddy import BuddyClient
from offwork_pet.core import Config, load_config, save_config
from server.app import create_app
from server.hosting import Hosting
from server.manage import import_sqlite
from server.store import Store


class HostingTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'OFFWORK_REGISTRATION': 'invite', 'OFFWORK_CHAT_DAYS': '2',
                                          'OFFWORK_ADMIN_USER': 'test', 'OFFWORK_ADMIN_PASSWORD': 'test-secret'})
        self.env.start()
        self.context = TestClient(create_app(':memory:'))
        self.client = self.context.__enter__()
        self.relay = self.client.app.state.relay

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.env.stop()

    def login(self):
        self.assertEqual(self.client.post('/admin/login', json={'username': 'test', 'password': 'test-secret'}).status_code, 200)

    def invite(self, **data):
        self.login()
        return self.client.post('/admin/api/access-invites', json=data).json()

    def register(self, code):
        return self.client.post('/v2/devices', json={'access_code': code})

    def test_invitation_is_required_consumed_and_not_exposed(self):
        self.assertEqual(self.register('').status_code, 403)
        code = self.invite()['code']
        self.assertIn('token', self.register(code).json())
        self.assertEqual(self.register(code).status_code, 403)
        self.assertNotIn(code, self.client.get('/admin/api/hosting').text)

    def test_expired_and_revoked_codes_cannot_enroll(self):
        invite = self.invite()
        self.relay.store.db.execute('UPDATE access_invites SET expires=0')
        self.assertEqual(self.register(invite['code']).status_code, 403)
        invite = self.invite()
        self.client.delete('/admin/api/access-invites/'+invite['id'])
        self.assertEqual(self.register(invite['code']).status_code, 403)

    def test_policy_and_history_require_authorization(self):
        self.assertEqual(self.client.get('/admin/api/hosting').status_code, 401)
        self.assertEqual(self.client.post('/admin/api/access-invites', json={}).status_code, 401)
        self.assertEqual(self.client.get('/v2/chat/history').status_code, 401)
        self.login()
        response = self.client.post('/admin/api/hosting', json={'name': '测试', 'registration': 'closed', 'chat_days': 1},
                                    headers={'origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)

    def test_closed_registration_still_allows_existing_device(self):
        identity = self.register(self.invite()['code']).json()
        self.client.post('/admin/api/hosting', json={'name': '自己的服务', 'registration': 'closed', 'chat_days': 0})
        self.assertEqual(self.register('').status_code, 403)
        self.assertEqual(self.relay.store.authenticate(identity['token']), identity['device_id'])

    def test_disable_disconnects_and_denies_future_auth(self):
        identity = self.register(self.invite()['code']).json()
        with self.client.websocket_connect('/v2/connect', headers={'authorization': 'Bearer '+identity['token']}) as ws:
            ws.receive_json()
            result = self.client.post('/admin/api/hosting/devices/'+identity['device_id']+'/disabled', json={'disabled': True})
            self.assertEqual(result.status_code, 200)
            for _ in range(5):
                if ws.receive()['type'] == 'websocket.close':
                    break
            else:
                self.fail('disabled socket was not closed')
        self.assertIsNone(self.relay.store.authenticate(identity['token']))

    def test_history_is_scoped_to_current_rally_and_retention(self):
        a = self.register(self.invite()['code']).json()
        b = self.register(self.invite()['code']).json()
        rally = self.relay.store.rally_create(a['device_id'], '一号')
        self.relay.hosting.save_chat(rally['id'], a['device_id'], '甲', 'hello')
        auth = lambda device: {'authorization': 'Bearer '+device['token']}
        self.assertEqual(self.client.get('/v2/chat/history', headers=auth(b)).status_code, 403)
        result = self.client.get('/v2/chat/history', headers=auth(a)).json()
        self.assertEqual(result['items'][0]['body'], 'hello')
        self.relay.store.rally_create(b['device_id'], '二号')
        self.assertEqual(self.client.get('/v2/chat/history', headers=auth(b)).json()['items'], [])
        self.relay.store.db.execute('UPDATE chat_messages SET created=0')
        self.assertEqual(self.client.get('/v2/chat/history', headers=auth(a)).json()['items'], [])
        self.relay.hosting.save_chat(rally['id'], a['device_id'], '甲', 'delete')
        self.client.post('/admin/api/hosting', json={'name': '测试', 'registration': 'invite', 'chat_days': 0})
        self.assertEqual(self.relay.store.db.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0], 0)

    def test_instances_do_not_share_tokens(self):
        identity = self.register(self.invite()['code']).json()
        with TestClient(create_app(':memory:')) as second:
            self.assertNotEqual(second.get('/api/server/info').json()['instance_id'], self.client.get('/api/server/info').json()['instance_id'])
            self.assertEqual(second.get('/v2/chat/history', headers={'authorization': 'Bearer '+identity['token']}).status_code, 401)

    def test_malformed_registration_returns_400(self):
        self.assertEqual(self.client.post('/v2/devices', content='bad').status_code, 400)
        self.assertEqual(self.client.post('/v2/devices', json=[]).status_code, 400)


class MigrationTests(unittest.TestCase):
    def test_reopen_preserves_identity_and_instance_policy(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'db.sqlite3'
            store = Store(path)
            identity = store.register()
            instance = Hosting(store).public()
            store.close()
            store = Store(path)
            self.assertEqual(store.authenticate(identity['token']), identity['device_id'])
            self.assertEqual(Hosting(store).public(), instance)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0], 3)
            store.close()

    def test_legacy_import_preserves_token_and_refuses_overwrite(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'legacy.sqlite3'
            original = Store(path)
            identity = original.register()
            original.close()
            before = path.read_bytes()
            target = Store(':memory:')
            import_sqlite(path, target)
            self.assertEqual(target.authenticate(identity['token']), identity['device_id'])
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(ValueError):
                import_sqlite(path, target)
            target.close()


class ServerChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_unconfigured_client_does_not_connect_to_product_service(self):
        config = Config(network_enabled=True)
        client = BuddyClient(config)
        client.start()
        self.assertFalse(client.enabled)
        self.assertEqual(client.endpoint, '')
        self.assertIsNone(client.preflight)
        client.deadlines.stop()

    def test_switch_discards_runtime_state_and_saves_only_address(self):
        config = Config(server_url='https://one.example')
        client = BuddyClient(config)
        client.relation = {'id': 'old'}
        client.rally = {'id': 'old'}
        client.received['old'] = 1
        client.change_server('https://two.example/', 'one-use-secret')
        self.assertIsNone(client.relation)
        self.assertIsNone(client.identity)
        self.assertIsNone(client.rally)
        self.assertFalse(client.received)
        with TemporaryDirectory() as temp:
            path = Path(temp)/'config.json'
            save_config(config, path)
            self.assertNotIn('one-use-secret', path.read_text(encoding='utf-8'))
            self.assertEqual(load_config(path)[0].server_url, 'https://two.example')
        client.deadlines.stop()
