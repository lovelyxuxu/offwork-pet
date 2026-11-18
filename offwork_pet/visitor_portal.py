"""Original vector doorway and time-based transitions, independent of pet actions."""
from dataclasses import dataclass
import math
from time import monotonic

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainterPath, QPen, QPolygonF, QRadialGradient


def smooth(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


@dataclass(frozen=True)
class PortalFrame:
    progress: float
    door_scale: float
    door_alpha: float
    openness: float
    reveal: float
    done: bool


class PortalTransition:
    duration = 1.8

    def __init__(self, leaving=False, clock=monotonic):
        self.clock = clock
        self.started = clock()
        self.leaving = leaving

    def sample(self):
        elapsed = max(0.0, self.clock()-self.started)
        appear = smooth(elapsed/.22)
        disappear = smooth((elapsed-1.55)/.25)
        opened = smooth((elapsed-.18)/.40) * (1-smooth((elapsed-1.28)/.30))
        reveal = smooth((elapsed-.58)/.70)
        return PortalFrame(min(1.0, elapsed/self.duration),
            (.82+.18*appear)*(1-disappear), appear*(1-disappear), opened,
            1-reveal if self.leaving else reveal, elapsed >= self.duration)


def color(value, alpha=1.0):
    result = QColor(value)
    result.setAlphaF(max(0.0, min(1.0, alpha)))
    return result


def polygon(points):
    return QPolygonF([QPointF(x, y) for x, y in points])


def draw_door(painter, frame):
    """Draw at the same logical foot line (y=264) used by PackagePlayer."""
    if frame.door_alpha <= 0 or frame.door_scale <= 0:
        return
    painter.save()
    painter.setOpacity(frame.door_alpha)
    painter.translate(123, 264)
    painter.scale(frame.door_scale, frame.door_scale)
    painter.translate(-123, -264)
    painter.setPen(Qt.PenStyle.NoPen)

    glow = QRadialGradient(QPointF(123, 170), 119)
    glow.setColorAt(0, color('#F7B7E7', .54))
    glow.setColorAt(.62, color('#CBB6FA', .16))
    glow.setColorAt(1, color('#E6D6FF', 0))
    painter.setBrush(glow)
    painter.drawEllipse(QRectF(8, 47, 230, 230))
    painter.setBrush(color('#6D4B81', .13))
    painter.drawEllipse(QRectF(36, 250, 181, 20))

    frame_gradient = QLinearGradient(48, 54, 198, 264)
    frame_gradient.setColorAt(0, QColor('#F9AFCA'))
    frame_gradient.setColorAt(1, QColor('#C86792'))
    painter.setPen(QPen(QColor('#A85783'), 2))
    painter.setBrush(frame_gradient)
    painter.drawRoundedRect(QRectF(46, 52, 152, 212), 10, 10)
    painter.setPen(QPen(QColor('#FFE5EF'), 2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(50, 56, 144, 204), 7, 7)

    opening = QLinearGradient(60, 65, 185, 252)
    opening.setColorAt(0, QColor('#4C416E'))
    opening.setColorAt(.5, QColor('#957EB8'))
    opening.setColorAt(1, QColor('#D1EEE4'))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(opening)
    painter.drawRoundedRect(QRectF(56, 63, 132, 192), 4, 4)
    light = QRadialGradient(QPointF(121, 207), 115)
    light.setColorAt(0, color('#FFF4D7', .69*frame.openness))
    light.setColorAt(1, color('#E8DEFF', 0))
    painter.setBrush(light)
    painter.drawRoundedRect(QRectF(57, 64, 130, 189), 4, 4)

    # Bilinear projection keeps the hinged leaf and its panels consistent as it
    # swings past edge-on, without a bitmap or a role-specific animation.
    angle = math.radians(105*frame.openness)
    width, depth = 132*math.cos(angle), 14*math.sin(angle)
    def project(u, v):
        return 56+width*u, 63+depth*u+(192-2*depth*u)*v
    def panel(u, v, w, h):
        return polygon([project(u, v), project(u+w, v), project(u+w, v+h), project(u, v+h)])
    leaf = QLinearGradient(56, 63, 56+max(1, width), 250)
    leaf.setColorAt(0, QColor('#F7A5C3'))
    leaf.setColorAt(1, QColor('#D77A9F'))
    painter.setPen(QPen(QColor('#A85580'), 2))
    painter.setBrush(leaf)
    painter.drawPolygon(panel(0, 0, 1, 1))
    if abs(width) > 7:
        painter.setPen(QPen(QColor('#FBD2E1'), 1.6))
        painter.setBrush(color('#C96D97', .18))
        painter.drawPolygon(panel(.13, .10, .71, .36))
        painter.drawPolygon(panel(.13, .58, .71, .31))
        x, y = project(.84, .52)
        painter.setPen(QPen(QColor('#B4884B'), 1))
        painter.setBrush(QColor('#F9D78A'))
        painter.drawEllipse(QPointF(x, y), max(1.4, abs(math.cos(angle))*4), 4)
    painter.setBrush(QColor('#E996B5'))
    painter.setPen(QPen(QColor('#B66A8F'), 1))
    painter.drawRoundedRect(QRectF(43, 254, 158, 10), 4, 4)

    # A fixed handful of little stars, generated from elapsed progress rather
    # than random per-frame particles; redraws remain stable and bounded.
    for i in range(8):
        phase = frame.progress*1.8+i*.73
        x = 31+(i*47 % 184)+math.sin(phase*3)*5
        y = 71+(i*31 % 150)-math.sin(phase*2)*7
        alpha = (.35+.3*math.sin(phase*5)**2)*frame.openness
        painter.setPen(QPen(color('#FFF6CB', alpha), 1.5))
        radius = 2.6+(i % 3)
        painter.drawLine(QPointF(x-radius, y), QPointF(x+radius, y))
        painter.drawLine(QPointF(x, y-radius), QPointF(x, y+radius))
    painter.restore()


def draw_character(painter, frame, draw):
    reveal = frame.reveal
    if reveal <= 0:
        return
    painter.save()
    painter.setOpacity(reveal)
    painter.translate(140-22*(1-reveal), 264-4*math.sin(reveal*math.pi))
    scale = .68+.32*reveal
    painter.scale(scale, scale)
    painter.translate(-140, -264)
    # Widen the doorway mask as the guest moves into the foreground.
    path = QPainterPath()
    path.addRoundedRect(QRectF(56-90*reveal, 60-35*reveal, 132+180*reveal, 205+55*reveal), 7, 7)
    painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
    draw(painter)
    painter.restore()
