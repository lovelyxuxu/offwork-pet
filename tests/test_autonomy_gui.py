from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from offwork_pet.app import Pet, SettingsDialog
from offwork_pet.autonomy import AutonomyController
from offwork_pet.core import Config
from offwork_pet.desktop_scene import Rect, Scene, Surface
from pack_fixtures import create_pack
from test_autonomy import autonomous_pack


class AutonomyGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        archive, _ = create_pack(self.temp.name, mutate=autonomous_pack)
        self.pet = Pet(Config(character='', activity_min_seconds=5, activity_max_seconds=5),
            persist=False, package_directory=Path(self.temp.name)/'library', seed_starters=False)
        self.pet.animation.stop()
        self.pet.tick_timer.stop()
        self.pet.import_package(archive)
        self.now = 100.0
        self.pet.runtime.clock = self.pet.runtime.pointer_look.clock = lambda: self.now
        self.pet.runtime.last_tick = self.now
        self.pet.autonomy = AutonomyController(self.pet.runtime, clock=lambda: self.now)
        self.pet.move(300, 300)
        self.scene = Scene(Rect(0, 0, 1400, 1000), (Surface('window:test', 0, 1400, 564),))
        self.observer = patch.object(self.pet.desktop_observer, 'snapshot', side_effect=lambda _: self.scene)
        self.observer.start()

    def tearDown(self):
        self.observer.stop()
        self.pet.buddy.stop()
        self.pet.click_timer.stop()
        self.pet.save_timer.stop()
        self.pet.tray.hide()
        self.pet.hide()
        self.pet.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def step(self, seconds=0):
        self.now += seconds
        self.pet.advance_remote()
        self.pet.advance_autonomy()

    def start_walking(self):
        self.step()
        self.step(5)
        self.assertEqual(self.pet.runtime.current, 'stretch')
        self.step(.1)
        self.assertGreater(self.pet.x(), 300)

    def test_fractional_movement_and_click_immediately_preempts_autonomy(self):
        self.start_walking()
        point = QPoint(140, 200)
        event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(point),
            QPointF(self.pet.mapToGlobal(point)), Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.pet, event)
        self.assertIsNone(self.pet.runtime.current)
        before = self.pet.pos()
        self.step(.1)
        self.assertEqual(self.pet.pos(), before)
        self.assertIsNone(self.pet.autonomy.support)

    def test_panels_pause_and_hide_switch_clear_old_motion(self):
        self.start_walking()
        self.pet.open_actions()
        self.assertIsNone(self.pet.runtime.current)
        self.step(100)
        self.assertIsNone(self.pet.runtime.current)
        self.pet.action_panel.hide()
        self.step()
        self.step(5)
        self.assertEqual(self.pet.runtime.current, 'stretch')
        previous = self.pet.autonomy
        self.pet.select_package('')
        self.assertIsNone(self.pet.autonomy)
        self.assertTrue(previous.runtime.stopped)
        self.pet.advance_autonomy()

    def test_settings_and_menu_controls_keep_other_preferences(self):
        dialog = SettingsDialog(self.pet.config, self.pet)
        dialog.activity_min.setValue(35)
        dialog.activity_max.setValue(90)
        dialog.roaming.setCurrentIndex(dialog.roaming.findData('stay'))
        config = dialog.result_config(self.pet.config)
        self.assertEqual((config.activity_min_seconds, config.activity_max_seconds, config.roaming_mode), (35, 90, 'stay'))
        self.assertTrue(config.pointer_look_enabled)
        dialog.autonomous.setChecked(False)
        self.assertFalse(dialog.activity_min.isEnabled())
        self.assertFalse(dialog.result_config(self.pet.config).autonomous_enabled)
        dialog.deleteLater()
        self.pet.set_stay(True)
        self.assertEqual(self.pet.config.roaming_mode, 'stay')
        self.assertTrue(self.pet.stay_action.isChecked())
        self.pet.set_stay(False)
        self.assertEqual(self.pet.config.roaming_mode, 'free')
        self.pet.set_autonomous(False)
        self.assertFalse(self.pet.autonomous_action.isChecked())
        self.step(100)
        self.assertIsNone(self.pet.runtime.current)

    def test_fullscreen_preserves_position_and_only_cancels_automatic_actions(self):
        self.start_walking()
        self.scene = Scene(self.scene.bounds, self.scene.surfaces, fullscreen=True)
        self.pet.scene_checked = 0
        self.step(.1)
        before = self.pet.pos()
        self.assertIsNone(self.pet.runtime.current)
        self.assertEqual(self.pet.invoke_package_menu('menu'), 'started')
        self.step(.1)
        self.assertEqual(self.pet.runtime.context['source'], 'local')
        self.assertEqual(self.pet.pos(), before)


if __name__ == '__main__':
    unittest.main()
