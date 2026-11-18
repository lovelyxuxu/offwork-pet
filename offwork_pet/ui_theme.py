"""Shared desktop surfaces, with quiet colors and generous touch targets."""
from pathlib import Path
import weakref
from shiboken6 import isValid
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFont, QWheelEvent
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox,
    QApplication, QComboBox, QFrame, QLabel, QScrollArea, QScrollBar, QVBoxLayout, QWidget)


class InputWheelGuard(QObject):
    """Only a deliberately clicked, still-focused value control may wheel-edit."""
    def __init__(self, app):
        super().__init__(app)
        self.armed = None

    @staticmethod
    def control(widget):
        while isinstance(widget, QWidget):
            if isinstance(widget, (QAbstractSpinBox, QComboBox, QAbstractSlider)) and not isinstance(widget, QScrollBar):
                return widget
            widget = widget.parentWidget()
        return None

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn, QEvent.Type.FocusOut, QEvent.Type.Wheel, QEvent.Type.Polish):
            return False
        control = self.control(watched)
        if kind == QEvent.Type.Polish and control is watched:
            control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        elif kind == QEvent.Type.MouseButtonPress:
            if control is not None:
                self.armed = weakref.ref(control)
            else:
                armed = self.armed() if self.armed else None
                # The same unhandled click can bubble through the field's
                # parents. It must not immediately disarm the clicked field.
                if not (armed is not None and isValid(armed) and isinstance(watched, QWidget) and watched.isAncestorOf(armed)
                        and armed.rect().contains(armed.mapFromGlobal(event.globalPosition().toPoint()))):
                    self.armed = None
        elif kind == QEvent.Type.FocusOut:
            if (control is not None and self.armed and self.armed() is control
                    and event.reason() != Qt.FocusReason.PopupFocusReason
                    and (not isinstance(control, QComboBox) or event.reason() == Qt.FocusReason.ActiveWindowFocusReason)):
                self.armed = None
        elif kind == QEvent.Type.FocusIn and event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self.armed = None
        elif kind == QEvent.Type.Wheel and control is not None:
            focus = QApplication.focusWidget()
            if self.armed and self.armed() is control and focus is not None and (focus is control or control.isAncestorOf(focus)):
                return False
            # Forward directly to the surrounding page; never let hovering a
            # value field consume the scroll or change its value.
            parent = control.parentWidget()
            while parent is not None and not isinstance(parent, QAbstractScrollArea):
                parent = parent.parentWidget()
            if parent is not None:
                viewport = parent.viewport()
                forwarded = QWheelEvent(viewport.mapFromGlobal(event.globalPosition().toPoint()).toPointF(),
                    event.globalPosition(), event.pixelDelta(), event.angleDelta(), event.buttons(),
                    event.modifiers(), event.phase(), event.inverted())
                QApplication.sendEvent(viewport, forwarded)
                event.accept()
            else:
                event.ignore()
            return True
        return False


def protect_input_wheels():
    app = QApplication.instance()
    if not hasattr(app, '_input_wheel_guard'):
        app._input_wheel_guard = InputWheelGuard(app)
        app.installEventFilter(app._input_wheel_guard)


