import os
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.app import client_ip, create_app
from server.store import DomainError, Store


class AdminStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.a, self.b = [self.store.register()['device_id'] for _ in range(2)]

    def tearDown(self):
        self.store.close()

    def test_counts_and_rallies(self):
        self.assertEqual(self.store.admin_counts(), {'devices': 2, 'relations': 0, 'rallies': 0})
        self.assertEqual(self.store.all_rallies(), [])
        rally = self.store.rally_create(self.a, '测试集会', '小明')
        self.store.rally_join(self.b, rally['code'], '小红')
        self.store.redeem(self.b, self.store.invite(self.a)['code'])
        self.assertEqual(self.store.admin_counts(), {'devices': 2, 'relations': 1, 'rallies': 1})
        rallies = self.store.all_rallies()
        self.assertEqual(len(rallies), 1)
        self.assertEqual(rallies[0]['code'], rally['code'])
        self.assertEqual(rallies[0]['name'], '测试集会')
        self.assertEqual([m['name'] for m in rallies[0]['members']], ['小明', '小红'])
        self.assertEqual(rallies[0]['member_limit'], 12)

    def test_admin_devices_dissolve_and_limit(self):
        self.store.character(self.a, 'lulu')
        rally = self.store.rally_create(self.a, '测试集会', '小明')
        self.store.rally_join(self.b, rally['code'], '小红')
        devices = self.store.admin_devices()
        self.assertEqual(len(devices), 2)
        self.assertTrue(any(d['id'] == self.a for d in devices))
        self.assertNotIn('token_hash', devices[0])
        updated = self.store.admin_set_rally_limit(rally['code'], 5)
        self.assertEqual(updated['member_limit'], 5)
        with self.assertRaises(DomainError):
            self.store.admin_set_rally_limit(rally['code'], 1)
        with self.assertRaises(DomainError):
            self.store.admin_set_rally_limit('NOPE', 5)
        self.store.admin_dissolve_rally(rally['code'])
        self.assertIsNone(self.store.rally(self.a))
        with self.assertRaises(DomainError):
            self.store.admin_dissolve_rally(rally['code'])


class SessionMetadataTests(unittest.TestCase):
    def test_client_ip_trust_boundary(self):
        def socket(host, headers):
            return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)
        self.assertEqual(client_ip(socket('10.0.0.5', {'x-real-ip': '9.9.9.9'})), '10.0.0.5')
        self.assertEqual(client_ip(socket('127.0.0.1', {'x-real-ip': '9.9.9.9'})), '9.9.9.9')
        self.assertEqual(client_ip(socket('127.0.0.1', {'x-forwarded-for': '9.9.9.9, 8.8.8.8'})), '9.9.9.9')
        self.assertEqual(client_ip(socket('::1', {})), '::1')

    def test_session_records_ip_and_connect_time(self):
        with TestClient(create_app(':memory:')) as client:
            identity = client.post('/v2/devices').json()
            with client.websocket_connect('/v2/connect', headers={'Authorization': 'Bearer ' + identity['token']}) as socket:
                socket.receive_json()
                session = client.app.state.relay.sessions[identity['device_id']]
                self.assertTrue(session.ip)
                self.assertGreater(session.connected, 0)


class AdminDisabledTests(unittest.TestCase):
    def test_routes_hidden_without_credentials(self):
        with patch.dict(os.environ):
            os.environ.pop('OFFWORK_ADMIN_USER', None)
            os.environ.pop('OFFWORK_ADMIN_PASSWORD', None)
            with TestClient(create_app(':memory:')) as client:
                for method, path in [('GET', '/admin'), ('POST', '/admin/login'),
                                     ('POST', '/admin/logout'), ('GET', '/admin/api/overview')]:
                    self.assertEqual(client.request(method, path).status_code, 404, path)


