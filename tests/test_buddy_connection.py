"""Protocol and reverse-proxy failures must stop rather than loop forever."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from offwork_pet.buddy import BuddyClient
from offwork_pet.core import Config


class BuddyConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def check_blocked(self, health, token='', devices_html=False):
        paths = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                raw = health if isinstance(health, bytes) else json.dumps(health).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)
            def do_POST(self):
                paths.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'<!doctype html><html>website fallback</html>')
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f'http://127.0.0.1:{server.server_port}'
        identity = SimpleNamespace(endpoint=endpoint, token=token, record={})
        client = BuddyClient(Config(network_enabled=True), persist=False, endpoint=endpoint)
        try:
            with patch('offwork_pet.buddy.IdentityStore', return_value=identity):
                client.start()
                deadline = time.monotonic() + 3
                while client.enabled and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
            self.assertFalse(client.enabled)
            self.assertFalse(client.config.network_enabled)
            self.assertFalse(client.retry.isActive())
            self.assertIsNone(client.socket)
            self.assertIn('升级', client.connection_text)
            self.assertEqual(paths, ['/health', '/v2/devices'] if devices_html else ['/health'])
        finally:
            client.stop()
            client.deadlines.stop()
            client.deleteLater()
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_old_service_stops_before_registration(self):
        self.check_blocked({'ok': True, 'protocol': 1})

    def test_cached_device_checks_service_before_websocket(self):
        self.check_blocked({'ok': True, 'protocol': 1}, token='cached-token')

    def test_website_fallback_is_not_a_service(self):
        self.check_blocked(b'<html>website fallback</html>')

    def test_registration_proxy_returning_html_stops(self):
        self.check_blocked({'ok': True, 'protocol': 2}, devices_html=True)
