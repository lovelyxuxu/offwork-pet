"""Two real Qt clients, private HTTP assets, real relay; no product identities."""
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QPoint, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication
from offwork_pet.app import Pet
from offwork_pet.core import Config
from offwork_pet.packages import PackageLibrary


def main():
    app = QApplication.instance() or QApplication([])
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    app.setQuitOnLastWindowClosed(False)
    errors = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda kind, value, trace: errors.append(str(value))
    def until(predicate, description, timeout=15):
        # Keep a native event loop running: sleeping between processEvents()
        # throttles QFile uploads of HD packs and does not model the real app.
        loop = QEventLoop()
        poll, deadline = QTimer(), QTimer()
        poll.setInterval(10)
        deadline.setSingleShot(True)
        poll.timeout.connect(lambda: loop.quit() if errors or predicate() else None)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(int(timeout*1000))
        try:
            if not errors and not predicate():
                loop.exec()
        finally:
            poll.stop()
            deadline.stop()
        if errors:
            raise AssertionError('Qt callback failed: '+errors[-1])
        if not predicate():
            raise AssertionError(description)

    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        with socket.socket() as free:
            free.bind(('127.0.0.1', 0))
            port = free.getsockname()[1]
        (ROOT/'work').mkdir(exist_ok=True)
        a = b = None
        old_home = os.environ.get('OFFWORK_HOME')
        log = (ROOT/'work/integration-visits-server.log').open('w', encoding='utf-8')
        server = subprocess.Popen([sys.executable, '-m', 'server', '--port', str(port)], cwd=ROOT,
            env=dict(os.environ, OFFWORK_DB=str(root/'relay.sqlite3')),
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            endpoint = f'http://127.0.0.1:{port}'
            deadline = time.monotonic()+10
            while True:
                try:
                    urlopen(endpoint+'/health', timeout=.3).close()
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.05)
            lib_a, lib_b = PackageLibrary(root/'a-library'), PackageLibrary(root/'b-library')
            source = ROOT/'offwork_pet/assets/starter-packs'
            lib_a.install(source/'monthly-salary-cat.petpack')
            lib_b.install(source/'gugagaga.petpack')
            a = Pet(Config(character='monthly-salary-cat', autonomous_enabled=False, auto_update_check=False),
                persist=False, relay_endpoint=endpoint, package_directory=lib_a.root, seed_starters=False)
            b = Pet(Config(character='gugagaga', autonomous_enabled=False, auto_update_check=False),
                persist=False, relay_endpoint=endpoint, package_directory=lib_b.root, seed_starters=False)
            a.move(100, 200); b.move(450, 200)
            os.environ['OFFWORK_HOME'] = str(root/'a-identity')
            a.buddy.start()
            until(lambda: a.buddy.ready, 'sender did not connect')
            os.environ['OFFWORK_HOME'] = str(root/'b-identity')
            b.buddy.start()
            until(lambda: b.buddy.ready, 'host did not connect')
            a.buddy.invite()
            until(lambda: a.buddy.invitation, 'invitation missing')
            b.buddy.redeem(a.buddy.invitation['code'])
            until(lambda: a.visits.supported and b.visits.supported, 'visit capabilities did not synchronize')
            notices = []
            a.buddy.notice.connect(notices.append)
            original = b.runtime, b.runtime.revision, b.pos(), b.runtime.current
            a.open_actions(1)
            a.action_panel.visit_start.click()
            until(lambda: b.visits.guest is not None and b.visits.guest.portal is not None,
                  'doorway introduction did not start')
            assert a.visits.outgoing['stage'] != 'active', 'actions became ready before the doorway finished'
            assert not b.visits.guest.can_interact
            until(lambda: a.visits.outgoing and a.visits.outgoing['stage'] == 'active'
                and b.visits.guest and b.visits.guest.isVisible(), 'visitor did not arrive')
            assert b.visits.guest.runtime.package.id == 'monthly-salary-cat'
            assert (b.runtime, b.runtime.revision, b.pos(), b.runtime.current) == original, 'host changed on arrival'
            assert [p['id'] for p in b.library.entries()] == ['gugagaga'], 'visitor became a personal character'
            assert a.action_panel.remote_buttons and all(button.isEnabled() for button in a.action_panel.remote_buttons)
            a.action_panel.remote_buttons[0].click()
            until(lambda: b.visits.guest.runtime.current is not None, 'visitor did not play its action')
            assert b.visits.guest.runtime.context['source'] == 'visitor'
            assert b.runtime.current is None, 'host was animated by the visit'
            until(lambda: any('表演完成' in n for n in notices), 'terminal action receipt missing')
            # Produce an actual rendered UI preview with both independent windows.
            output = ROOT/'outputs/visitor-preview.png'
            output.parent.mkdir(exist_ok=True)
            picture = QImage(620, 320, QImage.Format.Format_ARGB32)
            picture.fill(QColor('#FAF6F1'))
            painter = QPainter(picture)
            painter.drawPixmap(QPoint(15, 15), b.grab())
            painter.drawPixmap(QPoint(325, 15), b.visits.guest.grab())
            painter.end()
            assert picture.save(str(output))
            guest = b.visits.guest
            b.visits.dismiss()
            until(lambda: a.visits.outgoing is None and b.visits.incoming is None, 'dismissal did not reach sender')
            assert guest.runtime.stopped and guest.player.cache.bytes == 0
            with patch.object(b.visits, 'download', wraps=b.visits.download) as download:
                a.visits.start()
                until(lambda: a.visits.outgoing and a.visits.outgoing['stage'] == 'active'
                    and b.visits.guest is not None, 'cached visitor did not arrive')
                assert download.call_count == 0, 'cached visitor was downloaded again'
            b.set_paused(True)
            until(lambda: a.visits.outgoing is None and b.visits.guest is None, 'pause left an active guest')
            b.set_paused(False)
            until(lambda: a.buddy.status == 'available', 'resume did not synchronize')
            # The reverse direction uses wholly different ids and a host with local interaction off.
            a.config.interactive = False
            b.visits.start()
            until(lambda: b.visits.outgoing and b.visits.outgoing['stage'] == 'active'
                and a.visits.guest is not None, 'reverse visitor did not arrive')
            entry = b.runtime.package.behavior['interactions'][0]['id']
            b.visits.play(entry)
            until(lambda: a.visits.guest.runtime.current is not None, 'local interaction switch blocked visitor')
            b.select_package('gugagaga')
            until(lambda: b.visits.outgoing is None and a.visits.guest is None, 'role switch left a stale guest')
            until(lambda: b.buddy.ready and a.buddy.status == 'available', 'role synchronization failed')
            a.config.interactive = True
            a.visits.start()
            until(lambda: b.visits.guest is not None and a.visits.outgoing['stage'] == 'active', 'third visit missing')
            a.visits.leave()
            until(lambda: b.visits.guest is None and a.visits.outgoing is None, 'sender could not leave')
            a.visits.start()
            until(lambda: b.visits.guest is not None and a.visits.outgoing['stage'] == 'active', 'fourth visit missing')
            b.buddy.stop()
            until(lambda: a.visits.outgoing is None and b.visits.guest is None, 'disconnect left a visitor')
            assert not a.visits.timer.isActive() and not b.visits.timer.isActive()
            print('PASS: two Qt clients; different roles; authenticated HTTP assets; independent host; action completion; cache reuse; dismissal; pause; reverse visit; local switch; role switch; disconnect')
        finally:
            for pet in (a, b):
                if pet:
                    pet.visits.shutdown(); pet.buddy.stop(); pet.tray.hide(); pet.hide(); pet.deleteLater()
            app.processEvents()
            server.terminate(); server.wait(timeout=5); log.close()
            sys.excepthook = previous_hook
            if old_home is None:
                os.environ.pop('OFFWORK_HOME', None)
            else:
                os.environ['OFFWORK_HOME'] = old_home


if __name__ == '__main__':
    main()
