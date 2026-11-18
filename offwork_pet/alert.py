"""Screen-edge alert flare: a system signal, independent of character packages.

The overlay is click-through and only repaints while a flare is active.
Global hotkeys use Windows RegisterHotKey so they work from any application.
"""
import ctypes
from ctypes import wintypes
import math
import sys
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .core import alert_buttons, parse_hotkey, notification_hotkeys

LEVELS = {
    'safe': ('安全', '#2FBF71'),
    'warning': ('警戒', '#FFAA1A'),
    'danger': ('危险', '#FF3B30'),
}
ATTACK = 0.18
RELEASE = 0.9


def alert_intensity(elapsed, duration, level):
    """Fade envelope with a breathing pulse for danger; 0 outside the flare."""
    if elapsed < 0 or elapsed >= duration:
        return 0.0
    attack = min(1.0, elapsed / ATTACK)
    release = min(1.0, max(0.0, (duration - elapsed) / RELEASE))
    envelope = attack * attack * (3 - 2 * attack) * release * release * (3 - 2 * release)
    if level == 'danger':
        return envelope * (0.72 + 0.28 * (0.5 + 0.5 * math.sin(2 * math.pi * 1.5 * elapsed - math.pi / 2)))
    if level == 'warning':
        return envelope * (0.9 + 0.1 * math.sin(2 * math.pi * 0.8 * elapsed))
    return envelope


class AlertOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.level = 'danger'
        self.label, self.color = LEVELS['danger']
        self.source = ''
        self.started = 0.0
        self.duration = 5.0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.advance)
        self._origin = None
        app = QApplication.instance()
        app.screenAdded.connect(self.sync_geometry)
        app.screenRemoved.connect(self.sync_geometry)

    def sync_geometry(self, *_):
        screens = QApplication.screens()
        if not screens:
            return
        geometry = screens[0].geometry()
        for screen in screens[1:]:
            geometry = geometry.united(screen.geometry())
        self._origin = geometry.topLeft()
        self.setGeometry(geometry)

    def trigger(self, level, seconds, source='', *, label=None, color=None):
        if level not in LEVELS:
            return
        self.level, self.source = level, source
        self.label = label or LEVELS[level][0]
        self.color = color or LEVELS[level][1]
        self.duration = max(0.5, float(seconds))
        self.started = time.monotonic()
        self.sync_geometry()
        if not self.isVisible():
            self.show()
        self.raise_()
        self.timer.start()
        self.update()

    def stop(self):
        self.timer.stop()
        self.hide()

    def advance(self):
        if time.monotonic() - self.started >= self.duration:
            self.stop()
        else:
            self.update()

    def paintEvent(self, event):
        elapsed = time.monotonic() - self.started
        intensity = alert_intensity(elapsed, self.duration, self.level)
        if intensity <= 0 or self._origin is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = QColor(self.color)
        for screen in QApplication.screens():
            rect = screen.geometry().translated(-self._origin.x(), -self._origin.y())
            self.paint_edge(painter, QRectF(rect), base, intensity)
        self.paint_caption(painter, base, intensity)

    def paint_edge(self, painter, rect, base, intensity):
        # A faint inward glow, noticeable only when looked for; never a heavy frame.
        for index in range(20):
            fraction = index / 19
            width = 1.5 + (1 - fraction) ** 2 * 30
            alpha = intensity * (0.02 + 0.24 * fraction ** 2)
            color = QColor(base)
            color.setAlphaF(min(1.0, alpha))
            painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = width / 2
            painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), 8, 8)
        color = QColor(base)
        color.setAlphaF(min(1.0, intensity * 0.42))
        painter.setPen(QPen(color, 1.5))
        painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), 6, 6)

    def caption_layout(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        rect = screen.availableGeometry().translated(-self._origin.x(), -self._origin.y())
        text = self.label + (f' · 来自 {self.source}' if self.source else '')
        font = QFont('Microsoft YaHei UI', 8)
        metrics = QFontMetricsF(font)
        width = max(28, min(metrics.horizontalAdvance(text) + 28, 320, rect.width() - 24))
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, width - 28)
        pill = QRectF(rect.right() - width - 12, rect.bottom() - 32, width, 22)
        return pill, font, text

    def paint_caption(self, painter, base, intensity):
        layout = self.caption_layout()
        if layout is None:
            return
        pill, font, text = layout
        painter.setPen(Qt.PenStyle.NoPen)
        scrim = QColor(15, 17, 23)
        scrim.setAlphaF(0.55 * intensity)
        painter.setBrush(scrim)
        painter.drawRoundedRect(pill, 8, 8)
        dot = QColor(base)
        dot.setAlphaF(0.85 * intensity)
        painter.setBrush(dot)
        painter.drawEllipse(QRectF(pill.left() + 9, pill.center().y() - 2.5, 5, 5))
        painter.setFont(font)
        ink = QColor(255, 255, 255)
        ink.setAlphaF(0.75 * intensity)
        painter.setPen(ink)
        painter.drawText(pill.adjusted(20, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        # RegisterHotKey(None, ...) posts thread messages, dispatched by Qt as
        # windows_dispatcher_MSG rather than the normal window message type.
        if event_type in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam in self.owner.ids:  # WM_HOTKEY
                self.owner.activated.emit(self.owner.ids[msg.wParam])
                return True, 0
        return False, 0


class GlobalHotkeys(QObject):
    activated = Signal(str)

    def __init__(self):
        super().__init__()
        self.ids = {}
        self.failed = []
        self._filter = None

    def register(self, mapping):
        self.unregister()
        self.failed = []
        app = QApplication.instance()
        self._filter = _HotkeyFilter(self)
        app.installNativeEventFilter(self._filter)
        user32 = ctypes.windll.user32
        for index, (level, text) in enumerate(sorted(mapping.items())):
            try:
                mods, vk = parse_hotkey(text)
            except ValueError:
                self.failed.append((level, text))
                continue
            hotkey_id = 0xA171 + index
            # MOD_NOREPEAT: holding the key must not machine-gun the flare.
            if user32.RegisterHotKey(None, hotkey_id, mods | 0x4000, vk):
                self.ids[hotkey_id] = level
            else:
                self.failed.append((level, text))
        return self.failed

    def unregister(self):
        if self._filter is not None:
            app = QApplication.instance()
            if app:
                app.removeNativeEventFilter(self._filter)
            self._filter = None
        for hotkey_id in self.ids:
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
        self.ids = {}


class AlertController(QObject):
    notice = Signal(str)
    shortcuts_changed = Signal()
    chat_requested = Signal()
    mute_requested = Signal()

    def __init__(self, config, buddy, parent=None, enable_hotkeys=True):
        super().__init__(parent)
        self.config, self.buddy = config, buddy
        self.overlay = AlertOverlay()
        self.received = {}
        self.hotkeys = None
        self._registered_hotkeys = None
        if enable_hotkeys and sys.platform == 'win32' and QApplication.platformName() != 'offscreen':
            self.hotkeys = GlobalHotkeys()
            self.hotkeys.activated.connect(self.activate_hotkey)
            self.register_hotkeys()
        buddy.rally_event.connect(self.receive)

    def register_hotkeys(self):
        mapping = notification_hotkeys(self.config)
        if self.hotkeys:
            self.hotkeys.register(mapping)
        self._registered_hotkeys = mapping
        self.shortcuts_changed.emit()

    def update_config(self, config):
        self.config = config
        mapping = notification_hotkeys(config)
        if self.hotkeys and mapping != self._registered_hotkeys:
            self.register_hotkeys()
        if config.notifications_muted:
            self.overlay.stop()
        self.shortcuts_changed.emit()

    def activate_hotkey(self, identifier):
        if identifier == '__chat':
            self.chat_requested.emit()
        elif identifier == '__mute':
            self.mute_requested.emit()
        else:
            self.trigger(identifier)

    def trigger(self, identifier, broadcast=True):
        button = next((button for button in alert_buttons(self.config) if button['id'] == identifier), None)
        if button is None:
            return
        level = button['level']
        if not self.config.notifications_muted:
            self.overlay.trigger(level, self.config.alert_seconds, label=button['name'], color=button['color'])
        if not broadcast:
            return
        if self.buddy.ready and self.buddy.rally and self.buddy.rally_supported:
            if 'rally-signals/v1' in self.buddy.service_features:
                self.buddy.rally_alert(level, label=button['name'], color=button['color'])
            else:
                self.buddy.rally_alert(level)
                if (button['name'], button['color']) != LEVELS[level]:
                    self.notice.emit('当前服务按默认级别广播，自定义文字与颜色需要服务升级')
        else:
            self.notice.emit('已在本机展示 · 加入集会后可广播给成员')

    def receive(self, msg):
        if msg.get('type') != 'rally_alert':
            return
        level = msg.get('level')
        rally = self.buddy.rally
        expires = msg.get('expires_at')
        alert_id = msg.get('alert_id')
        if (self.config.notifications_muted or level not in LEVELS or not self.config.rally_receive or self.config.paused or not rally
                or msg.get('rally_id') != rally['id'] or not isinstance(alert_id, str)
                or type(expires) not in (int, float)
                or not 0 < expires - time.time() - self.buddy.clock_offset <= 7):
            return
        now = time.monotonic()
        self.received = {key: expiry for key, expiry in self.received.items() if expiry > now}
        key = (msg['rally_id'], alert_id)
        if key in self.received:
            return
        self.received[key] = now + 30
        source = msg.get('from')
        source = source[:24] if isinstance(source, str) else ''
        label, color = msg.get('label'), msg.get('color')
        from .core import validate_alert_buttons
        if label is not None or color is not None:
            try:
                validate_alert_buttons([{'id': 'received', 'name': label, 'color': color, 'level': level}])
            except ValueError:
                return
        self.overlay.trigger(level, self.config.alert_seconds, source, label=label, color=color)
        if self.config.alert_sound and level == 'danger':
            QApplication.beep()

    def shutdown(self):
        if self.hotkeys:
            self.hotkeys.unregister()
        self.overlay.stop()
        self.overlay.close()
        self.overlay.deleteLater()
        self.received.clear()
