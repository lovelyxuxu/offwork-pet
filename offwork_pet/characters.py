"""Rendered 3D Lulu and scalable vector Lumei. Sources: docs/形象来源.md."""
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from .sprites import draw_lulu

CHARACTERS = {'lulu': '噜噜（3D 动画）', 'lumei': '噜妹（二维）'}


def draw_character(p, character='lulu', phase=0, gaze=None, hovered=False, happy=False,
                   dragging=False, action='', progress=0, interactive=True, reaction_progress=None):
    """Draw inside a 280 x 280 logical canvas; leave y < 110 for the bubble."""
    if character == 'lulu':
        draw_lulu(p, phase, gaze, hovered, happy, dragging, action, progress, interactive,
                  reaction_progress)
        return
    gaze = gaze or QPointF()
    fur, edge, muzzle = '#FFD65D', '#B67C2F', '#F7A847'
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    def oval(x, y, w, h, color, outline=False):
        p.setPen(QPen(QColor(edge), 1.8) if outline else Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(x, y, w, h))
    def rounded(x, y, w, h, radius, color, outline=False):
        p.setPen(QPen(QColor(edge), 1.8) if outline else Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)
    oval(78, 253, 128, 12, '#20000000')
    p.translate(0, math.sin(phase / 14) * 1.7)
    active = bool(action)
    wave = interactive and (happy or phase % 760 < 55 and phase > 55)
    if dragging and interactive:
        p.translate(140, 155)
        p.rotate(math.sin(phase / 3) * 6)
        p.translate(-140, -155)
    elif action == 'receive_bonk':
        p.translate(math.sin(progress * math.pi * 8) * (1 - progress) * 9, 0)
    # Feet and rounded torso.
    rounded(92, 200, 97, 56, 28, fur, True)
    oval(87, 242, 38, 17, fur, True)
    oval(158, 242, 38, 17, fur, True)
    oval(115, 216, 52, 33, '#FFE89B')
    if character == 'lumei':
        dress = QPainterPath(QPointF(106, 215))
        dress.lineTo(176, 215)
        dress.lineTo(195, 248)
        dress.quadTo(142, 265, 87, 248)
        dress.closeSubpath()
        p.setPen(QPen(QColor('#C87897'), 1.5))
        p.setBrush(QColor('#F5ADCA'))
        p.drawPath(dress)
        for x, y in ((110, 235), (144, 244), (174, 234)):
            for angle in range(0, 360, 72):
                oval(x + math.cos(math.radians(angle)) * 4 - 3, y + math.sin(math.radians(angle)) * 4 - 3, 6, 6, '#FFF5E4')
            oval(x - 2, y - 2, 4, 4, '#E5AE38')
    # Short rounded ears rather than cat ears.
    p.save()
    tilt = gaze.x() * 4 if hovered and interactive else math.sin(phase / 42) * 2 if interactive else 0
    if happy and interactive:
        tilt = math.sin(phase / 8) * 5
    p.translate(140, 191)
    p.rotate(tilt)
    if action == 'receive_pinch':
        p.scale(1 + math.sin(progress * math.pi) * 0.12, 1 - math.sin(progress * math.pi) * 0.05)
    p.translate(-140, -191)
    oval(84, 136, 28, 32, fur, True)
    oval(170, 136, 28, 32, fur, True)
    oval(90, 143, 15, 18, '#ECAF56')
    oval(177, 143, 15, 18, '#ECAF56')
    rounded(76, 149, 132, 82, 38, fur, True)
    oval(80, 187, 35, 28, muzzle)
    oval(165, 187, 35, 28, muzzle)
    rounded(103, 180, 75, 42, 21, muzzle)
    blink = phase % 95 in (0, 1, 2, 3)
    p.setPen(QPen(QColor('#60452F'), 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for x in (112, 169):
        if dragging or action == 'receive_bonk':
            p.drawLine(QPointF(x - 4, 169), QPointF(x + 4, 176))
            p.drawLine(QPointF(x + 4, 169), QPointF(x - 4, 176))
        elif happy or blink or action == 'receive_pinch':
            eye = QPainterPath(QPointF(x - 5, 174))
            eye.quadTo(x, 169 if happy else 176, x + 5, 174)
            p.drawPath(eye)
        else:
            oval(x - 7, 164, 14, 21, '#FFFDF0')
            dx, dy = (gaze.x() * 3, gaze.y() * 2.5) if interactive else (0, 0)
            oval(x - 3 + dx, 169 + dy, 6, 11, '#526E79')
            oval(x - 1 + dx, 170 + dy, 2, 3, '#FFFFFF')
            p.setPen(QPen(QColor('#60452F'), 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    oval(129, 188, 6, 4, '#9F6633')
    oval(147, 188, 6, 4, '#9F6633')
    p.setPen(QPen(QColor('#925D32'), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    mouth = QPainterPath(QPointF(128, 205))
    mouth.quadTo(140, 213 if happy else 208, 153, 205)
    p.drawPath(mouth)
    # Tangerine and leaf: shared identifying cue for both characters.
    oval(124, 119, 35, 29, '#F4A42B', True)
    p.setPen(QPen(QColor('#7E994D'), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(141, 121), QPointF(144, 114))
    oval(143, 112, 15, 7, '#91AD56')
    oval(129, 125, 9, 5, '#FFD16F')
    if character == 'lumei':
        for x, rotation in ((168, -25), (187, 25)):
            p.save()
            p.translate(x, 147)
            p.rotate(rotation)
            oval(-12, -9, 24, 18, '#EF90B6', True)
            p.restore()
        oval(173, 141, 12, 12, '#D9759F')
    if action == 'receive_pinch':
        oval(66, 191, 24, 15, '#F9D394', True)
        oval(194, 191, 24, 15, '#F9D394', True)
    p.restore()
    # Arms animate independently of the head.
    for side in (-1, 1):
        p.save()
        p.translate(140 + side * 48, 220)
        if action == 'send_pinch':
            p.rotate(-side * (50 + math.sin(progress * math.pi * 3) * 18))
        elif side == 1 and action == 'send_bonk':
            p.rotate(-25 - abs(math.sin(progress * math.pi * 2)) * 85)
        elif side == 1 and wave:
            p.rotate(-side * (55 + math.sin(phase / 3) * 20))
        oval(-10, -10, 22, 37, fur, True)
        p.restore()
    if happy:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#ED8AA1'))
        heart = QPainterPath(QPointF(215, 150))
        heart.cubicTo(194, 135, 206, 123, 215, 134)
        heart.cubicTo(225, 123, 236, 135, 215, 150)
        p.drawPath(heart)
    if action == 'receive_bonk':
        p.setPen(QPen(QColor('#DA9A36'), 2.5))
        for dx in (-15, 0, 15):
            p.drawLine(QPointF(141 + dx, 115), QPointF(141 + dx * 1.3, 104))
    p.restore()


def character_icon(character, size=80):
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.scale(size / 180, size / 180)
    p.translate(-50, -100)
    draw_character(p, character, phase=20)
    p.end()
    return pix
