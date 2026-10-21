"""Native transparent desktop pet with rendered 3D Lulu and vector Lumei."""
from dataclasses import replace
from collections import deque
from datetime import datetime
import os
from pathlib import Path
import sys
from time import monotonic

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTime, QTimer, QLockFile
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QMenu, QMessageBox,
    QSlider, QSpinBox, QSystemTrayIcon, QTimeEdit, QVBoxLayout, QWidget,
)

from .core import Config, ReminderTracker, bubble_visible, countdown, data_dir, load_config, save_config
from .buddy import BuddyClient
from .buddy_ui import BuddyDialog
from .characters import CHARACTERS, character_icon, draw_character
from .sprites import contains as sprite_contains
from . import __version__

PALETTES = {"奶油橘": ("#F5C997", "#FFF0D9"), "薄荷绿": ("#A9D5BB", "#E8F6EC"), "云朵蓝": ("#B3CDEC", "#ECF3FF")}
BUBBLE_MODES = {"始终展示": "always", "鼠标悬停时展示": "hover", "点击宠物后展示": "click", "悬停或点击时展示": "hover_click", "始终隐藏": "hidden"}


def startup_enabled():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.QueryValueEx(key, "OffworkPet")
        return True
    except OSError:
        return False


def set_startup(enabled):
    if sys.platform != "win32":
        return
    import subprocess
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            if getattr(sys, "frozen", False):
                args = [sys.executable]
            else:
                executable = Path(sys.executable).with_name("pythonw.exe")
                args = [str(executable if executable.exists() else Path(sys.executable)), str(Path(__file__).resolve().parents[1] / "main.py")]
            winreg.SetValueEx(key, "OffworkPet", 0, winreg.REG_SZ, subprocess.list2cmdline(args))
        else:
            try:
                winreg.DeleteValue(key, "OffworkPet")
            except FileNotFoundError:
                pass


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"下班搭子 · 设置 · v{__version__}")
        self.setMinimumWidth(430)
        self.setStyleSheet("QDialog {background:#FAF8F4;} QLabel,QCheckBox {color:#453F39;font-size:13px;} QPushButton {padding:7px 16px;} QTimeEdit,QComboBox {padding:5px;}")
        root = QVBoxLayout(self)
        title = QLabel("让下班，值得期待。")
        title.setStyleSheet("font-size:24px;font-weight:600;margin:10px 0;")
        root.addWidget(title)
        form = QFormLayout()
        form.setSpacing(14)
        self.clock = QTimeEdit(QTime.fromString(config.off_time, "HH:mm"))
        self.clock.setDisplayFormat("HH:mm")
        form.addRow("平时几点下班", self.clock)
        days = QHBoxLayout()
        self.days = []
        for i, label in enumerate("一二三四五六日"):
            check = QCheckBox(label)
            check.setChecked(i in config.weekdays)
            self.days.append(check)
            days.addWidget(check)
        form.addRow("工作日", days)
        self.today = QCheckBox("今天使用临时时间（含非工作日）")
        self.today.setChecked(config.override_date == datetime.now().date().isoformat())
        self.today_clock = QTimeEdit(QTime.fromString(config.override_time, "HH:mm"))
        self.today_clock.setDisplayFormat("HH:mm")
        self.today_clock.setEnabled(self.today.isChecked())
        self.today.toggled.connect(self.today_clock.setEnabled)
        form.addRow(self.today)
        form.addRow("今天几点下班", self.today_clock)
        self.character = QComboBox()
        for identifier, label in CHARACTERS.items():
            self.character.addItem(QIcon(character_icon(identifier)), label, identifier)
        self.character.setCurrentIndex(self.character.findData(config.character))
        form.addRow('宠物形象', self.character)
        self.color = QComboBox()
        self.color.addItems(PALETTES)
        self.color.setCurrentText(config.color)
        form.addRow("气泡主题", self.color)
        self.scale = QSlider(Qt.Orientation.Horizontal)
        self.scale.setRange(50, 200)
        self.scale.setValue(config.scale)
        self.size_label = QLabel(f"{config.scale}%")
        self.scale.valueChanged.connect(lambda value: self.size_label.setText(f"{value}%"))
        size_row = QHBoxLayout()
        size_row.addWidget(self.scale)
        size_row.addWidget(self.size_label)
        form.addRow("宠物大小", size_row)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(20, 100)
        self.opacity.setValue(config.opacity)
        self.opacity_label = QLabel(f"{config.opacity}%")
        self.opacity.valueChanged.connect(lambda value: self.opacity_label.setText(f"{value}%"))
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity)
        opacity_row.addWidget(self.opacity_label)
        form.addRow("不透明度", opacity_row)
        self.bubble_mode = QComboBox()
        for label, mode in BUBBLE_MODES.items():
            self.bubble_mode.addItem(label, mode)
        self.bubble_mode.setCurrentIndex(self.bubble_mode.findData(config.bubble_mode))
        form.addRow("气泡展示方式", self.bubble_mode)
        self.bubble_seconds = QSpinBox()
        self.bubble_seconds.setRange(2, 30)
        self.bubble_seconds.setSuffix(" 秒")
        self.bubble_seconds.setValue(config.bubble_seconds)
        self.bubble_mode.currentIndexChanged.connect(lambda _: self.bubble_seconds.setEnabled(self.bubble_mode.currentData() in ("click", "hover_click")))
        self.bubble_seconds.setEnabled(config.bubble_mode in ("click", "hover_click"))
        form.addRow("点击后停留", self.bubble_seconds)
        self.checks = {}
        for key, label in (("topmost", "始终置顶"), ("interactive", "陪伴互动（目光跟随、摸头回应、歪头挥爪）"), ("reminders", "提前 30 分钟、10 分钟及到点提醒"), ("sound", "提醒时播放系统提示音"), ("click_through", "鼠标穿透（通过托盘菜单恢复）")):
            check = QCheckBox(label)
            check.setChecked(getattr(config, key))
            self.checks[key] = check
            form.addRow(check)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.checks["click_through"].setChecked(False)
            self.checks["click_through"].setEnabled(False)
        self.startup = QCheckBox("登录 Windows 时自动启动")
        self.startup.setChecked(startup_enabled())
        form.addRow(self.startup)
        root.addLayout(form)
        hint = QLabel("拖动移动 · 单击摸头 · 双击设置 · 右键菜单\nCtrl + 滚轮调大小，Ctrl + Shift + 滚轮调不透明度。\n鼠标穿透时不能拖动或点击；悬停气泡仍可显示。")
        hint.setStyleSheet("color:#8B837B;font-size:12px;margin:12px 0;")
        root.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def validate_and_accept(self):
        if not any(day.isChecked() for day in self.days):
            QMessageBox.warning(self, "选择工作日", "请至少选择一个工作日。")
            return
        self.accept()

    def result_config(self, old):
        return replace(old, off_time=self.clock.time().toString("HH:mm"),
                       weekdays=[i for i, day in enumerate(self.days) if day.isChecked()],
                       override_date=datetime.now().date().isoformat() if self.today.isChecked() else "",
                       override_time=self.today_clock.time().toString("HH:mm"), color=self.color.currentText(),
                       character=self.character.currentData(), scale=self.scale.value(), opacity=self.opacity.value(),
                       bubble_mode=self.bubble_mode.currentData(), bubble_seconds=self.bubble_seconds.value(),
                       **{key: check.isChecked() for key, check in self.checks.items()})