class AdminTestBase(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'OFFWORK_ADMIN_USER': 'admin', 'OFFWORK_ADMIN_PASSWORD': 'secret-pass'})
        self.env.start()
        self.context = TestClient(create_app(':memory:'))
        self.client = self.context.__enter__()
        self.devices = [self.client.post('/v2/devices').json() for _ in range(2)]

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.env.stop()

    def login(self, username='admin', password='secret-pass'):
        return self.client.post('/admin/login', json={'username': username, 'password': password})

    def socket(self, identity):
        return self.client.websocket_connect('/v2/connect', headers={'Authorization': 'Bearer ' + identity['token']})

    def receive(self, socket, kind, request_id=None):
        for _ in range(100):
            msg = socket.receive_json()
            if msg['type'] == kind and (request_id is None or msg.get('request_id') == request_id):
                return msg
        self.fail('Expected protocol message not received')

    def command(self, socket, kind, **data):
        rid = uuid.uuid4().hex
        socket.send_json({'type': kind, 'request_id': rid, **data})
        return self.receive(socket, 'result', rid)

    def capabilities(self, name='测试角色', package='creator.test'):
        return {'packageId': package, 'packageVersion': '1.0.0', 'displayName': name,
                'digest': 'a' * 64, 'revision': 'rev-1',
                'interactions': [{'id': 'wave', 'label': '打招呼', 'order': 0}]}


class AdminAuthTests(AdminTestBase):
    def test_page_served(self):
        response = self.client.get('/admin')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn('管理端', response.text)

    def test_login_and_cookie_gate(self):
        self.assertEqual(self.client.get('/admin/api/overview').status_code, 401)
        bad = self.login(password='wrong')
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(bad.json()['detail'], '账号或密码不正确')
        self.assertEqual(self.login(username='nobody').json()['detail'], '账号或密码不正确')
        self.assertEqual(self.login().status_code, 200)
        self.assertIn('admin_session', self.client.cookies)
        self.assertEqual(self.client.get('/admin/api/overview').status_code, 200)

    def test_tampered_cookie_rejected(self):
        response = self.client.get('/admin/api/overview', headers={'Cookie': 'admin_session=9999999999.' + '0' * 64})
        self.assertEqual(response.status_code, 401)

    def test_expired_cookie_rejected(self):
        self.assertEqual(self.login().status_code, 200)
        with patch('server.admin.time.time', return_value=time.time() - 8 * 24 * 3600):
            self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.get('/admin/api/overview').status_code, 401)

    def test_login_rate_limit(self):
        for _ in range(10):
            self.assertEqual(self.login(password='wrong').status_code, 401)
        self.assertEqual(self.login(password='wrong').status_code, 429)

    def test_logout_clears_session(self):
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.post('/admin/logout').status_code, 200)
        self.assertEqual(self.client.get('/admin/api/overview').status_code, 401)

    def test_overview_totals_and_no_secrets(self):
        data = self.client.get('/admin/api/overview')
        self.assertEqual(data.status_code, 401)
        self.assertEqual(self.login().status_code, 200)
        response = self.client.get('/admin/api/overview')
        body = response.json()
        self.assertEqual(body['totals'], {'devices': 2, 'online': 0, 'rallies': 0, 'relations': 0})
        self.assertEqual(body['online'], [])
        self.assertEqual(body['rallies'], [])
        for identity in self.devices:
            self.assertNotIn(identity['token'], response.text)


