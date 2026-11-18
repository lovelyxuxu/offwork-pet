"""The public acceptance cleanup must preserve all pre-existing user records."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from server.store import Store
from tools.verify_rally_public import REMOTE


class PublicCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'relay.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.store.close)
        self.original = self.store.register()['device_id']
        self.name = '验收-123456abcdef'

    def report(self, devices=()):
        connect = sqlite3.connect
        output = io.StringIO()
        def local_connection(database_uri, **kwargs):
            mode = 'rw' if 'mode=rw' in database_uri else 'ro'
            return connect(self.path.as_uri() + '?mode=' + mode, **kwargs)
        with patch('sqlite3.connect', side_effect=local_connection), redirect_stdout(output):
            exec(REMOTE, {'payload': {'devices': list(devices), 'name': self.name}})
        return json.loads(output.getvalue())

    def test_partial_registration_cleanup_preserves_original_device(self):
        before = self.report()
        device = self.store.register()['device_id']
        self.assertEqual(self.report([device]), before)

    def test_cleanup_removes_only_test_rally_and_devices(self):
        self.store.rally_create(self.original, '原有集会')
        before = self.report()
        a, b = [self.store.register()['device_id'] for _ in range(2)]
        rally = self.store.rally_create(a, self.name)
        self.store.rally_join(b, rally['code'])
        self.assertEqual(self.report([a, b]), before)

    def test_cleanup_refuses_test_device_with_real_relation(self):
        device = self.store.register()['device_id']
        self.store.db.execute('INSERT INTO relations VALUES (?,?,?)', ('relation', device, self.original))
        before = self.report()
        with self.assertRaisesRegex(RuntimeError, 'paired'):
            self.report([device])
        self.assertEqual(self.report(), before)

    def test_cleanup_refuses_non_test_rally_member(self):
        device = self.store.register()['device_id']
        rally = self.store.rally_create(device, self.name)
        self.store.rally_join(self.original, rally['code'])
        before = self.report()
        with self.assertRaisesRegex(RuntimeError, 'non-test member'):
            self.report([device])
        self.assertEqual(self.report(), before)


if __name__ == '__main__':
    unittest.main()
