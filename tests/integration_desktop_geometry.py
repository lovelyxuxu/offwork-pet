"""Opt-in native Windows geometry smoke check using a separate disposable test window."""
import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fixture():
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QLabel
    app = QApplication([])
    window = QLabel('Offwork Pet geometry test')
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    window.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
    window.resize(600, 280)
    screen = app.primaryScreen().availableGeometry()
    window.move(screen.x()+250, screen.y()+350)
    window.show()
    app.processEvents()
    print(json.dumps({'handle': int(window.winId())}), flush=True)
    commands = Queue()
    def read_commands():
        for line in sys.stdin:
            commands.put(line.strip())
    Thread(target=read_commands, daemon=True).start()
    def update():
        if commands.empty():
            return
        command = commands.get()
        if command == 'move':
            window.move(window.x()+120, window.y()+40)
        elif command == 'minimize':
            window.showMinimized()
        elif command == 'fullscreen':
            window.showFullScreen()
        elif command == 'quit':
            app.quit()
        app.processEvents()
        print(command, flush=True)
    timer = QTimer()
    timer.timeout.connect(update)
    timer.start(10)
    app.exec()


def check():
    from PySide6.QtWidgets import QApplication
    from offwork_pet.desktop_scene import DesktopObserver
    app = QApplication([])
    observer = DesktopObserver()
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--fixture'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', env=dict(os.environ, QT_QPA_PLATFORM='windows', PYTHONUTF8='1'),
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    responses = Queue()
    def read_responses():
        for line in child.stdout:
            responses.put(line.strip())
    Thread(target=read_responses, daemon=True).start()
    try:
        handle = json.loads(responses.get(timeout=10))['handle']
        key = f'window:{handle}'
        screen = app.primaryScreen()
        def ledges():
            return [s for s in observer.snapshot(screen).surfaces if s.key == key]
        first = ledges()
        assert first, 'Native test window was not recognized'
        child.stdin.write('move\n')
        child.stdin.flush()
        assert responses.get(timeout=10) == 'move'
        second = ledges()
        assert second, 'Moved native test window was not recognized'
        assert abs(second[0].origin_x-first[0].origin_x-120) < 2
        assert abs(second[0].y-first[0].y-40) < 2
        child.stdin.write('fullscreen\n')
        child.stdin.flush()
        assert responses.get(timeout=10) == 'fullscreen'
        # Select the fixture as foreground without taking keyboard focus from the user.
        # Bounds, visibility, styles and monitor mapping still use the real Win32 APIs.
        with patch.object(observer.native.user, 'GetForegroundWindow', return_value=handle):
            assert observer.snapshot(screen).fullscreen, 'Fullscreen native geometry was not recognized'
        child.stdin.write('minimize\n')
        child.stdin.flush()
        assert responses.get(timeout=10) == 'minimize'
        assert not ledges(), 'Minimized window remained a walkable surface'
        print('PASS: native visible edge, logical coordinates, window move, fullscreen geometry, minimize')
    finally:
        child.terminate()
        child.wait(timeout=5)


if __name__ == '__main__':
    if sys.platform != 'win32':
        raise SystemExit('This native smoke check requires Windows')
    fixture() if '--fixture' in sys.argv else check()
