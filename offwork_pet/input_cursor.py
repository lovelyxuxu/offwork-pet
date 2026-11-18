"""Visible input caret, independent of the opacity of stealth chat text."""
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QLineEdit


def paint_caret(painter, rect, font_height):
    height = min(rect.height() - 2, font_height + 2)
    top = rect.center().y() - height / 2
    # QLineEdit's cursorRect includes horizontal margins and can start at -3
    # for an empty edit; its center is the actual insertion position.
    x = max(2, rect.left() + rect.width() / 2)
    start, end = QPointF(x, top), QPointF(x, top + height)
    painter.setOpacity(1)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(0, 0, 0, 255), 2))
    painter.drawLine(start, end)


class StealthLineEdit(QLineEdit):
    focus_left = Signal(object)
    composition_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.caret_on = True
        self.composing = False
        self.last_focus_reason = Qt.FocusReason.OtherFocusReason
        self.caret_timer = QTimer(self)
        self.caret_timer.setInterval(500)
        self.caret_timer.timeout.connect(self.blink_caret)
        self.cursorPositionChanged.connect(self.reset_caret)
        self.textEdited.connect(self.reset_caret)
        self.selectionChanged.connect(self.reset_caret)

    def reset_caret(self, *args):
        self.caret_on = True
        if self.isVisible() and self.hasFocus():
            self.caret_timer.start()
        self.update()

    def blink_caret(self):
        self.caret_on = not self.caret_on
        self.update()

    def focusInEvent(self, event):
        self.last_focus_reason = event.reason()
        super().focusInEvent(event)
        self.reset_caret()

    def focusOutEvent(self, event):
        self.last_focus_reason = event.reason()
        self.caret_timer.stop()
        super().focusOutEvent(event)
        self.focus_left.emit(event.reason())
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.reset_caret()

    def hideEvent(self, event):
        self.caret_timer.stop()
        super().hideEvent(event)

    def inputMethodEvent(self, event):
        super().inputMethodEvent(event)
        self.composing = bool(event.preeditString())
        self.composition_changed.emit(self.composing)
        self.reset_caret()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.hasFocus() and self.caret_on and not self.hasSelectedText():
            painter = QPainter(self)
            painter.setClipRect(self.contentsRect())
            paint_caret(painter, self.cursorRect(), self.fontMetrics().height())
