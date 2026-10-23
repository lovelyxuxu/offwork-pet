"""Real Qt WebSocket clients against a real uvicorn process, isolated identities."""
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen
import socket

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication
from offwork_pet.app import Pet
from offwork_pet.core import Config, countdown
from offwork_pet.buddy import BuddyClient
from offwork_pet.credentials import IdentityStore

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
OUTPUT = ROOT / 'outputs'
OUTPUT.mkdir(exist_ok=True)
(ROOT / 'work').mkdir(exist_ok=True)

def until(predicate, description, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(description)

with TemporaryDirectory() as temporary:
    with socket.socket() as free:
        free.bind(('127.0.0.1', 0))
        port = free.getsockname()[1]
    env = dict(os.environ, OFFWORK_DB=str(Path(temporary) / 'relay.sqlite3'))
    log = open(ROOT / 'work/integration-server.log', 'w', encoding='utf-8')
    server = subprocess.Popen([sys.executable, '-m', 'server', '--port', str(port)], cwd=ROOT, env=env,
                              stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    a = b = None
    try:
        endpoint = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 10
        while True:
            try:
                urlopen(endpoint + '/health', timeout=0.3).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        a = Pet(Config(character='lulu'), persist=False, relay_endpoint=endpoint)
        b = Pet(Config(character='lumei'), persist=False, relay_endpoint=endpoint)
        a.move(100, 200)
        b.move(450, 200)
        a.buddy.persist = b.buddy.persist = True
        notices = []
        a.buddy.notice.connect(notices.append)
        os.environ['OFFWORK_HOME'] = str(Path(temporary) / 'a')
        a.buddy.start()
        until(lambda: a.buddy.ready, 'A did not connect')
        os.environ['OFFWORK_HOME'] = str(Path(temporary) / 'b')
        b.buddy.start()
        until(lambda: b.buddy.ready, 'B did not connect')
        a.buddy.invite()
        until(lambda: a.buddy.invitation, 'Invitation not generated')
        b.buddy.redeem(a.buddy.invitation['code'])
        until(lambda: a.buddy.relation and b.buddy.relation and a.buddy.status == 'available', 'Pairing not synchronized')
        assert a.buddy.relation['character'] == 'lumei'
        relation = a.buddy.relation['id']
        a.open_actions(1)
        app.processEvents()
        label = next(item['label'] for item in a.buddy.relation['capabilities']['interactions'] if item['id'] == 'pinch')
        next(button for button in a.action_panel.remote_buttons if button.text() == label).click()
        assert a.action_panel.isVisible(), 'Interaction shelf closed after choosing an action'
        until(lambda: b.runtime.current == 'cheek_reply', 'Remote package animation did not start')
        assert b.runtime.context['source'] == 'remote'
        until(lambda: '对方客户端已接收' in notices, 'Delivery not acknowledged')
        a.snapshot = countdown(datetime(2026, 10, 20, 17, 32), a.config)
        b.snapshot = a.snapshot
        a.grab().save(str(OUTPUT / 'lulu.png'))
        b.grab().save(str(OUTPUT / 'lumei-pinch.png'))
        a.open_buddy()
        app.processEvents()
        a.buddy_dialog.grab().save(str(OUTPUT / 'buddy-panel.png'))
        a.buddy_dialog.hide()
        b.set_paused(True)
        until(lambda: a.buddy.status == 'paused', 'Pause not visible on A')
        assert b.runtime.current is None and not b.runtime.queue
        b.set_paused(False)
        until(lambda: a.buddy.status == 'available', 'Resume not synchronized')
        b.hide()
        until(lambda: a.buddy.status == 'paused', 'Hidden pet was still available')
        b.show()
        until(lambda: a.buddy.status == 'available', 'Shown pet did not recover')
        b.buddy.socket.abort()
        until(lambda: not b.buddy.ready, 'Abrupt disconnect was not detected')
        until(lambda: b.buddy.ready and a.buddy.status == 'available', 'Automatic reconnect did not complete')
        b.buddy.stop()
        until(lambda: a.buddy.status == 'offline', 'Disconnect not synchronized')
        reloaded = IdentityStore(endpoint)
        assert reloaded.record['relation_cache']['id'] == relation
        b.buddy.unlink()
        assert b.buddy.identity.record['pending_unlink'] == relation
        b.buddy.start()
        until(lambda: b.buddy.ready and b.buddy.relation is None and a.buddy.relation is None, 'Offline unlink did not synchronize')
        assert not b.buddy.identity.record['pending_unlink']
        # An invalid server identity must stop retrying instead of looping forever.
        bad_config = Config()
        os.environ['OFFWORK_HOME'] = str(Path(temporary) / 'invalid')
        invalid = IdentityStore(endpoint)
        invalid.set_identity({'device_id': 'invalid', 'token': 'invalid-token'})
        bad = BuddyClient(bad_config, endpoint=endpoint)
        bad.start()
        until(lambda: not bad.enabled and '身份失效' in bad.connection_text, 'Invalid identity was not rejected')
        bad.stop()
        print('PASS: v2 HTTP + DPAPI, independent character packages, capability discovery, interaction, ack, pause, hide, reconnect, offline unlink', flush=True)
    finally:
        for pet in (a, b):
            if pet:
                pet.buddy.stop()
                pet.tray.hide()
                pet.hide()
        app.processEvents()
        server.terminate()
        server.wait(timeout=5)
        log.close()
