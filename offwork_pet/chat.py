"""Ephemeral group text; only the active overlay owns received message bodies."""
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

from . import chat_history
from .input_cursor import StealthLineEdit
from .layout_ui import LayoutEditor, display_settings, element_rect, fit_messages, input_height, paint_text, text_rows
from .ui_theme import label


class MessageOverlay(QWidget):
    """Separate native window: never intercept clicks or take keyboard focus."""
    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.entries = []
        self.hovering = False
        self.last_tick = time.monotonic()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.advance)

    def push(self, source, text, seconds=None):
        settings = display_settings(self.pet.config.desktop_elements, 'chat_messages')
        count = settings['max_messages']
        if not self.entries:
            self.last_tick = time.monotonic()
        self.entries = (self.entries + [(source, text, time.monotonic() + (seconds or settings['seconds']))])[-count:]
        self.advance()
        self.show()
        self.timer.start()

    def clear(self):
        self.entries.clear()
        self.hovering = False
        self.last_tick = time.monotonic()
        self.timer.stop()
        self.hide()
        self.update()

    def layout_rows(self):
        settings = display_settings(self.pet.config.desktop_elements, 'chat_messages')
        bodies = [(f'{entry[0]}：{entry[1]}', entry) for entry in self.entries]
        fitted = fit_messages(bodies, settings)
        self.entries = [entry for _, entry in fitted]
        rows, _ = text_rows([body for body, _ in fitted], settings)
        return [(rect, body, entry[2]) for (rect, body), entry in zip(rows, self.entries)], settings['height']

    def advance(self):
        now = time.monotonic()
        elapsed = max(0, now - self.last_tick)
        self.last_tick = now
        if self.hovering:
            self.entries = [(source, text, expiry + elapsed) for source, text, expiry in self.entries]
        self.entries = [entry for entry in self.entries if entry[2] > now]
        if not self.entries:
            self.clear()
            return
        _, height = self.layout_rows()
        self.setGeometry(element_rect(self.pet.config.desktop_elements, 'chat_messages', height))
        settings = display_settings(self.pet.config.desktop_elements, 'chat_messages')
        metrics = QFontMetricsF(QFont('Microsoft YaHei UI', settings['font_size']))
        position = self.mapFromGlobal(QCursor.pos())
        self.hovering = self.isVisible() and self.rect().contains(position) and any(metrics.boundingRect(rect.adjusted(0, 4, 0, -4),
            Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere, body).contains(position)
            for rect, body, _ in self.layout_rows()[0])
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        settings = display_settings(self.pet.config.desktop_elements, 'chat_messages')
        rows = self.layout_rows()[0]
        fade = min(settings['fade_seconds'], settings['seconds'])
        now = time.monotonic()
        opacities = [1 if self.hovering else (min(1, max(0, (expiry - now) / fade)) if fade else float(expiry > now))
                     for _, _, expiry in rows]
        paint_text(painter, [(rect, body) for rect, body, _ in rows], settings, opacities)


