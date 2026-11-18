"""Render the real Qt visitor doorway to an animated preview, without networking."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication

from offwork_pet.app import Pet
from offwork_pet.core import Config
from offwork_pet.visits import VisitorWindow


def main():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    pet = Pet(Config(character='gugagaga', autonomous_enabled=False, auto_update_check=False), persist=False)
    pet.animation.stop(); pet.tick_timer.stop()
    controller = pet.visits
    guest = VisitorWindow(controller, pet.library.load('monthly-salary-cat'))
    now = [0.0]
    guest.runtime.clock = lambda: now[0]
    guest.runtime.last_tick = 0
    frames, samples = [], []
    own = pet.grab()

    def capture():
        picture = QImage(620, 340, QImage.Format.Format_RGBA8888)
        picture.fill(QColor('#FAF6F1'))
        painter = QPainter(picture)
        painter.setPen(QColor('#7A6270'))
        painter.setFont(QFont('Microsoft YaHei', 10))
        painter.drawText(QRectF(20, 10, 280, 26), Qt.AlignmentFlag.AlignCenter, '自己的桌宠 · 留在原位')
        painter.drawText(QRectF(320, 10, 280, 26), Qt.AlignmentFlag.AlignCenter, '搭子来串门')
        painter.drawPixmap(QPoint(15, 44), own)
        if not guest.disposing:
            painter.drawPixmap(QPoint(325, 44), guest.grab())
        painter.end()
        pil = Image.frombytes('RGBA', (picture.width(), picture.height()), picture.bits().tobytes()).convert('RGB')
        frames.append(pil)
        return picture

    try:
        guest.arrive(lambda: guest.runtime.suspend(False))
        guest.transition_timer.stop()
        guest.portal.clock = lambda: now[0]
        guest.portal.started = 0
        for index in range(40):
            now[0] = index*.05
            guest.advance_transition()
            picture = capture()
            if index in (4, 10, 18, 26, 36):
                samples.append(picture.copy())
        for _ in range(16):
            capture()
        guest.depart()
        guest.transition_timer.stop()
        guest.portal.clock = lambda: now[0]
        guest.portal.started = now[0]
        start = now[0]
        for index in range(40):
            now[0] = start+index*.05
            guest.advance_transition()
            capture()
        output = ROOT/'outputs'
        output.mkdir(exist_ok=True)
        frames[0].save(output/'visitor-portal.gif', save_all=True, append_images=frames[1:],
                       duration=50, loop=0, optimize=True)
        contact = QImage(620*len(samples), 340, QImage.Format.Format_RGBA8888)
        contact.fill(QColor('#FAF6F1'))
        painter = QPainter(contact)
        for index, sample in enumerate(samples):
            painter.drawImage(QPoint(index*620, 0), sample)
        painter.end()
        contact.save(str(output/'visitor-portal-stages.png'))
        print('Rendered outputs/visitor-portal.gif and outputs/visitor-portal-stages.png')
    finally:
        guest.dispose()
        pet.visits.shutdown(); pet.buddy.stop(); pet.tray.hide(); pet.hide(); pet.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    main()