STYLE = '''
QDialog, QWidget#panelSurface { background: #F5F6F8; color: #222735; }
QWidget { font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }
QLabel { color: #303645; background: transparent; }
QLabel#heading { font-size: 23px; font-weight: 700; color: #202533; }
QLabel#subtitle, QLabel#muted { color: #7A8293; font-size: 12px; }
QLabel#sectionTitle { font-size: 14px; font-weight: 600; }
QLabel#feedback { color: #6752C9; background: #EEEAFB; padding: 10px; border-radius: 10px; }
QFrame#card { background: #FFFFFF; border: 1px solid #E6E8EE; border-radius: 14px; }
QPushButton { color: #42495A; background: #FFFFFF; border: 1px solid #E1E4EC;
  border-radius: 9px; padding: 9px 14px; font-weight: 500; }
QPushButton:hover { background: #F0EDF9; border-color: #C6BDEB; color: #6250B3; }
QPushButton:pressed { background: #E5DEF6; }
QPushButton:disabled { color: #A3A9B5; background: #F0F2F5; border-color: #E9EBF0; }
QPushButton[variant="primary"] { color: white; background: #7964CB; border: 1px solid #7964CB; }
QPushButton[variant="primary"]:hover { background: #6953BC; }
QPushButton[variant="primary"]:disabled { background: #C6BFDA; border-color: #C6BFDA; }
QPushButton[variant="quiet"] { background: transparent; border-color: transparent; color: #737D90; }
QPushButton[variant="danger"] { color: #BB5F6B; }
QPushButton[segment="true"] { border: none; background: transparent; color: #808797; }
QPushButton[segment="true"]:checked { background: white; color: #6954BE; }
QFrame#segments { background: #EAECEF; border-radius: 11px; }
QPushButton#actionTile { text-align: left; padding: 14px; min-height: 26px; font-weight: 600; }
QLineEdit, QTimeEdit, QSpinBox, QComboBox { background: #F8F9FB; color: #303645;
 border: 1px solid #E1E5ED; border-radius: 8px; padding: 8px; min-height: 18px; selection-background-color: #7964CB; }
QLineEdit:focus, QTimeEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #9B8ADB; background: white; }
QLineEdit:disabled, QComboBox:disabled { color: #A0A6B0; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #EDE8FA; selection-color: #6954BE; padding: 5px; }
QCheckBox { color: #525B6E; spacing: 9px; padding: 5px 0; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #C9CDDA; border-radius: 5px; background: white; }
QCheckBox::indicator:checked { background: #7964CB; border-color: #7964CB; }
QCheckBox::indicator:disabled { background: #E2E5EC; }
QTabWidget::pane { border: none; background: transparent; padding-top: 12px; }
QTabBar::tab { background: transparent; color: #7C8494; border: none; padding: 11px 25px; }
QTabBar::tab:selected { color: #6B55BF; background: #EBE6F8; border-radius: 9px; }
QScrollArea, QScrollArea > QWidget > QWidget { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 3px 0; }
QScrollBar::handle:vertical { background: #CDD1DC; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSlider::groove:horizontal { background: #E6E8EF; height: 5px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #A08EDC; border-radius: 2px; }
QSlider::handle:horizontal { background: #7964CB; width: 15px; margin: -5px 0; border-radius: 7px; }
QListWidget { background: #FFFFFF; border: 1px solid #E6E8EE; border-radius: 12px; padding: 7px; outline: none; }
QListWidget::item { padding: 13px 9px; border-radius: 8px; margin: 2px 0; color: #525B6E; }
QListWidget::item:selected { background: #EEE9FB; color: #6954BE; }
QMenu { background: white; color: #42495A; border: 1px solid #E1E4EC; padding: 6px; border-radius: 10px; }
QMenu::item { padding: 9px 22px; border-radius: 6px; }
QMenu::item:selected { background: #EEE9FB; color: #6954BE; }
'''

_icons = Path(__file__).resolve().parent / 'assets' / 'ui'
STYLE += '''
QComboBox::down-arrow { image: url("%s"); width: 13px; height: 13px; }
QAbstractSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 23px; border: none; background: transparent; }
QAbstractSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 23px; border: none; background: transparent; }
QAbstractSpinBox::up-arrow { image: url("%s"); width: 11px; height: 11px; }
QAbstractSpinBox::down-arrow { image: url("%s"); width: 11px; height: 11px; }
QCheckBox::indicator:checked { image: url("%s"); }
''' % tuple((_icons / name).as_posix() for name in ('chevron-down.svg', 'chevron-up.svg', 'chevron-down.svg', 'check.svg'))


def style_window(window):
    # QApplication owns the shared style; short-lived per-widget styles can crash Qt on exit.
    app = QApplication.instance()
    protect_input_wheels()
    if app.style().objectName().lower() != 'fusion':
        app.setStyle('Fusion')
    window.setStyleSheet(STYLE)
    window.setFont(QFont('Microsoft YaHei UI', 10))


def label(text, kind=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if kind:
        widget.setObjectName(kind)
    return widget


def card(title=None, subtitle=None):
    frame = QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    if title:
        layout.addWidget(label(title, 'sectionTitle'))
    if subtitle:
        layout.addWidget(label(subtitle, 'muted'))
    return frame, layout


def scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(widget)
    return area
