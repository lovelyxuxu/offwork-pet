"""Regressions for cancelled input and settings changes across the desktop host."""
from datetime import datetime
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QFocusEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from offwork_pet.app import Pet
from offwork_pet.core import Config


class StateRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = Pet(Config(character='lulu', autonomous_enabled=False), persist=False)
        self.pet.animation.stop()
        self.pet.tick_timer.stop()

    def tearDown(self):
        self.pet.buddy.stop()
        self.pet.click_timer.stop()
        self.pet.save_timer.stop()
        self.pet.tray.hide()
        self.pet.hide()
        self.pet.deleteLater()
        self.app.processEvents()

    def mouse(self, kind, point, global_point, buttons):
        event = QMouseEvent(kind, QPointF(point), QPointF(global_point),
            Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton,
            buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.pet, event)

    def test_cancelling_unrelated_source_preserves_pointer_pose(self):
        self.pet.select_package('noah-lulu')
        runtime = self.pet.runtime
        runtime.pointer_look.update((296, 72))
        self.assertEqual(runtime.animation_id(), 'facing_04')
        self.pet.clear_remote()
        self.assertEqual(runtime.animation_id(), 'facing_04')

    def test_focus_loss_ends_drag_action_and_restores_cursor(self):
        point = QPoint(140, 180)
        global_point = self.pet.mapToGlobal(point)
        self.mouse(QEvent.Type.MouseButtonPress, point, global_point, Qt.MouseButton.LeftButton)
        self.mouse(QEvent.Type.MouseMove, point + QPoint(40, 20), global_point + QPoint(40, 20), Qt.MouseButton.LeftButton)
        self.assertTrue(self.pet.dragging)
        self.assertEqual(self.pet.runtime.context['source'], 'drag')
        QApplication.sendEvent(self.pet, QFocusEvent(QEvent.Type.FocusOut))
        self.assertIsNone(self.pet.runtime.current)
        self.assertEqual(self.pet.cursor().shape(), Qt.CursorShape.OpenHandCursor)

    def test_hiding_pet_cancels_pending_click(self):
        point = QPoint(140, 180)
        global_point = self.pet.mapToGlobal(point)
        self.mouse(QEvent.Type.MouseButtonPress, point, global_point, Qt.MouseButton.LeftButton)
        self.mouse(QEvent.Type.MouseButtonRelease, point, global_point, Qt.MouseButton.NoButton)
        self.assertTrue(self.pet.click_timer.isActive())
        self.pet.hide()
        self.assertFalse(self.pet.click_timer.isActive())

    def test_cancelling_settings_keeps_pending_wheel_save(self):
        self.pet.resize_pet(125)
        self.pet.save_timer.start(350)
        QTimer.singleShot(0, lambda: self.pet.dialog.reject())
        self.pet.open_settings()
        self.assertEqual(self.pet.config.scale, 125)
        self.assertTrue(self.pet.save_timer.isActive())

    def test_appearance_change_keeps_reminder_deduplication(self):
        with patch('offwork_pet.app.datetime') as clock, patch.object(self.pet.tray, 'showMessage') as notify:
            clock.now.return_value = datetime(2026, 10, 22, 17, 29)
            self.pet.tick()
            clock.now.return_value = datetime(2026, 10, 22, 17, 31)
            self.pet.tick()
            self.assertEqual(notify.call_count, 1)
            self.pet.config.opacity = 90
            self.pet.apply_config()
            clock.now.return_value = datetime(2026, 10, 22, 17, 29)
            self.pet.tick()
            clock.now.return_value = datetime(2026, 10, 22, 17, 31)
            self.pet.tick()
            self.assertEqual(notify.call_count, 1)

    def test_open_action_panel_reflects_disabled_interactions(self):
        self.pet.open_actions()
        panel = self.pet.action_panel
        self.pet.config.interactive = False
        self.pet.apply_config()
        self.assertIn('本地互动已关闭', panel.context.text())
        self.assertTrue(all(not button.isEnabled() for button in panel.local_buttons))
        panel.hide()

    def test_changing_schedule_still_notifies_for_new_target(self):
        with patch('offwork_pet.app.datetime') as clock, patch.object(self.pet.tray, 'showMessage') as notify:
            clock.now.return_value = datetime(2026, 10, 22, 17, 29)
            self.pet.tick()
            clock.now.return_value = datetime(2026, 10, 22, 17, 31)
            self.pet.tick()
            self.assertEqual(notify.call_count, 1)
            self.pet.config.off_time = '19:00'
            self.pet.apply_config()
            clock.now.return_value = datetime(2026, 10, 22, 18, 31)
            self.pet.tick()
            self.assertEqual(notify.call_count, 2)


if __name__ == '__main__':
    unittest.main()
