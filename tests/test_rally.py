"""Rally storage and relay protocol: membership, presence, ephemeral alert broadcast."""
import unittest
import uuid

from fastapi.testclient import TestClient

from server.app import create_app
from server.store import DomainError, Store


class RallyStoreTests(unittest.TestCase):
    def test_custom_capacity_rejects_full_group_then_allows_after_increase(self):
        rally = self.store.rally_create(self.a, '小组', member_limit=2)
        self.store.rally_join(self.b, rally['code'])
        with self.assertRaises(DomainError) as full:
            self.store.rally_join(self.c, rally['code'])
        self.assertEqual(full.exception.code, 'full')
        self.store.rally_set_limit(self.a, 3)
        self.assertEqual(len(self.store.rally_join(self.c, rally['code'])['members']), 3)

    def test_legacy_group_and_old_insert_shape_remain_compatible(self):
        self.store.db.execute('INSERT INTO rallies VALUES(?,?,?,?,?)', ('legacy', 'ABCDEFGH', '旧集会', self.a, 0))
        self.store.db.execute('INSERT INTO rally_members VALUES(?,?,?,?)', ('legacy', self.a, '原成员', 0))
        self.assertEqual(self.store.rally(self.a)['member_limit'], 12)
        self.assertEqual(self.store.rally_set_limit(self.a, 50)['member_limit'], 50)

    def test_configurable_member_limit_over_twelve_and_owner_permissions(self):
        rally = self.store.rally_create(self.a, '更大的集会', member_limit=25)
        for _ in range(13):
            self.store.rally_join(self.store.register()['device_id'], rally['code'])
        self.assertEqual(len(self.store.rally(self.a)['members']), 14)
        with self.assertRaises(DomainError):
            self.store.rally_set_limit(self.b, 30)
        with self.assertRaises(DomainError):
            self.store.rally_set_limit(self.a, 13)
        self.assertEqual(self.store.rally_set_limit(self.a, 30)['member_limit'], 30)
        for limit in (0, 101, True, '30'):
            with self.assertRaises(DomainError):
                self.store.rally_set_limit(self.a, limit)
        self.store.rally_leave(self.a)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM rally_settings').fetchone()[0], 0)

    def test_service_capacity_setting_bounds_new_groups(self):
        store = Store(':memory:', rally_max_members=3)
        try:
            owner = store.register()['device_id']
            self.assertEqual(store.rally_create(owner, '小组')['member_limit'], 3)
            with self.assertRaises(DomainError):
                store.rally_set_limit(owner, 4)
        finally:
            store.close()

    def setUp(self):
        self.store = Store(':memory:')
        self.a, self.b, self.c = [self.store.register()['device_id'] for _ in range(3)]

    def tearDown(self):
        self.store.close()

    def test_create_join_and_membership(self):
        rally = self.store.rally_create(self.a, '下班互助组', '小A')
        self.assertEqual(len(rally['code']), 8)
        self.assertEqual(rally['owner'], self.a)
        joined = self.store.rally_join(self.b, rally['code'].lower(), '小B')
        self.assertEqual(joined['id'], rally['id'])
        self.assertEqual([m['name'] for m in joined['members']], ['小A', '小B'])
        self.assertEqual(self.store.rally(self.c), None)

    def test_one_rally_per_device_and_idempotent_rejoin(self):
        first = self.store.rally_create(self.a, '一组')
        second = self.store.rally_create(self.b, '二组')
        with self.assertRaises(DomainError) as error:
            self.store.rally_join(self.a, second['code'])
        self.assertEqual(error.exception.code, 'joined')
        self.assertEqual(self.store.rally_join(self.a, first['code'])['id'], first['id'])
        with self.assertRaises(DomainError):
            self.store.rally_create(self.a, '三组')

    def test_invalid_code_and_member_cap(self):
        rally = self.store.rally_create(self.a, '一组')
        with self.assertRaises(DomainError) as error:
            self.store.rally_join(self.b, '!!!!!!!!')
        self.assertEqual(error.exception.code, 'format')
        with self.assertRaises(DomainError) as error:
            self.store.rally_join(self.b, 'ABCD2345')
        self.assertEqual(error.exception.code, 'unavailable')
        for _ in range(11):
            self.store.rally_join(self.store.register()['device_id'], rally['code'])
        with self.assertRaises(DomainError) as error:
            self.store.rally_join(self.c, rally['code'])
        self.assertEqual(error.exception.code, 'full')

    def test_member_leave_keeps_rally_owner_leave_dissolves(self):
        rally = self.store.rally_create(self.a, '一组')
        self.store.rally_join(self.b, rally['code'])
        self.store.rally_leave(self.b)
        self.assertIsNone(self.store.rally(self.b))
        self.assertEqual(len(self.store.rally(self.a)['members']), 1)
        self.store.rally_leave(self.a)
        self.assertIsNone(self.store.rally(self.a))
        with self.assertRaises(DomainError):
            self.store.rally_join(self.b, rally['code'])
        self.assertIsNone(self.store.rally_leave(self.c))

    def test_rename(self):
        rally = self.store.rally_create(self.a, '一组')
        self.store.rally_join(self.b, rally['code'])
        self.store.rally_name(self.b, '小明')
        self.assertEqual(self.store.rally(self.a)['members'][1]['name'], '小明')
        with self.assertRaises(DomainError):
            self.store.rally_name(self.b, 'x' * 25)


