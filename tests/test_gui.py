"""Widget integration checks; use a Windows desktop for native font rendering."""
from time import monotonic
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from offwork_pet.app import Pet, SettingsDialog
from offwork_pet.core import Config


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = Pet(Config(), persist=False)
        self.pet.animation.stop()
        self.pet.tick_timer.stop()

    def tearDown(self):
        self.pet.click_timer.stop()
        self.pet.save_timer.stop()
        self.pet.tray.hide()
        self.pet.hide()
        self.pet.deleteLater()
        self.app.processEvents()

    def mouse(self, kind, local, global_point, buttons):
        event = QMouseEvent(kind, QPointF(local), QPointF(global_point),
                            Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
                            buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.pet, event)

    def test_drag_moves_without_click_reaction(self):
        self.pet.move(200, 200)
        self.mouse(QEvent.Type.MouseButtonPress, QPoint(140, 180), QPoint(340, 380), Qt.MouseButton.LeftButton)
        self.mouse(QEvent.Type.MouseMove, QPoint(170, 200), QPoint(370, 400), Qt.MouseButton.LeftButton)
        self.assertTrue(self.pet.dragging)
        self.assertEqual(self.pet.pos(), QPoint(230, 220))
        self.mouse(QEvent.Type.MouseButtonRelease, QPoint(140, 180), QPoint(370, 400), Qt.MouseButton.NoButton)
        self.assertFalse(self.pet.click_timer.isActive())
        self.assertEqual((self.pet.config.x, self.pet.config.y), (230, 220))

    def test_click_bubble_and_reaction_respect_hidden(self):
        self.pet.config.bubble_mode = "click"
        self.assertFalse(self.pet.show_bubble())
        self.pet.pet_cat()
        self.assertTrue(self.pet.show_bubble())
        self.assertGreater(self.pet.reaction_until, monotonic())
        self.pet.config.bubble_mode = "hidden"
        self.pet.pet_cat()
        self.assertFalse(self.pet.show_bubble())
        self.pet.config.interactive = False
        self.pet.reaction_until = 0
        self.pet.pet_cat()
        self.assertEqual(self.pet.reaction_until, 0)

    def test_double_click_cancels_pending_single_click(self):
        self.pet.config.bubble_mode = "click"
        point = QPoint(140, 180)
        global_point = self.pet.mapToGlobal(point)
        self.mouse(QEvent.Type.MouseButtonPress, point, global_point, Qt.MouseButton.LeftButton)
        self.mouse(QEvent.Type.MouseButtonRelease, point, global_point, Qt.MouseButton.NoButton)
        self.assertTrue(self.pet.click_timer.isActive())
        QTimer.singleShot(0, lambda: self.pet.dialog.reject())
        self.mouse(QEvent.Type.MouseButtonDblClick, point, global_point, Qt.MouseButton.LeftButton)
        self.mouse(QEvent.Type.MouseButtonRelease, point, global_point, Qt.MouseButton.NoButton)
        self.assertFalse(self.pet.click_timer.isActive())
        self.assertEqual(self.pet.reaction_index, 0)
        self.assertFalse(self.pet.show_bubble())

    def test_hover_tracks_cat_and_bubble(self):
        self.pet.config.bubble_mode = "hover"
        self.pet.hovered = False
        with patch("offwork_pet.app.QCursor.pos", return_value=self.pet.mapToGlobal(QPoint(140, 180))):
            self.pet.animate()
        self.assertTrue(self.pet.show_bubble())
        with patch("offwork_pet.app.QCursor.pos", return_value=self.pet.mapToGlobal(QPoint(140, 40))):
            self.pet.animate()
        self.assertTrue(self.pet.show_bubble())
        with patch("offwork_pet.app.QCursor.pos", return_value=self.pet.mapToGlobal(QPoint(-50, -50))):
            self.pet.animate()
        self.assertFalse(self.pet.show_bubble())

    def test_wheel_scale_opacity_and_clamp(self):
        def wheel(modifiers, delta=120):
            event = QWheelEvent(QPointF(140, 180), QPointF(self.pet.mapToGlobal(QPoint(140, 180))),
                                QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton, modifiers,
                                Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(self.pet, event)
        wheel(Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.pet.config.scale, 105)
        wheel(Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, -120)
        self.assertEqual(self.pet.config.opacity, 95)
        self.assertAlmostEqual(self.pet.windowOpacity(), 0.95, places=2)
        self.pet.config.opacity = 20
        wheel(Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, -120)
        self.assertEqual(self.pet.config.opacity, 20)

    def test_settings_save_fields_and_cancel_live_preview(self):
        dialog = SettingsDialog(self.pet.config)
        dialog.opacity.setValue(65)
        dialog.bubble_mode.setCurrentIndex(dialog.bubble_mode.findData("hover_click"))
        dialog.bubble_seconds.setValue(10)
        updated = dialog.result_config(self.pet.config)
        self.assertEqual((updated.opacity, updated.bubble_mode, updated.bubble_seconds), (65, "hover_click", 10))
        dialog.deleteLater()
        position = self.pet.pos()
        def cancel_preview():
            self.pet.dialog.scale.setValue(150)
            self.pet.dialog.opacity.setValue(50)
            self.pet.dialog.reject()
        QTimer.singleShot(0, cancel_preview)
        self.pet.open_settings()
        self.assertEqual(self.pet.config.scale, 100)
        self.assertEqual(self.pet.windowOpacity(), 1)
        self.assertEqual(self.pet.pos(), position)

    def test_all_modes_sizes_and_reactions_render(self):
        for scale in (50, 100, 200):
            for mode in ("always", "hover", "click", "hover_click", "hidden"):
                self.pet.config.scale = scale
                self.pet.config.bubble_mode = mode
                self.pet.apply_config()
                for state in ("normal", "pet", "drag"):
                    self.pet.reaction_until = monotonic() + 2 if state == "pet" else 0
                    self.pet.dragging = state == "drag"
                    self.assertFalse(self.pet.grab().isNull())


if __name__ == "__main__":
    unittest.main()
