"""Desktop layout preview/editor. Draft handles never contain received messages."""
import math
import time

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .overlay_layout import ELEMENTS, copy_layout, element_settings
from .ui_theme import label, style_window
from .input_cursor import paint_caret


def element_screen(layout, key):
    name = element_settings(layout, key)['screen']
    return next((screen for screen in QApplication.screens() if screen.name() == name), QApplication.primaryScreen())


def display_settings(layout, key):
    settings = element_settings(layout, key)
    area = element_screen(layout, key).availableGeometry()
    settings['width'] = min(settings['width'], area.width())
    settings['height'] = min(settings['height'], area.height())
    return settings


def element_rect(layout, key, height):
    """Coordinates are local to the selected monitor, never relative to a pet."""
    settings = display_settings(layout, key)
    area = element_screen(layout, key).availableGeometry()
    spec = ELEMENTS[key]
    x = settings['x'] if settings['x'] is not None else round(area.width() * spec.default_x)
    y = settings['y'] if settings['y'] is not None else round(area.height() * spec.default_y)
    x = max(0, min(x, area.width() - settings['width']))
    y = max(0, min(y, area.height() - height))
    return QRect(area.left() + x, area.top() + y, settings['width'], min(height, area.height()))


def text_rows(bodies, settings):
    font = QFont('Microsoft YaHei UI', settings['font_size'])
    metrics = QFontMetricsF(font)
    flags = Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere
    rows, y = [], 0
    for body in bodies:
        height = math.ceil(max(20, metrics.boundingRect(QRectF(0, 0, settings['width'], 2000), flags, body).height())) + 8
        rows.append((QRectF(0, y, settings['width'], height), body))
        y += height + settings.get('gap', 0)
    return rows, max(28, y - settings.get('gap', 0))


def fit_messages(entries, settings):
    """Newest messages win; no hidden backlog when count or area is exceeded."""
    entries = entries[-settings.get('max_messages', 1):]
    rows, height = text_rows([body for body, _ in entries], settings)
    start = 0
    while len(entries) - start > 1 and height > settings['height']:
        height -= rows[start][0].height() + settings.get('gap', 0)
        start += 1
    return entries[start:]


def paint_text(painter, rows, settings, opacities=None, single_line=False):
    painter.setFont(QFont('Microsoft YaHei UI', settings['font_size']))
    ink = QColor(settings['color'])
    ink.setAlpha(round(255 * settings['opacity'] / 100))
    painter.setPen(ink)
    for index, (rect, body) in enumerate(rows):
        painter.setOpacity(opacities[index] if opacities is not None else 1)
        if single_line:
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, body)
        else:
            painter.drawText(rect.adjusted(0, 4, 0, -4), Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere, body)


def input_height(settings):
    return settings['height']


