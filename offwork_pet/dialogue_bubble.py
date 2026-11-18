"""Soft speech bubbles; dialogue owns the space while the pet is talking."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainterPath, QPen, QTextLayout


def rounded_font(size):
    font = QFont()
    font.setFamilies(['YouYuan', '幼圆', 'Microsoft YaHei UI', 'Microsoft YaHei'])
    font.setPointSizeF(size)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def speech_layout(content, width=232, height=66):
    """Prefer a natural two-part sentence, with a readable minimum font size."""
    for size in (15, 14, 13):
        font = rounded_font(size)
        metrics = QFontMetricsF(font)
        breaks = [i + 1 for i, char in enumerate(content[:-1]) if char in '，。！？；～~!?;']
        candidates = [index for index in breaks if metrics.horizontalAdvance(content[:index]) <= width
                      and metrics.horizontalAdvance(content[index:]) <= width]
        split = min(candidates, key=lambda index: abs(index - len(content)/2)) if candidates else None
        formatted = content[:split] + '\n' + content[split:] if split else content
        bounds = metrics.boundingRect(QRectF(0, 0, width, 1000), Qt.TextFlag.TextWordWrap, formatted)
        if bounds.height() <= height:
            return formatted, font
    # Exceptional imported long lines keep the readable font. The full text
    # remains available in the role's preview; only the last visible line elides.
    layout = QTextLayout(content, font)
    layout.beginLayout()
    lines = []
    max_lines = max(1, int(height // metrics.lineSpacing()))
    for index in range(max_lines):
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        start = line.textStart()
        if index == max_lines-1:
            lines.append(metrics.elidedText(content[start:], Qt.TextElideMode.ElideRight, width))
        else:
            lines.append(content[start:start+line.textLength()])
    layout.endLayout()
    return '\n'.join(lines), font


def draw_dialogue(painter, content, name, cream, theme):
    body = QRectF(10, 3, 260, 102)
    accent = QColor({'奶油橘': '#D893A8', '薄荷绿': '#70AB96', '云朵蓝': '#8D9FC7'}[theme])
    path = QPainterPath()
    path.addRoundedRect(body, 27, 27)
    tail = QPainterPath(QPointF(126, 101))
    tail.cubicTo(130, 109, 136, 113, 142, 114)
    tail.cubicTo(143, 108, 147, 104, 151, 101)
    shape = path.united(tail)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(80, 57, 63, 22))
    painter.drawPath(shape.translated(0, 3))
    gradient = QLinearGradient(0, 3, 0, 105)
    gradient.setColorAt(0, QColor('#FFFCFA'))
    gradient.setColorAt(1, QColor(cream))
    painter.setBrush(gradient)
    painter.setPen(QPen(accent.lighter(128), 1.5))
    painter.drawPath(shape)
    painter.setPen(Qt.PenStyle.NoPen)
    pale = QColor(accent)
    pale.setAlpha(45)
    painter.setBrush(pale)
    painter.drawEllipse(QRectF(19, 18, 11, 7))
    painter.drawEllipse(QRectF(250, 18, 11, 7))
    painter.setPen(accent.darker(120))
    header = QFont('Microsoft YaHei UI', 8)
    painter.setFont(header)
    title = QFontMetricsF(header).elidedText(name, Qt.TextElideMode.ElideRight, 190)
    painter.drawText(QRectF(32, 9, 216, 18), Qt.AlignmentFlag.AlignCenter, title)
    half_title = QFontMetricsF(header).horizontalAdvance(title) / 2
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(accent.lighter(120))
    for center in (140-half_title-12, 140+half_title+12):
        heart = QPainterPath(QPointF(center, 20))
        heart.cubicTo(center-8, 15, center-2, 11, center, 15)
        heart.cubicTo(center+2, 11, center+8, 15, center, 20)
        painter.drawPath(heart)
    formatted, font = speech_layout(content)
    painter.setFont(font)
    painter.setPen(QColor('#65484D'))
    painter.drawText(QRectF(24, 30, 232, 66), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, formatted)
    painter.restore()
