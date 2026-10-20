from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import uuid

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.app import create_app
from server.store import DomainError, Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.a, self.b, self.c = [self.store.register()['device_id'] for _ in range(3)]

    def tearDown(self):
        self.store.close()

    def test_single_use_and_idempotent_original_redeemer(self):
        code = self.store.invite(self.a)['code']
        relation = self.store.redeem(self.b, code)
        self.assertEqual(self.store.redeem(self.b, code), relation)
        self.assertEqual(self.store.relation(self.a)['partner_id'], self.b)
        with self.assertRaises(DomainError):
            self.store.redeem(self.c, code)

    def test_expiry_revoke_and_self(self):
        code = self.store.invite(self.a, now=0)['code']
        with self.assertRaises(DomainError) as error:
            self.store.redeem(self.b, code, now=601)
        self.assertEqual(error.exception.code, 'expired')
        code = self.store.invite(self.a)['code']
        with self.assertRaises(DomainError):
            self.store.redeem(self.a, code)
        self.store.revoke(self.a)
        with self.assertRaises(DomainError):
            self.store.redeem(self.b, code)

    def test_stale_unlink_cannot_end_new_relation(self):
        old = self.store.redeem(self.b, self.store.invite(self.a)['code'])['id']
        self.store.unlink(self.a, old)
        new = self.store.redeem(self.c, self.store.invite(self.a)['code'])['id']
        self.store.unlink(self.a, old)
        self.assertEqual(self.store.relation(self.a)['id'], new)
        self.assertIsNone(self.store.relation(self.b))

    def test_simultaneous_redemption_has_one_winner(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'test.sqlite3'
            seed = Store(path)
            a, b, c = [seed.register()['device_id'] for _ in range(3)]
            code = seed.invite(a)['code']
            def attempt(device):
                store = Store(path)
                try:
                    store.redeem(device, code)
                    return True
                except DomainError:
                    return False
                finally:
                    store.close()
            with ThreadPoolExecutor(2) as executor:
                self.assertEqual(sum(executor.map(attempt, (b, c))), 1)
            seed.close()


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.context = TestClient(create_app(':memory:'))
        self.client = self.context.__enter__()
        self.a, self.b, self.c = [self.client.post('/v1/devices').json() for _ in range(3)]

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def socket(self, identity):
        return self.client.websocket_connect('/v1/connect', headers={'Authorization': 'Bearer ' + identity['token']})

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

    def profile(self, socket, **options):
        return self.command(socket, 'profile', character='lulu', paused=options.get('paused', False), visible=options.get('visible', True))

    def pair(self, a, b):
        self.profile(a)
        self.profile(b)
        code = self.command(a, 'invite')['data']['code']
        result = self.command(b, 'redeem', code=code)
        self.assertTrue(result['ok'])
        return result['data']['relation']['id']

    def test_authentication_and_health(self):
        self.assertTrue(self.client.get('/health').json()['ok'])
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/v1/connect') as socket:
                socket.receive_json()

    def test_pair_action_ack_and_deduplication(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            relation = self.pair(a, b)
            rid = uuid.uuid4().hex
            action_id = uuid.uuid4().hex
            a.send_json({'type': 'action', 'request_id': rid, 'action_id': action_id,
                         'relation_id': relation, 'action': 'pinch', 'count': 3})
            event = self.receive(b, 'action')
            self.assertEqual(event['count'], 3)
            b.send_json({'type': 'ack', 'relation_id': relation, 'action_id': action_id, 'accepted': True})
            self.assertTrue(self.receive(a, 'result', rid)['ok'])
            duplicate = self.command(a, 'action', action_id=action_id, relation_id=relation, action='pinch', count=3)
            self.assertEqual(duplicate['code'], 'duplicate')

    def test_paused_hidden_and_unlinked_reject(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            relation = self.pair(a, b)
            for profile in ({'paused': True}, {'visible': False}):
                self.profile(b, **profile)
                result = self.command(a, 'action', action_id=uuid.uuid4().hex, relation_id=relation, action='bonk', count=1)
                self.assertEqual(result['code'], 'paused')
            self.command(b, 'unlink', relation_id=relation)
            result = self.command(a, 'action', action_id=uuid.uuid4().hex, relation_id=relation, action='pinch', count=1)
            self.assertEqual(result['code'], 'unlinked')

    def test_offline_not_queued_and_profiles_independent(self):
        with self.socket(self.a) as a:
            with self.socket(self.b) as b:
                relation = self.pair(a, b)
                self.command(b, 'profile', character='lumei', paused=False, visible=True)
                state = self.command(a, 'profile', character='lulu', paused=False, visible=True)['data']
                self.assertEqual(state['relation']['character'], 'lumei')
                self.command(b, 'profile', character='lulu', paused=False, visible=True)
                same = self.command(a, 'profile', character='lulu', paused=False, visible=True)['data']
                self.assertEqual(same['relation']['character'], 'lulu')
            result = self.command(a, 'action', action_id=uuid.uuid4().hex, relation_id=relation, action='pinch', count=1)
            self.assertEqual(result['code'], 'offline')
            self.assertFalse(self.client.app.state.relay.pending)

    def test_malformed_input_does_not_close_connection(self):
        with self.socket(self.a) as a:
            self.receive(a, 'state')
            a.send_text('{broken')
            self.assertFalse(self.receive(a, 'result')['ok'])
            self.assertTrue(self.profile(a)['ok'])

    def test_expired_receipt_is_not_reported_as_delivered(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            relation = self.pair(a, b)
            rid, aid = uuid.uuid4().hex, uuid.uuid4().hex
            a.send_json({'type': 'action', 'request_id': rid, 'action_id': aid,
                         'relation_id': relation, 'action': 'bonk', 'count': 1})
            self.receive(b, 'action')
            self.client.app.state.relay.pending[(relation, aid)]['expires'] = 0
            b.send_json({'type': 'ack', 'relation_id': relation, 'action_id': aid, 'accepted': True})
            self.assertEqual(self.receive(a, 'result', rid)['code'], 'unknown')


if __name__ == '__main__':
    unittest.main()