class PreviewHandle(QWidget):
    HEADER = 24

    def __init__(self, editor, key):
        super().__init__(editor, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        self.editor, self.key = editor, key
        self.offset = None
        self.fade = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def sample(self):
        settings = display_settings(self.editor.draft, self.key)
        sample = ELEMENTS[self.key].sample
        if ELEMENTS[self.key].single_line:
            metrics = QFontMetricsF(QFont('Microsoft YaHei UI', settings['font_size']))
            body = metrics.elidedText(sample[0], Qt.TextElideMode.ElideRight, settings['width'])
            height = input_height(settings)
            return settings, ([(QRectF(0, 0, settings['width'], height), body)], height)
        bodies = [sample[index % len(sample)] for index in range(settings.get('max_messages', 1))]
        bodies = [body for body, _ in fit_messages([(body, None) for body in bodies], settings)]
        rows, _ = text_rows(bodies, settings)
        return settings, (rows, settings['height'])

    def refresh(self):
        settings, (_, height) = self.sample()
        rect = element_rect(self.editor.draft, self.key, height)
        self.setGeometry(rect.adjusted(-1, -self.HEADER, 1, 1))
        self.update()

    def paintEvent(self, event):
        settings, (rows, _) = self.sample()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self.editor.current_key() == self.key
        painter.setPen(QPen(QColor('#8D80D6' if selected else '#667384'), 1, Qt.PenStyle.DashLine))
        painter.setBrush(QColor(24, 28, 36, 28))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))
        painter.setFont(QFont('Microsoft YaHei UI', 8))
        painter.setPen(QColor('#8D80D6' if selected else '#667384'))
        painter.drawText(QRect(6, 0, self.width() - 12, self.HEADER), Qt.AlignmentFlag.AlignVCenter,
                         ELEMENTS[self.key].title + ' · 拖动定位（预览）')
        painter.translate(1, self.HEADER)
        painter.setClipRect(QRectF(0, 0, settings['width'], settings['height']))
        paint_text(painter, rows, settings, [self.fade] * len(rows), ELEMENTS[self.key].single_line)
        if ELEMENTS[self.key].single_line:
            metrics = QFontMetricsF(QFont('Microsoft YaHei UI', settings['font_size']))
            x = min(settings['width'] - 4, metrics.horizontalAdvance(rows[0][1]) + 1)
            paint_caret(painter, QRectF(x, 0, 3, settings['height']), metrics.height())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.editor.select(self.key)
            self.offset = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            position = event.globalPosition().toPoint() - self.offset + QPoint(1, self.HEADER)
            # Crossing displays moves the element to that monitor.
            screen = QApplication.screenAt(event.globalPosition().toPoint()) or element_screen(self.editor.draft, self.key)
            area = screen.availableGeometry()
            values = self.editor.draft.setdefault(self.key, {})
            values.update(screen=screen.name(), x=max(0, position.x() - area.left()), y=max(0, position.y() - area.top()))
            self.editor.refresh_handles()
            self.editor.rebuild_form()
            event.accept()

    def mouseReleaseEvent(self, event):
        self.offset = None


