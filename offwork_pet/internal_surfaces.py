"""Optional isolated, time-limited UIA geometry reads. All communication is local."""
import json
import math
import os
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QObject, QProcess, QTimer


class InternalSurfaces(QObject):
    def __init__(self):
        super().__init__()
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.receive)
        self.process.errorOccurred.connect(self.failed)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.failed)
        self.pending = None
        self.results, self.previous = [], []
        self.result_key = None
        self.result_time = self.next_request = 0
        self.buffer = b''

    def request(self, handle, geometry):
        now = monotonic()
        key = handle, geometry
        if self.result_key != key or now-self.result_time > 12:
            self.results = []
        if self.pending or now < self.next_request or self.process.state() != QProcess.ProcessState.NotRunning:
            return self.results
        self.pending = key
        self.buffer = b''
        program = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        script = Path(__file__).parent/'assets/ui/read-surfaces.ps1'
        self.process.start(str(program), ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-TargetWindow', str(handle)])
        self.timeout.start(5000)
        self.next_request = now+5
        return self.results

    def receive(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        if len(self.buffer) > 65536:
            self.failed()
            return
        if b'\n' not in self.buffer or self.pending is None:
            return
        try:
            rows = json.loads(self.buffer.split(b'\n', 1)[0])
            if not isinstance(rows, list) or len(rows) > 24:
                raise ValueError('invalid geometry')
            valid = []
            for row in rows:
                if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                        or len(row['id']) > 256 or not all(type(row.get(k)) in (int, float)
                        and math.isfinite(row[k]) for k in ('left', 'top', 'right', 'bottom'))):
                    raise ValueError('invalid geometry')
                valid.append(row)
            # Require two consecutive matching samples before using a ledge.
            self.results = [row for row in valid if row in self.previous] if self.result_key == self.pending else []
            self.previous = valid
            self.result_key = self.pending
            self.result_time = monotonic()
            self.pending = None
            self.timeout.stop()
            self.buffer = b''
        except (ValueError, TypeError):
            self.failed()

    def failed(self, *_):
        self.timeout.stop()
        self.pending = None
        self.results, self.previous = [], []
        self.next_request = monotonic()+30
        self.process.kill()

    def close(self):
        self.timeout.stop()
        self.pending = None
        self.results, self.previous = [], []
        self.process.kill()
        self.process.waitForFinished(1000)