class AdminDataTests(AdminTestBase):
    def test_online_device_rally_and_pairing(self):
        a, b = self.devices
        with self.socket(a) as sa, self.socket(b) as sb:
            self.assertTrue(self.command(sa, 'profile', capabilities=self.capabilities(), paused=False, visible=True)['ok'])
            self.assertTrue(self.command(sb, 'profile', capabilities=None, paused=False, visible=True)['ok'])
            created = self.command(sa, 'rally_create', name='测试集会', member_name='小明')
            code = created['data']['rally']['code']
            self.assertTrue(self.command(sb, 'rally_join', code=code, name='小红')['ok'])
            invite = self.command(sa, 'invite')
            self.assertTrue(self.command(sb, 'redeem', code=invite['data']['code'])['ok'])
            self.assertEqual(self.login().status_code, 200)
            body = self.client.get('/admin/api/overview').json()
            self.assertEqual(body['totals'], {'devices': 2, 'online': 2, 'rallies': 1, 'relations': 1})
            self.assertEqual(len(body['online']), 2)
            by_name = {item['name']: item for item in body['online']}
            self.assertEqual(by_name['小明']['character'], 'creator.test')
            self.assertEqual(by_name['小明']['rally'], '测试集会')
            self.assertEqual(by_name['小明']['status'], 'available')
            self.assertTrue(by_name['小明']['paired'])
            self.assertTrue(by_name['小明']['ip'])
            self.assertGreaterEqual(by_name['小明']['online_seconds'], 0)
            self.assertEqual(by_name['小红']['name'], '小红')
            self.assertEqual(len(body['rallies']), 1)
            rally = body['rallies'][0]
            self.assertEqual(rally['code'], code)
            self.assertEqual(rally['members'], 2)
            self.assertEqual(rally['online'], 2)
            self.assertEqual(rally['limit'], 12)
            self.assertEqual(rally['max_members'], 100)
            self.assertEqual({m['name'] for m in rally['member_names']}, {'小明', '小红'})

    def test_devices_rallies_trend_endpoints(self):
        a, b = self.devices
        with self.socket(a) as sa:
            self.command(sa, 'profile', capabilities=self.capabilities(name='小明', package='creator.cat'), paused=False, visible=True)
            self.command(sa, 'rally_create', name='测试集会', member_name='小明')
            self.login()
            for path in ('/admin/api/devices', '/admin/api/rallies', '/admin/api/trend'):
                self.assertEqual(self.client.get(path).status_code, 200, path)
            devices = self.client.get('/admin/api/devices').json()
            self.assertEqual(len(devices), 2)
            by_id = {d['device']: d for d in devices}
            self.assertEqual(by_id[a['device_id'][:8]]['name'], '小明')
            self.assertEqual(by_id[a['device_id'][:8]]['rally'], '测试集会')
            self.assertEqual(by_id[b['device_id'][:8]]['status'], 'offline')
            self.assertNotIn(a['token'], self.client.get('/admin/api/devices').text)
            self.assertEqual(len(self.client.get('/admin/api/rallies').json()), 1)
            self.assertIn('points', self.client.get('/admin/api/trend').json())


class AdminActionTests(AdminTestBase):
    def test_dissolve_and_limit(self):
        a, b = self.devices
        with self.socket(a) as sa:
            self.command(sa, 'profile', capabilities=self.capabilities(), paused=False, visible=True)
            code = self.command(sa, 'rally_create', name='测试集会', member_name='小明')['data']['rally']['code']
            self.login()
            self.assertEqual(self.client.post(f'/admin/api/rallies/{code}/limit', json={'limit': 5}).status_code, 200)
            self.assertEqual(self.client.get('/admin/api/rallies').json()[0]['limit'], 5)
            self.assertEqual(self.client.post(f'/admin/api/rallies/{code}/limit', json={'limit': 0}).status_code, 400)
            self.assertEqual(self.client.post(f'/admin/api/rallies/{code}/limit', json={'limit': 'x'}).status_code, 400)
            self.assertEqual(self.client.post('/admin/api/rallies/NOPE/limit', json={'limit': 5}).status_code, 404)
            self.assertEqual(self.client.post(f'/admin/api/rallies/{code}/dissolve').status_code, 200)
            self.assertEqual(self.client.get('/admin/api/rallies').json(), [])
            self.assertEqual(self.client.post(f'/admin/api/rallies/{code}/dissolve').status_code, 404)

    def test_kick_online_and_offline(self):
        a, b = self.devices
        self.login()
        short = a['device_id'][:8]
        self.assertEqual(self.client.post(f'/admin/api/devices/{short}/kick').status_code, 409)
        self.assertEqual(self.client.post('/admin/api/devices/zzzzzzzz/kick').status_code, 404)
        with self.socket(a) as sa:
            self.command(sa, 'profile', capabilities=self.capabilities(), paused=False, visible=True)
            self.assertEqual(self.client.post(f'/admin/api/devices/{short}/kick').status_code, 200)
            self.assertIsNone(self.client.app.state.relay.sessions.get(a['device_id']))

    def test_mutation_requires_login_and_same_origin(self):
        a, b = self.devices
        with self.socket(a) as sa:
            self.command(sa, 'profile', capabilities=self.capabilities(), paused=False, visible=True)
            code = self.command(sa, 'rally_create', name='测试集会', member_name='')['data']['rally']['code']
            self.assertEqual(self.client.post(f'/admin/api/rallies/{code}/dissolve').status_code, 401)
            self.login()
            self.assertEqual(self.client.post(f'/admin/api/rallies/{code}/dissolve',
                headers={'Origin': 'http://evil.example'}).status_code, 403)
            self.assertEqual(self.client.post(f'/admin/api/rallies/{code}/dissolve').status_code, 200)


if __name__ == '__main__':
    unittest.main()