class LayoutEditor(QDialog):
    def __init__(self, pet):
        super().__init__(pet, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.pet = pet
        self.draft = copy_layout(pet.config.desktop_elements)
        self.handles = {}
        self.controls = {}
        self.setWindowTitle('桌面布局 · 预览模式')
        self.resize(440, min(840, QApplication.primaryScreen().availableGeometry().height() - 60))
        style_window(self)
        self.setStyleSheet(self.styleSheet() + '''
            QDoubleSpinBox { background: #F8F9FB; color: #303645;
                border: 1px solid #E1E5ED; border-radius: 8px; padding: 8px; min-height: 18px; }
            QDoubleSpinBox:focus { border-color: #9B8ADB; background: white; }
        ''')
        root = QVBoxLayout(self)
        root.addWidget(label('拖动预览元素，放到你希望的位置。', 'sectionTitle'))
        root.addWidget(label('预览辅助框只在编辑时显示。这里使用示例文字，不会发消息；预览期间新消息直接忽略。保存后生效，取消放弃修改。', 'muted'))
        root.addWidget(label('总时长包含淡出，淡出设为 0 则直接消失。超出条数或区域高度时优先保留新消息；单条过长时裁切，不扩大区域。', 'muted'))
        self.selector = QComboBox()
        for key, spec in ELEMENTS.items():
            self.selector.addItem(spec.title, key)
        root.addWidget(self.selector)
        area = QScrollArea()
        area.setWidgetResizable(True)
        body = QWidget()
        self.form = QFormLayout(body)
        area.setWidget(body)
        root.addWidget(area, 1)
        buttons = QHBoxLayout()
        reset = QPushButton('重置当前元素')
        reset.clicked.connect(self.reset_current)
        buttons.addWidget(reset)
        effect = QPushButton('预览淡出效果')
        effect.clicked.connect(self.play_effect)
        buttons.addWidget(effect)
        root.addLayout(buttons)
        self.feedback = label('', 'feedback')
        root.addWidget(self.feedback)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Save).setText('保存布局')
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        actions.accepted.connect(self.save)
        actions.rejected.connect(self.reject)
        root.addWidget(actions)
        self.effect_timer = QTimer(self)
        self.effect_timer.setInterval(33)
        self.effect_timer.timeout.connect(self.advance_effect)
        self.selector.currentIndexChanged.connect(self.selection_changed)
        self.rebuild_form()

    def current_key(self):
        return self.selector.currentData()

    def select(self, key):
        self.selector.setCurrentIndex(self.selector.findData(key))

    def selection_changed(self):
        self.stop_effect()
        self.rebuild_form()
        self.refresh_handles()

    def rebuild_form(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self.controls = {}
        key = self.current_key()
        settings = element_settings(self.draft, key)
        rect = element_rect(self.draft, key, 28)
        area = element_screen(self.draft, key).availableGeometry()
        for name, parameter in ELEMENTS[key].parameters.items():
            value = settings[name]
            if parameter.kind == 'screen':
                widget = QComboBox()
                for index, screen in enumerate(QApplication.screens()):
                    widget.addItem(f'{index + 1} · {screen.name()}', screen.name())
                widget.setCurrentIndex(max(0, widget.findData(element_screen(self.draft, key).name())))
                widget.currentIndexChanged.connect(lambda _, w=widget, n=name: self.set_value(n, w.currentData()))
            elif parameter.kind == 'color':
                widget = QPushButton(value)
                widget.clicked.connect(self.pick_color)
            else:
                widget = QDoubleSpinBox() if parameter.kind == 'float' else QSpinBox()
                if parameter.kind == 'float':
                    widget.setDecimals(1)
                    widget.setSingleStep(.1)
                widget.setRange(parameter.minimum, parameter.maximum)
                if value is None:
                    value = rect.x() - area.left() if name == 'x' else rect.y() - area.top()
                widget.setValue(value)
                widget.valueChanged.connect(lambda v, n=name: self.set_value(n, v))
            self.controls[name] = widget
            self.form.addRow(parameter.title, widget)

    def set_value(self, name, value):
        self.draft.setdefault(self.current_key(), {})[name] = value
        self.stop_effect()
        self.refresh_handles()
        if name == 'screen':
            self.rebuild_form()

    def pick_color(self):
        settings = element_settings(self.draft, self.current_key())
        color = QColorDialog.getColor(QColor(settings['color']), self, '文字颜色')
        if color.isValid():
            self.set_value('color', color.name())
            self.controls['color'].setText(color.name())

    def reset_current(self):
        self.draft.pop(self.current_key(), None)
        self.stop_effect()
        self.rebuild_form()
        self.refresh_handles()

    def refresh_handles(self):
        for handle in self.handles.values():
            handle.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        for key in ELEMENTS:
            handle = self.handles.setdefault(key, None)
            if handle is None:
                handle = self.handles[key] = PreviewHandle(self, key)
            handle.refresh()
            handle.show()

    def play_effect(self):
        self.effect_started = time.monotonic()
        self.effect_timer.start()

    def advance_effect(self):
        elapsed = time.monotonic() - self.effect_started
        finished = True
        for key, handle in self.handles.items():
            settings = element_settings(self.draft, key)
            duration = settings.get('seconds', 5)
            fade = min(duration, settings.get('fade_seconds', 1))
            remaining = duration - elapsed
            handle.fade = (min(1, max(0, remaining / fade)) if fade else float(remaining > 0))
            finished &= remaining <= 0
            handle.update()
        if finished:
            self.effect_timer.stop()

    def stop_effect(self):
        self.effect_timer.stop()
        for handle in self.handles.values():
            handle.fade = 1
            handle.update()

    def save(self):
        previous = self.pet.config.desktop_elements
        self.pet.config.desktop_elements = copy_layout(self.draft)
        if self.pet.write_config() is False:
            self.pet.config.desktop_elements = previous
            self.feedback.setText('布局保存失败，请检查配置目录权限后重试。')
            return
        self.pet.chat.apply_layout()
        self.accept()

    def done(self, result):
        self.effect_timer.stop()
        for handle in self.handles.values():
            handle.hide()
        super().done(result)

    def closeEvent(self, event):
        self.reject()
        event.accept()
