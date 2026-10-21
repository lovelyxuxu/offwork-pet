"""Checks for complete distributable frames and desktop transparency behavior."""
import unittest

from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from offwork_pet.sprites import ASSETS, animation_frame, contains, manifest


class SpriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_all_distributed_clips_are_complete_transparent_images(self):
        data = manifest()
        for clip, spec in data['clips'].items():
            for index in range(spec['frames']):
                with self.subTest(clip=clip, index=index):
                    image = QImage(str(ASSETS / clip / f'{index:03d}.png'))
                    self.assertFalse(image.isNull())
                    self.assertEqual((image.width(), image.height()), tuple(data['size']))
                    self.assertTrue(image.hasAlphaChannel())
                    self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                    self.assertGreater(image.pixelColor(160, 210).alpha(), 240)

    def test_local_drag_takes_priority_over_remote_action(self):
        self.assertEqual(animation_frame(0, dragging=True, action='receive_bonk')[0], 'drag')
        self.assertEqual(animation_frame(0, action='receive_pinch', progress=1)[1], 15)
        self.assertEqual(animation_frame(40, dragging=True, interactive=False), ('idle', 0))
        self.assertEqual(animation_frame(0, action='receive_bonk', interactive=False)[0], 'receive_bonk')

    def test_transparent_margins_do_not_capture_pointer(self):
        self.assertTrue(contains(QPointF(140, 180)))
        self.assertFalse(contains(QPointF(70, 112)))
        self.assertFalse(contains(QPointF(140, 100)))
        self.assertFalse(contains(QPointF(140, 270)))


if __name__ == '__main__':
    unittest.main()