class RallyProtocolTests(unittest.TestCase):
    def test_custom_metadata_and_group_limit_round_trip(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            self.profile(a)
            self.profile(b)
            created = self.command(a, 'rally_create', name='自定义集会', member_limit=2)
            self.assertEqual(created['data']['rally']['member_limit'], 2)
            self.command(b, 'rally_join', code=created['data']['rally']['code'])
            result = self.command(a, 'rally_alert', level='warning', label='老板来了', color='#7964cb', alert_id=uuid.uuid4().hex)
            self.assertTrue(result['ok'])
            event = self.receive(b, 'rally_alert')
            self.assertEqual((event['label'], event['color']), ('老板来了', '#7964CB'))
            invalid = self.command(a, 'rally_alert', level='safe', label='坏\n消息', color='#7964CB', alert_id=uuid.uuid4().hex)
            self.assertEqual(invalid['code'], 'format')
            self.assertEqual(self.command(b, 'rally_limit', member_limit=30)['code'], 'forbidden')
            changed = self.command(a, 'rally_limit', member_limit=30)
            self.assertEqual(changed['data']['rally']['member_limit'], 30)
            push = self.receive(b, 'rally_state')
            self.assertEqual(push['rally']['member_limit'], 30)

    def setUp(self):
        self.context = TestClient(create_app(':memory:'))
        self.client = self.context.__enter__()
        self.a, self.b, self.c = [self.client.post('/v2/devices').json() for _ in range(3)]

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def socket(self, identity):
        return self.client.websocket_connect('/v2/connect', headers={'Authorization': 'Bearer ' + identity['token']})

    def receive(self, socket, kind, request_id=None):
        for _ in range(100):
            msg = socket.receive_json()
            if msg['type'] == kind and (request_id is None or msg.get('request_id') == request_id):
                return msg
        self.fail('Expected protocol message not received: ' + kind)

    def command(self, socket, kind, **data):
        rid = uuid.uuid4().hex
        socket.send_json({'type': kind, 'request_id': rid, **data})
        return self.receive(socket, 'result', rid)

    def profile(self, socket):
        return self.command(socket, 'profile', paused=False, visible=True, capabilities=None,
                            visitSupport=True, allowVisits=True)

    def test_health_declares_rally(self):
        self.assertIn('rally/v1', self.client.get('/health').json()['features'])

    def test_create_join_and_repeated_alerts(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            self.profile(a)
            self.profile(b)
            created = self.command(a, 'rally_create', name='下班互助组', member_name='小A')
            self.assertTrue(created['ok'])
            rally = created['data']['rally']
            joined = self.command(b, 'rally_join', code=rally['code'], name='小B')
            self.assertTrue(joined['ok'])
            push = self.receive(a, 'rally_state')
            self.assertEqual(len(push['rally']['members']), 2)
            self.assertEqual(push['rally']['members'][1]['name'], '小B')
            alert_id = uuid.uuid4().hex
            result = self.command(a, 'rally_alert', level='danger', alert_id=alert_id)
            event = self.receive(b, 'rally_alert')
            self.assertEqual(event['level'], 'danger')
            self.assertEqual(event['from'], '小A')
            self.assertEqual(event['rally_id'], rally['id'])
            self.assertGreater(event['expires_at'], 0)
            self.assertTrue(result['ok'])
            self.assertEqual(result['data']['delivered'], 1)
            duplicate = self.command(a, 'rally_alert', level='danger', alert_id=alert_id)
            self.assertTrue(duplicate['ok'])
            self.assertEqual(self.receive(b, 'rally_alert')['alert_id'], alert_id)
            invalid = self.command(a, 'rally_alert', level='explode', alert_id=uuid.uuid4().hex)
            self.assertEqual(invalid['code'], 'format')

    def test_alert_rate_limit(self):
        with self.socket(self.a) as a:
            self.profile(a)
            self.command(a, 'rally_create', name='一组')
            for _ in range(20):
                self.assertTrue(self.command(a, 'rally_alert', level='safe', alert_id=uuid.uuid4().hex)['ok'])
            for _ in range(50):
                limited = self.command(a, 'rally_alert', level='safe', alert_id=uuid.uuid4().hex)
                if not limited['ok']: break
            self.assertEqual(limited['code'], 'rate')

    def test_alert_requires_membership(self):
        with self.socket(self.a) as a:
            self.profile(a)
            result = self.command(a, 'rally_alert', level='safe', alert_id=uuid.uuid4().hex)
            self.assertEqual(result['code'], 'unjoined')

    def test_leave_and_dissolve_notify_members(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            self.profile(a)
            self.profile(b)
            rally = self.command(a, 'rally_create', name='一组')['data']['rally']
            self.command(b, 'rally_join', code=rally['code'])
            self.receive(a, 'rally_state')
            self.assertTrue(self.command(b, 'rally_leave')['ok'])
            push = self.receive(a, 'rally_state')
            self.assertEqual(len(push['rally']['members']), 1)
            self.command(b, 'rally_join', code=rally['code'])
            self.receive(a, 'rally_state')
            self.assertTrue(self.command(a, 'rally_leave')['ok'])
            push = self.receive(b, 'rally_state')
            self.assertIsNone(push['rally'])

    def test_rename_broadcasts_and_signs_alerts(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            self.profile(a)
            self.profile(b)
            rally = self.command(a, 'rally_create', name='一组')['data']['rally']
            self.command(b, 'rally_join', code=rally['code'])
            self.receive(a, 'rally_state')
            self.command(b, 'rally_name', name='小明')
            push = self.receive(a, 'rally_state')
            self.assertEqual(push['rally']['members'][1]['name'], '小明')
            self.command(b, 'rally_alert', level='warning', alert_id=uuid.uuid4().hex)
            event = self.receive(a, 'rally_alert')
            self.assertEqual(event['from'], '小明')
            self.assertEqual(event['level'], 'warning')

    def test_v1_clients_cannot_use_rally(self):
        identity = self.client.post('/v1/devices').json()
        with self.client.websocket_connect('/v1/connect', headers={'Authorization': 'Bearer ' + identity['token']}) as socket:
            self.command(socket, 'profile', character='lulu', paused=False, visible=True)
            result = self.command(socket, 'rally_create', name='一组')
            self.assertFalse(result['ok'])
            self.assertEqual(result['code'], 'format')


if __name__ == '__main__':
    unittest.main()
