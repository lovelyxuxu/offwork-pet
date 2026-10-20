"""Native transparent desktop cat, drawn with Qt (no external image assets)."""
from dataclasses import replace
from datetime import datetime
import math
from pathlib import Path
import sys
from time import monotonic

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTime, QTimer, QLockFile, QStandardPaths
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QMenu, QMessageBox,
    QSlider, QSpinBox, QSystemTrayIcon, QTimeEdit, QVBoxLayout, QWidget,
)

from .core import Config, ReminderTracker, bubble_visible, countdown, load_config, save_config

PALETTES = {"奶油橘": ("#F5C997", "#FFF0D9"), "薄荷绿": ("#A9D5BB", "#E8F6EC"), "云朵蓝": ("#B3CDEC", "#ECF3FF")}
BUBBLE_MODES = {"始终展示": "always", "鼠标悬停时展示": "hover", "点击小猫后展示": "click", "悬停或点击时展示": "hover_click", "始终隐藏": "hidden"}


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
        self.setWindowTitle("下班搭子 · 设置")
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
        self.color = QComboBox()
        self.color.addItems(PALETTES)
        self.color.setCurrentText(config.color)
        form.addRow("小猫配色", self.color)
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
                       scale=self.scale.value(), opacity=self.opacity.value(),
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
        self.reminders = ReminderTracker()
        self.snapshot = countdown(datetime.now(), config)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("下班搭子")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.tray = QSystemTrayIcon(self)
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#F5C997"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(5, 13, 54, 46)
        p.drawPolygon(QPolygonF([QPointF(8, 25), QPointF(9, 3), QPointF(28, 16)]))
        p.drawPolygon(QPolygonF([QPointF(36, 16), QPointF(55, 3), QPointF(56, 25)]))
        p.setBrush(QColor("#493D35"))
        p.drawEllipse(21, 32, 4, 6)
        p.drawEllipse(40, 32, 4, 6)
        p.end()
        self.setWindowIcon(QIcon(pix))
        self.tray.setIcon(QIcon(pix))
        self.menu = QMenu()
        self.menu.addAction("显示小猫 / 恢复位置", self.restore)
        self.menu.addAction("设置…", self.open_settings)
        self.pass_action = self.menu.addAction("鼠标穿透")
        self.pass_action.setCheckable(True)
        self.pass_action.triggered.connect(self.toggle_passthrough)
        self.menu.addAction("隐藏小猫", self.hide)
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
        return (((x - 141) / 71) ** 2 + ((y - 181) / 52) ** 2 <= 1
                or QRectF(84, 117, 115, 34).contains(QPointF(x, y))
                or QRectF(86, 211, 111, 47).contains(QPointF(x, y))
                or QRectF(195, 199, 46, 54).contains(QPointF(x, y)))

    def pet_cat(self):
        now = monotonic()
        self.clicked_until = now + self.config.bubble_seconds
        if self.config.interactive:
            self.reaction_until = now + 2.5
            messages = ("喵～收到你的摸摸啦", "你认真工作，我认真陪你", "累了就伸个懒腰吧", "有我在，慢慢来就好")
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
        fur, cream = PALETTES[self.config.color]
        ink = QColor("#55483E")
        def ellipse(x, y, w, h, color):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawEllipse(QRectF(x, y, w, h))
        def text(rect, content, size, color, bold=False):
            p.setPen(QColor(color))
            p.setFont(QFont("Microsoft YaHei UI", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, content)
        now = monotonic()
        reacting = self.config.interactive and now < self.reaction_until
        waving = self.config.interactive and (reacting or (self.phase % 760 < 55 and self.phase > 55))
        if self.show_bubble(now):
            p.setPen(QPen(QColor("#E9E2D7"), 1))
            p.setBrush(QColor("#FFFCF6"))
            p.drawRoundedRect(QRectF(20, 8, 240, 94), 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([QPointF(124, 100), QPointF(140, 114), QPointF(151, 100)]))
            caption = "距离下班还有" if self.snapshot.state == "working" else "今天辛苦啦" if self.snapshot.state == "done" else "好好享受今天"
            text(QRectF(20, 16, 240, 25), caption, 10, "#9A8977")
            text(QRectF(20, 40, 240, 42), self.snapshot.display, 24, "#55483E", True)
            foot = "快了快了，准备收工" if self.snapshot.state == "working" and self.snapshot.seconds <= 1800 else "小猫陪你，慢慢来" if self.snapshot.state == "working" else "生活时间，现在开始"
            if self.config.interactive:
                if self.dragging:
                    foot = "喵！我们要搬去哪里呀"
                elif reacting:
                    foot = self.reaction_message
                elif self.hovered:
                    foot = "我在呢，陪你一起等下班"
            text(QRectF(20, 79, 240, 18), foot, 8, "#A29180")
        ellipse(69, 251, 145, 14, "#20000000")
        p.save()
        p.translate(0, math.sin(self.phase / 14) * 2)
        if self.dragging and self.config.interactive:
            p.translate(141, 154)
            p.rotate(math.sin(self.phase / 3) * 6)
            p.translate(-141, -154)
        # Curled tail, body, paws, then a large expressive head.
        p.setPen(QPen(QColor(fur), 18, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        tail = QPainterPath(QPointF(192, 237))
        tail.cubicTo(240, 247, 239, 195 + math.sin(self.phase / (4 if reacting else 12)) * (12 if reacting else 7), 218, 207)
        p.drawPath(tail)
        ellipse(93, 186, 98, 70, fur)
        ellipse(113, 205, 57, 47, cream)
        ellipse(86, 241, 43, 17, fur)
        ellipse(154, 241, 43, 17, fur)
        # The head gently tilts towards the pointer, independently of the body.
        p.save()
        if self.config.interactive:
            tilt = self.gaze.x() * 5 if self.hovered else math.sin(self.phase / 42) * 3
            if reacting:
                tilt = math.sin(self.phase / 8) * 7
            p.translate(141, 196)
            p.rotate(tilt)
            p.translate(-141, -196)
        p.setPen(QPen(ink, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(QColor(fur))
        p.drawPolygon(QPolygonF([QPointF(85, 168), QPointF(84, 116), QPointF(123, 143)]))
        p.drawPolygon(QPolygonF([QPointF(161, 143), QPointF(199, 116), QPointF(198, 168)]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#E9A59C"))
        p.drawPolygon(QPolygonF([QPointF(92, 145), QPointF(91, 129), QPointF(108, 144)]))
        p.drawPolygon(QPolygonF([QPointF(177, 144), QPointF(192, 129), QPointF(192, 146)]))
        ellipse(75, 139, 132, 94, fur)
        ellipse(101, 181, 80, 45, cream)
        ellipse(86, 184, 22, 10, "#E8A499")
        ellipse(177, 184, 22, 10, "#E8A499")
        p.setPen(QPen(ink, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        blink = self.phase % 95 in (0, 1, 2, 3)
        happy = reacting or (self.snapshot.state in ("done", "rest") and not self.hovered)
        for x in (113, 169):
            if self.dragging and self.config.interactive:
                p.drawEllipse(QRectF(x - 4, 171, 8, 12))
            elif blink or happy:
                path = QPainterPath(QPointF(x - 5, 178))
                path.quadTo(x, 172 if happy else 180, x + 5, 178)
                p.drawPath(path)
            else:
                # Bright eyes and small pupils make the gaze readable at 100%.
                ellipse(x - 7, 169, 14, 19, "#FFF9EF")
                dx = self.gaze.x() * 3.5 if self.config.interactive else 0
                dy = self.gaze.y() * 3 if self.config.interactive else 0
                ellipse(x - 3 + dx, 173 + dy, 6, 10, "#55483E")
                ellipse(x - 1 + dx, 174 + dy, 2, 3, "#FFFFFF")
                p.setPen(QPen(ink, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        ellipse(138, 186, 7, 5, "#B27770")
        p.setPen(QPen(ink, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        mouth = QPainterPath(QPointF(132, 195))
        mouth.quadTo(137, 202, 142, 193)
        mouth.quadTo(147, 202, 152, 195)
        p.drawPath(mouth)
        for x, end in ((84, 67), (199, 216)):
            p.drawLine(QPointF(x, 185), QPointF(end, 181))
            p.drawLine(QPointF(x, 192), QPointF(end, 195))
        p.restore()
        if waving:
            p.save()
            p.translate(199, 221)
            p.rotate(-30 + math.sin(self.phase / 3) * 18)
            ellipse(-9, -27, 22, 39, fur)
            ellipse(-4, -24, 12, 13, "#E8A499")
            p.restore()
        p.restore()
        if reacting:
            p.save()
            p.translate(209, 139 - (1 - (self.reaction_until - now) / 2.5) * 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#E79799"))
            heart = QPainterPath(QPointF(0, 4))
            heart.cubicTo(-15, -6, -8, -15, 0, -8)
            heart.cubicTo(8, -15, 15, -6, 0, 4)
            p.drawPath(heart)
            p.restore()

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
        self.config.x, self.config.y = self.x(), self.y()
        self.write_config()
        self.tray.hide()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OffworkPet")
    app.setQuitOnLastWindowClosed(False)
    lock_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_dir / "offwork-pet.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "下班搭子已运行", "小猫已经在桌面上啦，可以从任务栏托盘打开设置。")
        return
    config, warning = load_config()
    pet = Pet(config)
    if warning:
        QTimer.singleShot(500, lambda: QMessageBox.warning(pet, "配置提示", warning))
    app.aboutToQuit.connect(lock.unlock)
    sys.exit(app.exec())
