"""A small draggable desktop ball; uses the host animation clock, no background timer."""
from time import monotonic

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget


class BallWindow(QWidget):
    removed = Signal()

    def __init__(self, game, floor, parent):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus
        if parent.config.topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(parent, flags)
        self.game, self.floor = game, floor
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle('下班搭子 · 小球')
        self.setToolTip('向任意方向拖动并松手抛球 · 可落在可见窗口边缘 · 右键收起')
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFixedSize(round(44*game.scale), round(44*game.scale))
        self.last_sample = None
        self.throw_velocity = 0
        self.throw_velocity_y = 0
        self.sync()

    def sync(self):
        center_y = self.game.ball.y if self.game.ball.y is not None else self.floor-18*self.game.scale
        self.move(round(self.game.ball.x-self.width()/2), round(center_y-21*self.game.scale))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(self.game.scale, self.game.scale)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(80, 60, 100, 35))
        p.drawEllipse(QRectF(6, 35, 32, 7))
        p.translate(22, 21)
        gradient = QRadialGradient(-5, -6, 28)
        gradient.setColorAt(0, QColor('#FFE9BB'))
        gradient.setColorAt(.55, QColor('#F2B36F'))
        gradient.setColorAt(1, QColor('#CF8264'))
        p.setBrush(gradient)
        p.drawEllipse(QRectF(-16, -16, 32, 32))
        p.rotate(self.game.ball.angle % 360)
        p.setPen(QPen(QColor('#FFF5DE'), 3))
        p.drawArc(QRectF(-11, -15, 22, 30), 65*16, 230*16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#FFF8E8'))
        p.drawEllipse(QRectF(-7, -10, 6, 4))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.removed.emit()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.game.hold()
            self.last_sample = (event.globalPosition().x(), event.globalPosition().y(), monotonic())
            self.grab_offset = (event.globalPosition().x()-self.game.ball.x,
                                event.globalPosition().y()-(self.game.ball.y or self.floor-18*self.game.scale))
            self.throw_velocity = 0
            self.throw_velocity_y = 0
            self.moved = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.game.dragging and event.buttons() & Qt.MouseButton.LeftButton:
            x, y, now = event.globalPosition().x(), event.globalPosition().y(), monotonic()
            old_x, old_y, old_time = self.last_sample
            if abs(x-old_x)+abs(y-old_y) > 1:
                self.moved = True
                self.throw_velocity = (x-old_x)/max(.016, now-old_time)
                self.throw_velocity_y = (y-old_y)/max(.016, now-old_time)
                self.last_sample = x, y, now
            self.game.ball.x = max(self.game.ball.left, min(self.game.ball.right, x-self.grab_offset[0]))
            if self.game.ball.y is not None:
                bounds = self.game.scene.bounds
                radius = 16*self.game.scale
                self.game.ball.y = max(bounds.top+radius, min(bounds.bottom-radius, y-self.grab_offset[1]))
            self.sync()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.game.dragging:
            recent = monotonic()-self.last_sample[2] <= .15
            velocity = self.throw_velocity if recent else 0
            velocity_y = self.throw_velocity_y if recent else 0
            if not self.moved:
                velocity = 180*self.game.scale
                velocity_y = -320*self.game.scale
            self.game.release(velocity, velocity_y)
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def closeEvent(self, event):
        self.removed.emit()
        event.accept()
