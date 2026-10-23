from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QSpinBox, QVBoxLayout
from .ui_theme import label, style_window


class FocusDialog(QDialog):
    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.setWindowTitle('一起专注一会儿')
        self.setMinimumWidth(360)
        style_window(self)
        root = QVBoxLayout(self)
        root.addWidget(label('我在旁边，陪你完成这一段', 'heading'))
        root.addWidget(label('专注期间暂停自主漫步和搭子互动。结束后提醒休息。', 'muted'))
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 180)
        self.minutes.setValue(25)
        self.minutes.setSuffix(' 分钟')
        root.addWidget(self.minutes)
        self.status = label('', 'heading')
        root.addWidget(self.status)
        row = QHBoxLayout()
        self.start = QPushButton('开始专注')
        self.start.setProperty('variant', 'primary')
        self.start.clicked.connect(lambda: self.change('start'))
        self.pause = QPushButton('暂停')
        self.pause.clicked.connect(lambda: self.change('pause'))
        self.cancel = QPushButton('结束本次')
        self.cancel.clicked.connect(lambda: self.change('cancel'))
        for button in (self.start, self.pause, self.cancel):
            row.addWidget(button)
        root.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.setInterval(500)
        self.refresh()

    def change(self, action):
        focus = self.pet.focus
        if action == 'start':
            focus.start(self.minutes.value())
        elif action == 'pause':
            focus.resume() if focus.paused_seconds is not None else focus.pause()
        else:
            focus.cancel()
        self.pet.focus_changed()
        self.refresh()

    def refresh(self):
        focus = self.pet.focus
        minutes, seconds = divmod(focus.remaining, 60)
        self.status.setText(f'{minutes:02}:{seconds:02}' if focus.active else '准备好就开始吧')
        self.pause.setText('继续' if focus.paused_seconds is not None else '暂停')
        self.start.setEnabled(not focus.active)
        self.minutes.setEnabled(not focus.active)
        self.pause.setEnabled(focus.active)
        self.cancel.setEnabled(focus.active)

    def showEvent(self, event):
        self.timer.start()
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
