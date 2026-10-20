from tempfile import TemporaryDirectory
import unittest

from offwork_pet.credentials import IdentityStore, normalize_endpoint


class CredentialTests(unittest.TestCase):
    def test_dpapi_roundtrip_and_separate_services(self):
        with TemporaryDirectory() as folder:
            first = IdentityStore('https://pet.example.com', folder)
            first.set_identity({'device_id': 'test-device', 'token': 'private-test-token'})
            self.assertNotIn('private-test-token', first.path.read_text())
            self.assertEqual(IdentityStore('https://pet.example.com', folder).token, 'private-test-token')
            self.assertFalse(IdentityStore('https://other.example.com', folder).token)

    def test_endpoint_validation(self):
        self.assertEqual(normalize_endpoint(' http://127.0.0.1:8765/ '), 'http://127.0.0.1:8765')
        for bad in ('http://example.com', 'https://user:secret@example.com', 'https://example.com/path', 'file:///tmp', 'https://example.com?token=1'):
            with self.assertRaises(ValueError):
                normalize_endpoint(bad)
