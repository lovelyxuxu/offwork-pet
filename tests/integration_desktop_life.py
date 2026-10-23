"""Native smoke check: real UI Automation geometry in an isolated disposable window."""
import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread
from time import monotonic
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fixture():
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QVBoxLayout, QWidget
    app = QApplication([])
    window = QWidget()
    window.setWindowTitle('Offwork Pet UI geometry fixture')
    window.setWindowFlags(window.windowFlags() | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel('Geometry fixture'))
    panel = QGroupBox('Panel')
    panel.setMinimumSize(500, 200)
    QVBoxLayout(panel).addWidget(QLabel('Geometry only'))
    layout.addWidget(panel)
    window.move(200, 360)
    window.show()
    app.processEvents()
    print(json.dumps({'handle': int(window.winId())}), flush=True)
    QTimer.singleShot(25000, app.quit)
    app.exec()


def check():
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from offwork_pet.desktop_scene import DesktopObserver
    app = QApplication([])
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--fixture'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
        env=dict(os.environ, QT_QPA_PLATFORM='windows', PYTHONUTF8='1'),
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    queue = Queue()
    Thread(target=lambda: queue.put(child.stdout.readline()), daemon=True).start()
    observer = DesktopObserver()
    observer.internal_enabled = True
    try:
        handle = json.loads(queue.get(timeout=10))['handle']
        deadline = monotonic()+16
        ledges = []
        with patch.object(observer.native.user, 'GetForegroundWindow', return_value=handle):
            while monotonic() < deadline:
                scene = observer.snapshot(app.primaryScreen())
                ledges = [s for s in scene.surfaces if s.key.startswith(f'ui:{handle}:')]
                if ledges:
                    break
                QTest.qWait(100)
        assert ledges, f'No UIA ledges: results={observer.internal.results if observer.internal else None}; stderr={bytes(observer.internal.process.readAllStandardError()).decode(errors="replace") if observer.internal else ""}'
        assert all(s.right-s.left >= 60 for s in ledges)
        observer.internal_enabled = False
        observer.snapshot(app.primaryScreen())
        assert observer.internal is None
        print('PASS: native UIA group geometry, stable samples, bounded worker and disable cleanup')
    finally:
        observer.close()
        child.terminate()
        child.wait(timeout=5)


if __name__ == '__main__':
    fixture() if '--fixture' in sys.argv else check()
