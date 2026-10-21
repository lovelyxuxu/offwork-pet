"""Transparent, pre-rendered 3D Lulu frames; no runtime 3D engine required."""
from functools import lru_cache
import json
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

ASSETS = Path(__file__).resolve().parent / 'assets' / 'lulu3d'


@lru_cache(maxsize=1)
def manifest():
    return json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=64)
def frame(clip, index):
    path = ASSETS / clip / f'{index:03d}.png'
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        raise RuntimeError(f'3D animation frame missing or unreadable: {path}')
    return pixmap


def animation_frame(phase, dragging=False, action='', progress=0, interactive=True,
                    reaction_progress=None):
    """Return an asset frame using the existing 20 Hz animation clock."""
    data = manifest()['clips']
    if not interactive and not action:
        return 'idle', 0
    if dragging and interactive:
        clip = 'drag'
    elif action in data:
        clip = action
        count = data[clip]['frames']
        return clip, min(count - 1, max(0, int(progress * count)))
    elif reaction_progress is not None:
        clip = 'pet'
        count = data[clip]['frames']
        return clip, min(count - 1, max(0, int(reaction_progress * count)))
    else:
        clip = 'idle'
    return clip, int(phase * .05 * data[clip]['fps']) % data[clip]['frames']


def contains(point):
    """Test the visible sprite rather than catching clicks in transparent margins."""
    rect = QRectF(*manifest()['draw_rect'])
    if not rect.contains(point):
        return False
    image = frame('idle', 0).toImage()
    x = min(image.width() - 1, int((point.x() - rect.x()) / rect.width() * image.width()))
    y = min(image.height() - 1, int((point.y() - rect.y()) / rect.height() * image.height()))
    return image.pixelColor(x, y).alpha() > 30


def draw_lulu(painter, phase=0, gaze=None, hovered=False, happy=False,
              dragging=False, action='', progress=0, interactive=True,
              reaction_progress=None):
    clip, index = animation_frame(phase, dragging, action, progress, interactive, reaction_progress)
    pixmap = frame(clip, index)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#20000000'))
    painter.drawEllipse(QRectF(104, 253, 76, 9))
    if hovered and interactive and gaze is not None and not (dragging or action or reaction_progress is not None):
        painter.translate(140, 254)
        painter.rotate(gaze.x() * 1.8)
        painter.translate(-140, -254)
    painter.drawPixmap(QRectF(*manifest()['draw_rect']), pixmap, QRectF(pixmap.rect()))
    # Small visual cues distinguish remote interactions without changing delivery logic.
    if action == 'receive_pinch':
        painter.setBrush(QColor('#75EE9AA4'))
        painter.drawEllipse(QRectF(101, 172, 13, 8))
        painter.drawEllipse(QRectF(165, 172, 13, 8))
    if action in ('send_bonk', 'receive_bonk'):
        painter.setPen(QPen(QColor('#D5A140'), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        amplitude = math.sin(max(0, min(1, progress)) * math.pi)
        for dx in (-10, 0, 10):
            painter.drawLine(QPointF(140 + dx, 108 - amplitude * 5),
                             QPointF(140 + dx * 1.35, 102 - amplitude * 8))
    if happy and interactive:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#E99CAF'))
        heart = QPainterPath(QPointF(202, 157))
        heart.cubicTo(185, 144, 194, 135, 202, 143)
        heart.cubicTo(210, 135, 219, 144, 202, 157)
        painter.drawPath(heart)
    painter.restore()
