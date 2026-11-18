"""Two real Qt pets + a temporary relay: rally create/join,三色广播, auto-hide, panel preview."""
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
from offwork_pet.core import Config

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


def hold(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


with TemporaryDirectory() as temporary:
    with socket.socket() as free:
        free.bind(('127.0.0.1', 0))
        port = free.getsockname()[1]
    env = dict(os.environ, OFFWORK_DB=str(Path(temporary) / 'relay.sqlite3'))
    log = open(ROOT / 'work/integration-rally-server.log', 'w', encoding='utf-8')
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
        a = Pet(Config(character='gugagaga', alert_seconds=2, rally_name='小A'), persist=False,
                relay_endpoint=endpoint, enable_alert_hotkeys=False)
        b = Pet(Config(character='noah-lulu', alert_seconds=2, rally_name='小B'), persist=False,
                relay_endpoint=endpoint, enable_alert_hotkeys=False)
        a.move(100, 200)
        b.move(450, 200)
        a.buddy.persist = b.buddy.persist = True
        os.environ['OFFWORK_HOME'] = str(Path(temporary) / 'a')
        a.buddy.start()
        until(lambda: a.buddy.ready, 'A did not connect')
        os.environ['OFFWORK_HOME'] = str(Path(temporary) / 'b')
        b.buddy.start()
        until(lambda: b.buddy.ready, 'B did not connect')

        notices = []
        b.alerts.notice.connect(notices.append)
        b.alerts.trigger('safe')
        assert b.alerts.overlay.isVisible() and b.alerts.overlay.level == 'safe'
        assert any('本机' in notice for notice in notices), 'Local-only trigger should say it stays local'
        until(lambda: not b.alerts.overlay.isVisible(), 'Local flare did not auto-hide', timeout=6)

        a.buddy.rally_create('下班互助组')
        until(lambda: a.buddy.rally, 'Rally not created')
        code = a.buddy.rally['code']
        b.buddy.rally_join(code, '小B')
        until(lambda: b.buddy.rally and a.buddy.rally and len(a.buddy.rally['members']) == 2, 'Rally membership not synchronized')
        assert a.buddy.rally['members'][0]['name'] == '小A'

        a.alerts.trigger('danger')
        until(lambda: b.alerts.overlay.isVisible() and b.alerts.overlay.level == 'danger', 'Danger flare did not reach B')
        assert b.alerts.overlay.source == '小A', f"Unexpected source: {b.alerts.overlay.source}"
        hold(0.4)
        b.alerts.overlay.grab().save(str(OUTPUT / 'rally-danger.png'))
        until(lambda: not b.alerts.overlay.isVisible(), 'Broadcast flare did not auto-hide', timeout=6)

        b.alerts.trigger('warning')
        until(lambda: a.alerts.overlay.isVisible() and a.alerts.overlay.level == 'warning', 'Warning flare did not reach A')
        assert a.alerts.overlay.source == '小B'
        hold(0.4)
        a.alerts.overlay.grab().save(str(OUTPUT / 'rally-warning.png'))
        until(lambda: not a.alerts.overlay.isVisible(), 'Warning flare did not auto-hide', timeout=6)

        b.alerts.trigger('safe')
        until(lambda: a.alerts.overlay.isVisible() and a.alerts.overlay.level == 'safe', 'Safe flare did not reach A')
        hold(0.4)
        a.alerts.overlay.grab().save(str(OUTPUT / 'rally-safe.png'))
        until(lambda: not a.alerts.overlay.isVisible(), 'Safe flare did not auto-hide', timeout=6)

        from offwork_pet.core import alert_buttons
        a.config.alert_buttons = alert_buttons(a.config) + [
            {'id': 'meeting', 'name': '开会啦', 'color': '#7964CB', 'hotkey': '', 'level': 'warning'}]
        a.alerts.update_config(a.config)
        a.alerts.trigger('meeting')
        until(lambda: b.alerts.overlay.isVisible() and b.alerts.overlay.label == '开会啦', 'Custom text did not reach B')
        assert b.alerts.overlay.color == '#7964CB'
        caption, font, _ = b.alerts.overlay.caption_layout()
        assert font.pointSize() == 8 and caption.height() == 22
        a.buddy.rally_set_limit(40)
        until(lambda: a.buddy.rally['member_limit'] == b.buddy.rally['member_limit'] == 40, 'Member limit not synchronized')
        until(lambda: not b.alerts.overlay.isVisible(), 'Custom flare did not auto-hide', timeout=6)

        a.open_rally()
        app.processEvents()
        a.rally_dialog.grab().save(str(OUTPUT / 'rally-panel.png'))
        a.rally_dialog.hide()

        b.config.paused = True
        b.alerts.receive({'type': 'rally_alert', 'rally_id': b.buddy.rally['id'], 'alert_id': 'paused-check',
                          'level': 'danger', 'from': '小A', 'expires_at': time.time() + 5})
        assert not b.alerts.overlay.isVisible(), 'Paused client must not display rally alerts'
        b.config.paused = False

        b.buddy.rally_leave()
        until(lambda: b.buddy.rally is None and a.buddy.rally and len(a.buddy.rally['members']) == 1, 'Leave not synchronized')
        a.buddy.rally_leave()
        until(lambda: a.buddy.rally is None, 'Owner leave did not dissolve')
        print('PASS: rally create/join, three-level and custom broadcasts, small bottom-right caption, member-limit sync, auto-hide, paused filter, leave/dissolve, panel preview', flush=True)
    finally:
        for pet in (a, b):
            if pet:
                pet.alerts.shutdown()
                pet.buddy.stop()
                pet.tray.hide()
                pet.hide()
        app.processEvents()
        server.terminate()
        server.wait(timeout=5)
        log.close()