class Pet(QWidget):
    def __init__(self, config, persist=True):
        super().__init__()
        self.config = config
        self.persist = persist
        self.phase = 0
        self.drag = None
        self.press_position = None
        self.dragging = False
        self.hovered = False
        self.clicked_until = 0.0
        self.reaction_until = 0.0
        self.reaction_index = 0
        self.reaction_message = ""
        self.gaze = QPointF(0, 0)
        self.last_activity = monotonic()
        self.double_click = False
        self.dialog = None
        self.buddy_dialog = None
        self.remote_current = None
        self.remote_queue = None
        self.remote_started = 0.0
        self.remote_until = 0.0
        self.remote_message = ''
        self.remote_plays = deque(maxlen=3)
        self.current_relation_id = ''
        self.reminders = ReminderTracker()
        self.snapshot = countdown(datetime.now(), config)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(f"下班搭子 · v{__version__}")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.tray = QSystemTrayIcon(self)
        icon = QIcon(character_icon(config.character))
        self.setWindowIcon(icon)
        self.tray.setIcon(icon)
        self.menu = QMenu()
        self.menu.addAction("显示宠物 / 恢复位置", self.restore)
        self.menu.addAction("设置…", self.open_settings)
        self.menu.addAction('搭子…', self.open_buddy)
        self.pause_action = self.menu.addAction('暂停接收远程互动')
        self.pause_action.setCheckable(True)
        self.pause_action.setChecked(config.paused)
        self.pause_action.triggered.connect(self.set_paused)
        self.pass_action = self.menu.addAction("鼠标穿透")
        self.pass_action.setCheckable(True)
        self.pass_action.triggered.connect(self.toggle_passthrough)
        self.menu.addAction("隐藏宠物", self.hide)
        self.menu.addSeparator()
        self.menu.addAction("退出下班搭子", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        else:
            self.config.click_through = False
        self.apply_config()
        self.restore(initial=True)
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self.pet_cat)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.write_config)
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(1000)
        self.animation = QTimer(self)
        self.animation.timeout.connect(self.animate)
        self.animation.start(50)
        self.buddy = BuddyClient(self.config, self, persist=persist)
        self.buddy.changed.connect(self.buddy_changed)
        self.buddy.incoming.connect(self.receive_remote)
        self.buddy.outgoing.connect(self.send_animation)
        if self.config.network_enabled and persist:
            QTimer.singleShot(0, self.buddy.start)

    def open_buddy(self):
        if self.buddy_dialog is None:
            self.buddy_dialog = BuddyDialog(self)
        self.buddy_dialog.show()
        self.buddy_dialog.raise_()
        self.buddy_dialog.activateWindow()

    def buddy_changed(self):
        relation_id = self.buddy.relation['id'] if self.buddy.relation else ''
        if relation_id != self.current_relation_id or not self.buddy.ready:
            self.clear_remote()
            self.current_relation_id = relation_id

    def set_paused(self, paused):
        self.config.paused = paused
        self.pause_action.setChecked(paused)
        if paused:
            self.clear_remote()
        self.buddy.update_config(self.config)
        self.write_config()
        self.buddy.changed.emit()

    def clear_remote(self):
        self.remote_current = self.remote_queue = None
        self.remote_until = 0.0
        self.remote_message = ''
        self.update()

    def receive_remote(self, msg):
        import time
        valid = (self.buddy.ready and self.buddy.relation and not self.config.paused and self.isVisible()
                 and not self.dialog and msg.get('relation_id') == self.buddy.relation['id']
                 and msg.get('action') in ('pinch', 'bonk') and isinstance(msg.get('action_id'), str)
                 and type(msg.get('count')) is int and 1 <= msg['count'] <= 5
                 and type(msg.get('expires_at')) in (int, float)
                 and 0 < msg['expires_at'] - time.time() - self.buddy.clock_offset <= 6)
        if not valid:
            self.buddy.acknowledge(msg, False)
            return
        key = (msg['relation_id'], msg['action_id'])
        if key in self.buddy.received:
            self.buddy.acknowledge(msg, True)
            return
        if len(self.remote_plays) == 3 and monotonic() - self.remote_plays[0] < 8:
            self.buddy.acknowledge(msg, False)
            return
        if self.remote_queue and self.remote_queue['action'] != msg['action']:
            self.buddy.acknowledge(msg, False)
            return
        if self.remote_queue:
            self.remote_queue['count'] = min(5, self.remote_queue['count'] + msg['count'])
            self.remote_queue['expires_at'] = min(self.remote_queue['expires_at'], msg['expires_at'])
        else:
            self.remote_queue = dict(msg)
        self.buddy.received[key] = monotonic() + 30
        self.buddy.acknowledge(msg, True)
        self.advance_remote()

    def advance_remote(self):
        import time
        now = monotonic()
        if self.remote_current and now >= self.remote_until:
            self.remote_current = None
        if not self.remote_queue or self.remote_current:
            return
        queued = self.remote_queue
        if (not self.buddy.ready or not self.buddy.relation or queued['relation_id'] != self.buddy.relation['id']
                or queued['expires_at'] <= time.time() + self.buddy.clock_offset or self.config.paused or not self.isVisible()):
            self.remote_queue = None
            return
        if self.dragging or self.dialog or now < self.reaction_until:
            return
        if len(self.remote_plays) == 3 and now - self.remote_plays[0] < 8:
            self.remote_queue = None
            return
        self.remote_queue = None
        self.remote_plays.append(now)
        self.remote_current = {'action': 'receive_' + queued['action'], 'count': queued['count']}
        self.remote_started, self.remote_until = now, now + 1.1
        self.remote_message = ('被捏捏脸' if queued['action'] == 'pinch' else '被轻轻捶了一下') + f" ×{queued['count']}"
        if self.config.remote_sound:
            QApplication.beep()
        self.update()

    def send_animation(self, action, count):
        if self.dragging or self.dialog or monotonic() < self.reaction_until:
            return
        self.remote_current = {'action': 'send_' + action, 'count': count}
        self.remote_started = monotonic()
        self.remote_until = self.remote_started + 0.8
        self.remote_message = ''
        self.update()

    def hideEvent(self, event):
        if hasattr(self, 'buddy'):
            self.clear_remote()
            self.buddy.visible = False
            if self.buddy.ready:
                self.buddy.profile()
        super().hideEvent(event)

    def showEvent(self, event):
        if hasattr(self, 'buddy'):
            self.buddy.visible = True
            if self.buddy.ready:
                self.buddy.profile()
        super().showEvent(event)

    def write_config(self):
        if self.persist:
            try:
                save_config(self.config)
            except OSError as error:
                QMessageBox.warning(self, "保存失败", str(error))

    def apply_config(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.config.topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if self.config.click_through:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setFixedSize(round(280 * self.config.scale / 100), round(280 * self.config.scale / 100))
        self.setWindowOpacity(self.config.opacity / 100)
        icon = QIcon(character_icon(self.config.character))
        self.setWindowIcon(icon)
        self.tray.setIcon(icon)
        if hasattr(self, 'buddy'):
            self.buddy.update_config(self.config)
        self.hovered = False
        self.clicked_until = 0.0
        self.pass_action.setChecked(self.config.click_through)
        self.snapshot = countdown(datetime.now(), self.config)
        self.reminders = ReminderTracker()
        self.tick()
        self.show()

    def show_bubble(self, now=None):
        return bubble_visible(self.config.bubble_mode, self.hovered, self.clicked_until,
                              monotonic() if now is None else now)

    def cat_contains(self, point):
        scale = self.config.scale / 100
        x, y = point.x() / scale, point.y() / scale
        if self.config.character == 'lulu':
            return sprite_contains(QPointF(x, y))
        return (((x - 141) / 71) ** 2 + ((y - 181) / 52) ** 2 <= 1
                or QRectF(84, 117, 115, 34).contains(QPointF(x, y))
                or QRectF(86, 211, 111, 47).contains(QPointF(x, y))
                or QRectF(195, 199, 46, 54).contains(QPointF(x, y)))

    def pet_cat(self):
        now = monotonic()
        self.clicked_until = now + self.config.bubble_seconds
        if self.config.interactive:
            self.reaction_until = now + 2.5
            messages = ("收到你的摸摸啦", "你认真工作，我认真陪你", "累了就伸个懒腰吧", "有我在，慢慢来就好")
            self.reaction_message = messages[self.reaction_index % len(messages)]
            self.reaction_index += 1
        self.last_activity = now
        self.update()

    def restore(self, initial=False):
        if not initial:
            self.config.click_through = False
            self.apply_config()
        screens = QApplication.screens()
        point = QPoint(self.config.x or 0, self.config.y or 0)
        saved = self.config.x is not None and self.config.y is not None
        # Keep the entire pet inside one available desktop after display changes.
        screen = next((s for s in screens if s.availableGeometry().contains(point)), QApplication.primaryScreen())
        rect = screen.availableGeometry()
        if initial and saved and rect.contains(point):
            x, y = point.x(), point.y()
        else:
            x, y = rect.right() - self.width() - 36, rect.bottom() - self.height() - 24
        self.move(max(rect.left(), min(x, rect.right() - self.width() + 1)),
                  max(rect.top(), min(y, rect.bottom() - self.height() + 1)))
        self.show()

    def toggle_passthrough(self, checked):
        self.config.click_through = checked
        self.apply_config()
        self.write_config()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_settings()

    def open_settings(self):
        if self.dialog:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        original = replace(self.config)
        original_position = self.pos()
        self.save_timer.stop()
        self.click_timer.stop()
        self.dialog = SettingsDialog(self.config)
        self.clear_remote()
        self.dialog.scale.valueChanged.connect(self.resize_pet)
        self.dialog.opacity.valueChanged.connect(lambda value: self.setWindowOpacity(value / 100))
        accepted = False
        try:
            if self.dialog.exec() == QDialog.DialogCode.Accepted:
                accepted = True
                new_config = self.dialog.result_config(self.config)
                try:
                    if self.dialog.startup.isChecked() != startup_enabled():
                        set_startup(self.dialog.startup.isChecked())
                except OSError as error:
                    QMessageBox.warning(self.dialog, "开机启动设置失败", str(error))
                self.config = new_config
                self.apply_config()
                self.keep_on_screen()
                self.config.x, self.config.y = self.x(), self.y()
                self.write_config()
        finally:
            if not accepted:
                self.config = original
                self.apply_config()
                self.move(original_position)
            self.dialog.deleteLater()
            self.dialog = None

    def tick(self):
        self.snapshot = countdown(datetime.now(), self.config)
        crossed = self.reminders.update(self.snapshot)
        self.tray.setToolTip(f"下班搭子 · {self.snapshot.display}")
        if crossed and self.config.reminders:
            threshold = crossed[0]
            message = "今天辛苦了，收工回家！" if threshold == 0 else f"还有 {threshold // 60} 分钟就下班啦。"
            self.tray.showMessage("下班搭子", message, QSystemTrayIcon.MessageIcon.Information, 6000)
            if self.config.sound:
                QApplication.beep()
        self.update()

    def animate(self):
        if self.isVisible():
            self.phase += 1
            self.advance_remote()
            now = monotonic()
            local = self.mapFromGlobal(QCursor.pos())
            over_cat = self.cat_contains(local)
            over_bubble = self.show_bubble(now) and QRectF(20, 8, 240, 98).contains(QPointF(local) / (self.config.scale / 100))
            entered = over_cat and not self.hovered
            self.hovered = over_cat or over_bubble
            if entered:
                self.last_activity = now
            target_x = max(-1.0, min(1.0, (local.x() / (self.config.scale / 100) - 141) / 240))
            target_y = max(-1.0, min(1.0, (local.y() / (self.config.scale / 100) - 178) / 180))
            self.gaze += (QPointF(target_x, target_y) - self.gaze) * 0.18
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(self.config.scale / 100, self.config.scale / 100)
        _, cream = PALETTES[self.config.color]
        def text(rect, content, size, color, bold=False):
            p.setPen(QColor(color))
            p.setFont(QFont("Microsoft YaHei UI", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, content)
        now = monotonic()
        reacting = self.config.interactive and now < self.reaction_until
        if self.show_bubble(now):
            p.setPen(QPen(QColor("#E9E2D7"), 1))
            p.setBrush(QColor(cream))
            p.drawRoundedRect(QRectF(20, 8, 240, 94), 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([QPointF(124, 100), QPointF(140, 114), QPointF(151, 100)]))
            caption = "距离下班还有" if self.snapshot.state == "working" else "今天辛苦啦" if self.snapshot.state == "done" else "好好享受今天"
            text(QRectF(20, 16, 240, 25), caption, 10, "#9A8977")
            text(QRectF(20, 40, 240, 42), self.snapshot.display, 24, "#55483E", True)
            foot = "快了快了，准备收工" if self.snapshot.state == "working" and self.snapshot.seconds <= 1800 else "搭子陪你，慢慢来" if self.snapshot.state == "working" else "生活时间，现在开始"
            if self.config.interactive:
                if self.dragging:
                    foot = "我们要搬去哪里呀"
                elif reacting:
                    foot = self.reaction_message
                elif self.hovered:
                    foot = "我在呢，陪你一起等下班"
            text(QRectF(20, 79, 240, 18), foot, 8, "#A29180")
        action = self.remote_current['action'] if self.remote_current else ''
        if self.dragging or self.dialog or reacting:
            action = ''
        progress = min(1.0, max(0.0, (now - self.remote_started) / max(0.01, self.remote_until - self.remote_started)))
        draw_character(p, self.config.character, self.phase, self.gaze, self.hovered,
                       reacting or self.snapshot.state in ('done', 'rest') and not self.hovered,
                       self.dragging, action, progress, self.config.interactive,
                       reaction_progress=max(0, min(1, 1 - (self.reaction_until - now) / 2.5)) if reacting else None)
        if self.config.remote_text and self.remote_current and self.remote_message and action.startswith('receive_'):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#FFFCF6'))
            p.drawRoundedRect(QRectF(39, 267, 202, 13), 6, 6)
            text(QRectF(39, 265, 202, 15), self.remote_message, 8, '#9A745D')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = event.globalPosition().toPoint() - self.pos()
            self.press_position = event.globalPosition().toPoint()
            self.dragging = False

    def mouseMoveEvent(self, event):
        if self.drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.globalPosition().toPoint() - self.press_position).manhattanLength() >= QApplication.startDragDistance():
                self.dragging = True
                self.click_timer.stop()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self.dragging:
                self.move(event.globalPosition().toPoint() - self.drag)
                self.keep_on_screen(event.globalPosition().toPoint())
                self.last_activity = monotonic()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag is not None:
            was_dragging = self.dragging
            self.drag = None
            self.dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if was_dragging:
                self.config.x, self.config.y = self.x(), self.y()
                self.write_config()
            elif self.cat_contains(event.position()) and not self.double_click:
                # Wait out the double-click interval so opening settings doesn't pet.
                self.click_timer.start(QApplication.doubleClickInterval())
        self.double_click = False

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.click_timer.stop()
        self.double_click = True
        self.drag = None
        self.open_settings()

    def keep_on_screen(self, point=None):
        screen = QApplication.screenAt(point or self.frameGeometry().center()) or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        self.move(max(rect.left(), min(self.x(), rect.right() - self.width() + 1)),
                  max(rect.top(), min(self.y(), rect.bottom() - self.height() + 1)))

    def wheelEvent(self, event):
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        step = 5 if delta > 0 else -5
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.config.opacity = max(20, min(100, self.config.opacity + step))
            self.setWindowOpacity(self.config.opacity / 100)
        else:
            self.resize_pet(max(50, min(200, self.config.scale + step)))
        self.save_timer.start(350)
        event.accept()

    def resize_pet(self, scale):
        anchor = QPoint(self.x() + self.width() // 2, self.y() + self.height())
        self.config.scale = scale
        self.setFixedSize(round(280 * scale / 100), round(280 * scale / 100))
        self.move(anchor.x() - self.width() // 2, anchor.y() - self.height())
        self.keep_on_screen()
        self.config.x, self.config.y = self.x(), self.y()
        self.update()

    def contextMenuEvent(self, event):
        self.menu.exec(event.globalPos())

    def closeEvent(self, event):
        if self.tray.isVisible():
            event.ignore()
            self.hide()
        else:
            self.quit()

    def quit(self):
        self.buddy.stop()
        if self.buddy_dialog:
            self.buddy_dialog.hide()
        self.config.x, self.config.y = self.x(), self.y()
        self.write_config()
        self.tray.hide()
        QApplication.quit()


def main():
    import argparse
    import re
    parser = argparse.ArgumentParser(description='下班搭子 · Windows 桌面宠物')
    parser.add_argument('--profile', help='独立本机配置名，供双窗口联调使用')
    args = parser.parse_args()
    if args.profile:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', args.profile):
            parser.error('profile 只能使用 1–32 位字母、数字、下划线或短横线')
        os.environ['OFFWORK_HOME'] = str(data_dir() / 'profiles' / args.profile)
    app = QApplication(sys.argv)
    app.setApplicationName("OffworkPet")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    lock_dir = data_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_dir / "offwork-pet.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "下班搭子已运行", "宠物已经在桌面上啦，可以从任务栏托盘打开设置。")
        return
    config, warning = load_config()
    pet = Pet(config)
    if warning:
        QTimer.singleShot(500, lambda: QMessageBox.warning(pet, "配置提示", warning))
    app.aboutToQuit.connect(lock.unlock)
    sys.exit(app.exec())