class ChatInput(QDialog):
    def __init__(self, controller):
        super().__init__(controller.pet, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.controller = controller
        self.setWindowTitle('即时消息 · 当前集会')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setObjectName('stealthChatInput')
        self.setFixedWidth(360)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.input = StealthLineEdit()
        self.input.setFrame(False)
        self.input.setFont(QFont('Microsoft YaHei UI', 10))
        self.input.setFixedHeight(28)
        self.input.setMaxLength(200)
        self.input.returnPressed.connect(controller.send)
        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.setInterval(100)
        self.dismiss_timer.timeout.connect(self.dismiss_if_unfocused)
        self.input.focus_left.connect(self.focus_left)
        self.input.composition_changed.connect(lambda _: self.dismiss_timer.start()
            if self.isVisible() and not self.input.hasFocus() else None)
        QApplication.instance().focusChanged.connect(self.focus_changed)
        root.addWidget(self.input)
        self.feedback = label('', 'feedback')
        self.feedback.setFont(QFont('Microsoft YaHei UI', 8))
        self.feedback.hide()
        root.addWidget(self.feedback)
        self.feedback_timer = QTimer(self)
        self.feedback_timer.setSingleShot(True)
        self.feedback_timer.timeout.connect(lambda: self.set_feedback(''))
        self.setFixedHeight(28)
        self.apply_layout()

    def focus_left(self, reason):
        if self.isVisible() and reason != Qt.FocusReason.PopupFocusReason:
            self.dismiss_timer.start()

    def focus_changed(self, old, new):
        if not self.isVisible():
            return
        if new is self.input or (new is not None and self.isAncestorOf(new)):
            self.dismiss_timer.stop()
        elif new is not None and new.window() is not self:
            self.input.last_focus_reason = Qt.FocusReason.OtherFocusReason
            self.dismiss_timer.start()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowDeactivate and hasattr(self, 'dismiss_timer'):
            self.dismiss_timer.start()

    def dismiss_if_unfocused(self):
        if not self.isVisible() or self.input.hasFocus():
            return
        popup = QApplication.activePopupWidget()
        if popup is not None and (popup is self or self.isAncestorOf(popup)):
            return
        # Native IME candidate popups can temporarily take focus during preedit.
        if self.input.composing and self.input.last_focus_reason == Qt.FocusReason.PopupFocusReason:
            return
        self.reject()

    def apply_layout(self):
        layout = self.controller.pet.config.desktop_elements
        settings = display_settings(layout, 'chat_input')
        color = QColor(settings['color'])
        ink = f'rgba({color.red()}, {color.green()}, {color.blue()}, {round(settings["opacity"] * 255 / 100)})'
        self.setStyleSheet(f'''
            QDialog#stealthChatInput {{ background: transparent; border: none; }}
            QLineEdit {{ background: transparent; border: none; border-radius: 0;
                         padding: 0; color: {ink}; selection-background-color: rgba(120,128,140,55); }}
            QLabel {{ background: transparent; border: none; color: {ink}; }}
        ''')
        self.input.setFont(QFont('Microsoft YaHei UI', settings['font_size']))
        self.input.setFixedHeight(input_height(settings))
        self.setFixedWidth(settings['width'])
        self.setFixedHeight(self.input.height() + (24 if self.feedback.text() else 0))
        self.setGeometry(element_rect(layout, 'chat_input', self.height()))

    def set_feedback(self, text):
        self.feedback_timer.stop()
        self.feedback.setText(text)
        self.feedback.setVisible(bool(text))
        self.apply_layout()
        if text:
            self.feedback_timer.start(4000)

    def done(self, result):
        self.dismiss_timer.stop()
        self.input.clear()
        self.set_feedback('')
        super().done(result)
        # Hiding can commit an active IME preedit through QLineEdit.focusOutEvent.
        # Clear once more after that transition so no hidden draft survives.
        self.input.clear()
        self.input.composing = False

    def closeEvent(self, event):
        self.dismiss_timer.stop()
        self.input.clear()
        self.set_feedback('')
        super().closeEvent(event)


class SendFailureNotice(QWidget):
    """Short-lived plain text at the input position, without activating a window."""
    def __init__(self, pet):
        super().__init__(pet, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        self.pet, self.text = pet, ''
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.clear)

    def show_error(self, message):
        self.text = '发送失败：' + str(message).replace('\n', ' ')[:100]
        settings = display_settings(self.pet.config.desktop_elements, 'chat_input')
        _, height = text_rows([self.text], settings)
        self.setGeometry(element_rect(self.pet.config.desktop_elements, 'chat_input', min(height, 100)))
        self.show()
        self.update()
        self.timer.start(4000)

    def clear(self):
        self.timer.stop()
        self.text = ''
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        settings = display_settings(self.pet.config.desktop_elements, 'chat_input')
        paint_text(painter, text_rows([self.text], settings)[0], settings)


class ChatController(QObject):
    def __init__(self, pet):
        super().__init__(pet)
        self.pet, self.buddy = pet, pet.buddy
        self.overlay = MessageOverlay(pet)
        self.composer = ChatInput(self)
        self.failure_notice = SendFailureNotice(pet)
        self.closed = False
        self.seen = {}
        self.layout_editor = None
        self.buddy.chat_event.connect(self.receive)
        self.buddy.changed.connect(self.state_changed)

    def state_changed(self):
        rally_id = (self.buddy.rally or {}).get('id')
        if not self.buddy.ready or rally_id != getattr(self, 'rally_id', None):
            self.overlay.clear()
            self.seen.clear()
            self.composer.reject()
        self.rally_id = rally_id

    def open(self):
        self.failure_notice.clear()
        if self.layout_editor and self.layout_editor.isVisible():
            self.layout_editor.raise_()
            self.layout_editor.activateWindow()
            return
        self.pet.interrupt_autonomy()
        self.composer.set_feedback('' if self.buddy.ready and self.buddy.rally else '请先联网并加入集会')
        self.composer.show()
        self.composer.apply_layout()
        self.composer.raise_()
        self.composer.activateWindow()
        self.composer.input.setFocus()

    def open_layout(self):
        self.failure_notice.clear()
        if self.layout_editor and self.layout_editor.isVisible():
            self.layout_editor.raise_()
            self.layout_editor.activateWindow()
            return
        self.overlay.clear()
        self.composer.reject()
        self.pet.interrupt_autonomy()
        if self.layout_editor:
            self.layout_editor.deleteLater()
        self.layout_editor = LayoutEditor(self.pet)
        self.layout_editor.show()

    def apply_layout(self):
        settings = display_settings(self.pet.config.desktop_elements, 'chat_messages')
        self.overlay.entries = self.overlay.entries[-settings['max_messages']:]
        self.overlay.advance()
        self.composer.apply_layout()

    def send(self):
        text = self.composer.input.text().strip()
        if not text:
            return
        if not self.buddy.ready or not self.buddy.rally or 'rally-chat/v1' not in self.buddy.service_features:
            self.composer.set_feedback('当前服务未就绪或不支持即时消息')
            return
        if len(text) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in text):
            self.composer.set_feedback('请输入 1–200 字的单行文字')
            return
        # Clear immediately; no retry buffer or message history, including failures.
        self.composer.input.clear()
        self.composer.accept()
        rally = self.buddy.rally
        snapshot = (text, rally['id'], rally.get('name') or '集会',
                    next((member['name'] for member in rally.get('members') or [] if member.get('self')), '我'))
        self.buddy.rally_chat(text, lambda result: self.sent(result, snapshot))

    def sent(self, result, snapshot=None):
        if self.closed:
            return
        if result.get('ok'):
            if snapshot:
                text, rally_id, rally_name, self_name = snapshot
                chat_history.append('我', rally_name, text)
                if (not self.pet.config.notifications_muted and not self.pet.config.paused
                        and self.buddy.rally and self.buddy.rally['id'] == rally_id
                        and not (self.layout_editor and self.layout_editor.isVisible())):
                    self.overlay.push(self_name, text)
            return
        # A failed request must not unexpectedly reopen/focus the input.
        if self.pet.rally_dialog:
            self.pet.rally_dialog.show_notice(result.get('message', '未确认转发，不会自动重发。'))
        if not self.pet.config.notifications_muted and not self.pet.config.paused:
            self.failure_notice.show_error(result.get('message') or '未确认转发，不会自动重发。')

    def receive(self, msg):
        config = self.pet.config
        if (config.notifications_muted or config.paused or not self.buddy.ready or not self.buddy.rally
                or (self.layout_editor and self.layout_editor.isVisible())):
            return
        text, source, identifier, expires = (msg.get(key) for key in ('text', 'from', 'message_id', 'expires_at'))
        if (msg.get('type') != 'rally_chat' or msg.get('rally_id') != self.buddy.rally['id']
                or not isinstance(text, str) or not 1 <= len(text) <= 200
                or any(ord(char) < 32 or ord(char) == 127 for char in text)
                or not isinstance(source, str) or not 1 <= len(source) <= 64
                or not isinstance(identifier, str) or not 1 <= len(identifier) <= 64
                or type(expires) not in (int, float)):
            return
        remaining = expires - time.time() - self.buddy.clock_offset
        if not 0 < remaining <= 6:
            return
        now = time.monotonic()
        self.seen = {key: expiry for key, expiry in self.seen.items() if expiry > now}
        if identifier in self.seen:
            return
        self.seen[identifier] = now + 30  # Deduplication IDs only.
        self.overlay.push(source, text)
        chat_history.append(source, self.buddy.rally.get('name') or '集会', text)

    def shutdown(self):
        self.closed = True
        self.failure_notice.clear()
        if self.layout_editor:
            self.layout_editor.reject()
        self.overlay.clear()
        self.overlay.close()
        self.composer.reject()
        self.seen.clear()
