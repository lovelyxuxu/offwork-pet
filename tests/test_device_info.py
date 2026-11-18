import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_admin import AdminTestBase
from server.device_info import validate_device_info
from server.store import DomainError, Store
from offwork_pet.device_info import collect_device_info


INFO = {'computer_name': 'FINANCE-023', 'system_user': 'zhangsan',
        'lan_ips': ['192.168.1.36', '10.2.3.4'], 'os_version': 'Windows 11',
        'app_version': '0.6.0-preview.11'}


class DeviceInfoTests(unittest.TestCase):
    def test_collects_private_addresses_without_loopback_or_public_addresses(self):
        from PySide6.QtNetwork import QHostAddress
        with patch('offwork_pet.device_info.QNetworkInterface.allAddresses', return_value=[
                QHostAddress(ip) for ip in ['127.0.0.1', '192.168.1.36', '192.168.1.36',
                                         '8.8.8.8', '169.254.1.2', '::1', 'fd00::1']]), \
                patch('offwork_pet.device_info.platform.node', return_value='FINANCE-023'), \
                patch('offwork_pet.device_info.getpass.getuser', return_value='zhangsan'):
            result = collect_device_info()
        self.assertEqual(result['computer_name'], INFO['computer_name'])
        self.assertEqual(result['system_user'], INFO['system_user'])
        self.assertEqual(result['lan_ips'], ['192.168.1.36', 'fd00::1'])
        self.assertEqual(validate_device_info(result), result)

    def test_old_database_migration_and_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'devices.db'
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE devices(id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,character TEXT NOT NULL DEFAULT 'lulu',relation_id TEXT,ended_id TEXT NOT NULL DEFAULT '')")
                db.execute("INSERT INTO devices VALUES('old','hash','cat',NULL,'')")
            db.close()
            for attempt in range(2):
                store = Store(path)
                try:
                    self.assertEqual(store.device('old')['token_hash'], 'hash')
                    if attempt == 0:
                        self.assertEqual(store.device('old')['device_info'], '{}')
                        store.update_device_info('old', INFO)
                        store.admin_set_device_note('old', '张三 · 财务部')
                    else:
                        self.assertEqual(json.loads(store.device('old')['device_info']), INFO)
                        self.assertEqual(store.device('old')['admin_note'], '张三 · 财务部')
                        self.assertGreater(store.device('old')['info_reported_at'], 0)
                finally:
                    store.close()

    def test_rejects_bad_metadata(self):
        for invalid in (None, [], {'system_user': 3}, {'computer_name': 'a' * 129},
                        {'system_user': 'x\ny'}, {'lan_ips': '192.168.1.1'},
                        {'lan_ips': ['bad']}, {'lan_ips': ['8.8.8.8']},
                        {'lan_ips': ['127.0.0.1']}, {'lan_ips': ['10.0.0.1'] * 17}):
            with self.subTest(invalid=invalid), self.assertRaises(DomainError):
                validate_device_info(invalid)


class DeviceInfoApiTests(AdminTestBase):
    def test_report_reconnect_offline_and_admin_only(self):
        a, b = self.devices
        with self.socket(a) as sa, self.socket(b) as sb:
            self.assertTrue(self.command(sa, 'profile', paused=False, visible=True,
                                         capabilities=None, device_info=INFO)['ok'])
            self.command(sb, 'profile', paused=False, visible=True, capabilities=None)
            self.assertEqual(self.client.get('/admin/api/devices').status_code, 401)
            self.login()
            rows = self.client.get('/admin/api/devices').json()
            reported = next(row for row in rows if row['device_id'] == a['device_id'])
            self.assertEqual(reported['device_info'], INFO)
            self.assertGreater(reported['info_reported_at'], 0)
            self.assertEqual(next(row for row in rows if row['device_id'] == b['device_id'])['device_info'], {})
            # An older client sending no metadata must not erase the last report.
            self.command(sa, 'profile', paused=False, visible=True, capabilities=None)
            # Metadata never enters peer-visible state, even after pairing.
            code = self.command(sa, 'invite')['data']['code']
            peer_result = self.command(sb, 'redeem', code=code)
            self.assertNotIn('FINANCE-023', json.dumps(peer_result))
            self.assertNotIn('zhangsan', json.dumps(self.client.app.state.relay.state(b['device_id'])))
        with self.socket(a) as sa:
            self.command(sa, 'profile', paused=False, visible=True, capabilities=None)
            online = self.client.get('/admin/api/overview').json()['online']
            self.assertEqual(next(row for row in online if row['device_id'] == a['device_id'])['device_info'], INFO)
        row = next(row for row in self.client.get('/admin/api/devices').json() if row['device_id'] == a['device_id'])
        self.assertEqual(row['device_info'], INFO)
        self.assertEqual(row['status'], 'offline')

    def test_invalid_profile_does_not_overwrite_valid_report(self):
        a = self.devices[0]
        with self.socket(a) as sa:
            self.command(sa, 'profile', paused=False, visible=True, capabilities=None, device_info=INFO)
            result = self.command(sa, 'profile', paused=False, visible=True, capabilities=None,
                                  device_info={'computer_name': ['bad']})
            self.assertFalse(result['ok'])
            self.assertEqual(json.loads(self.client.app.state.relay.store.device(a['device_id'])['device_info']), INFO)

    def test_notes_require_admin_and_origin_and_support_clear(self):
        a = self.devices[0]
        url = f"/admin/api/devices/{a['device_id']}/note"
        self.assertEqual(self.client.post(url, json={'note': '张三'}).status_code, 401)
        self.login()
        self.assertEqual(self.client.post(url, json={'note': '张三'}, headers={'Origin': 'http://evil.example'}).status_code, 403)
        for invalid in ({}, [], {'note': None}, {'note': 'a' * 201}, {'note': 'x\ny'}):
            self.assertEqual(self.client.post(url, json=invalid).status_code, 400)
        self.assertEqual(self.client.post(url, content='{').status_code, 400)
        self.assertEqual(self.client.post('/admin/api/devices/' + '0' * 32 + '/note', json={'note': '张三'}).status_code, 404)
        self.assertEqual(self.client.post(url, json={'note': ' 张三 · 财务部 '}).status_code, 200)
        row = next(row for row in self.client.get('/admin/api/devices').json() if row['device_id'] == a['device_id'])
        self.assertEqual(row['admin_note'], '张三 · 财务部')
        self.assertEqual(self.client.post(url, json={'note': ''}).status_code, 200)
        self.assertEqual(self.client.app.state.relay.store.device(a['device_id'])['admin_note'], '')
