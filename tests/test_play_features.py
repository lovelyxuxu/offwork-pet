from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from offwork_pet.app import Pet, SettingsDialog
from offwork_pet.behavior import BehaviorRuntime
from offwork_pet.core import Config, ReminderTracker, countdown, validate
from offwork_pet.desktop_scene import Rect, Scene
from offwork_pet.packages import PackageError, PackageLibrary
from offwork_pet.toys import Ball, BallGame
from pack_fixtures import create_pack


def playful_pack(manifest, behavior):
    manifest['requires'] += ['schedule.events/v1', 'desktop.toys/v1', 'desktop.motion/v1']
    behavior['animations']['movement']['frames'][0]['durationMs'] = 1000
    for name, direction in [('shuffle_west', -1), ('shuffle_east', 1), ('greet_orb', None)]:
        action = deepcopy(behavior['actions']['stretch'])
        action.update(onBusy='discard', cooldownMs=0)
        if direction:
            action['motion'] = {'direction': direction, 'speed': 100, 'endMs': 1000}
        behavior['actions'][name] = action
    behavior['toys'] = {'ball': {'states': ['napping'], 'leftAction': 'shuffle_west',
        'rightAction': 'shuffle_east', 'touchAction': 'greet_orb'}}
    behavior['actions']['celebrate'] = {**deepcopy(behavior['actions']['stretch']),
                                      'text': '今天辛苦啦', 'onBusy': 'discard'}
    behavior['rules'] += [
        {'id': 'soon', 'event': 'schedule.soon', 'then': {'type': 'action', 'id': 'stretch'}},
        {'id': 'done', 'event': 'schedule.offwork', 'then': {'type': 'action', 'id': 'celebrate'}}]


class PlayModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        archive, _ = create_pack(self.temp.name, mutate=playful_pack)
        self.package = PackageLibrary(Path(self.temp.name)/'library').install(archive)
        self.now = 100.0
        self.runtime = BehaviorRuntime(self.package, clock=lambda: self.now)
        self.ball = Ball(600, 100, 900)
        self.game = BallGame(self.runtime, self.ball, (0, 800), 100, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_chase_uses_declared_direction_and_respects_frame_motion(self):
        self.assertEqual(self.game.update(200), 200)
        self.assertEqual(self.runtime.current, 'shuffle_east')
        self.now += .1
        self.assertAlmostEqual(self.game.update(200), 210)
        self.ball.x = 110
        self.game.update(210)
        self.assertEqual(self.runtime.current, 'shuffle_west')
        self.now += .1
        self.assertAlmostEqual(self.game.update(210), 200)

    def test_ball_bounces_slows_and_bounds_long_suspension(self):
        self.ball.x, self.ball.velocity = 899, 600
        self.ball.advance(100)
        self.assertEqual(self.ball.x, 900)
        self.assertLess(self.ball.velocity, 0)
        speed = abs(self.ball.velocity)
        self.ball.advance(.1)
        self.assertLess(abs(self.ball.velocity), speed)
        self.assertGreater(self.ball.x, self.ball.left)

    def test_touch_and_three_touch_limit_then_throw_restarts(self):
        for index in range(3):
            self.ball.x, self.ball.velocity = 330, 0
            self.game.update(200)
            self.assertEqual(self.runtime.current, 'greet_orb')
            self.assertGreater(self.ball.velocity, 0)
            self.assertEqual(self.game.touches, index+1)
            self.now += 2.1
            self.runtime.tick()
        self.game.update(200)
        self.assertFalse(self.game.playing)
        self.game.release(10000)
        self.assertTrue(self.game.playing)
        self.assertEqual(self.game.touches, 0)
        self.assertEqual(self.ball.velocity, 600)

    def test_manual_actions_hold_timeout_and_boundary_preempt_movement(self):
        self.runtime.request('stretch', {'source': 'remote'})
        self.assertEqual(self.game.update(200), 200)
        self.game.stop()
        self.assertEqual(self.runtime.current, 'stretch')
        self.runtime.cancel()
        self.game.release(0)
        self.game.update(200)
        self.game.hold()
        self.assertIsNone(self.runtime.current)
        self.now += 30
        self.assertEqual(self.game.update(200), 200)
        self.game.release(0)
        self.assertEqual(self.game.deadline, self.now+20)
        self.now += 21
        self.game.update(200)
        self.assertFalse(self.game.playing)
        self.game.release(0)
        self.ball.x = 900
        self.game.update(800)
        self.now += .1
        self.assertLessEqual(self.game.update(800), 800)

    def test_capabilities_and_invalid_motion_are_rejected(self):
        mutations = [
            lambda m, b: m['requires'].remove('schedule.events/v1'),
            lambda m, b: m['requires'].remove('desktop.toys/v1'),
            lambda m, b: b['toys']['ball'].update(leftAction='shuffle_east'),
            lambda m, b: b['toys']['ball'].update(states=['missing']),
            lambda m, b: b['actions']['greet_orb'].update(onBusy='enqueue')]
        for index, mutate in enumerate(mutations):
            def invalid(m, b):
                playful_pack(m, b)
                mutate(m, b)
            with self.subTest(index=index):
                archive, _ = create_pack(self.temp.name, identifier=f'bad{index}', mutate=invalid)
                with self.assertRaises(PackageError):
                    PackageLibrary(Path(self.temp.name)/f'bad{index}').install(archive)
        with self.assertRaises(ValueError):
            validate(Config(rituals_enabled='yes'))


class PlayGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        archive, _ = create_pack(self.temp.name, mutate=playful_pack)
        self.pet = Pet(Config(character='', reminders=False), persist=False,
                       package_directory=Path(self.temp.name)/'library', seed_starters=False)
        self.pet.animation.stop()
        self.pet.tick_timer.stop()
        self.pet.import_package(archive)
        self.pet.move(200, 100)
        self.scene = Scene(Rect(0, 0, 1400, 1000), ())
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

    def tick_at(self, hour, minute, second=0):
        with patch('offwork_pet.app.datetime') as date:
            date.now.return_value = datetime(2026, 10, 22, hour, minute, second)
            self.pet.tick()

    def test_schedule_crossings_no_duplicates_and_preview_does_not_change_tracker(self):
        self.pet.reminders = ReminderTracker()
        self.tick_at(17, 49, 59)
        self.assertIsNone(self.pet.runtime.current)
        self.tick_at(17, 50)
        self.assertEqual(self.pet.runtime.current, 'stretch')
        self.pet.runtime.cancel()
        self.tick_at(18, 0)
        self.assertEqual(self.pet.runtime.current, 'celebrate')
        self.pet.advance_remote()
        self.assertEqual(self.pet.reaction_message, '今天辛苦啦')
        self.assertTrue(self.pet.show_bubble())
        self.pet.config.bubble_mode = 'hidden'
        self.assertFalse(self.pet.show_bubble())
        self.pet.runtime.cancel()
        self.tick_at(17, 59)
        self.tick_at(18, 0)
        self.assertIsNone(self.pet.runtime.current)
        sent = set(self.pet.reminders.sent)
        self.pet.runtime.cooldowns.clear()
        self.assertEqual(self.pet.preview_ritual(), 'started')
        self.assertEqual(self.pet.reminders.sent, sent)

    def test_disabled_hidden_fullscreen_and_busy_rituals_are_not_queued(self):
        for mode in ('disabled', 'hidden', 'fullscreen', 'busy'):
            with self.subTest(mode=mode):
                self.pet.runtime.cancel()
                self.pet.runtime.cooldowns.clear()
                self.pet.reminders = ReminderTracker()
                self.pet.config.rituals_enabled = mode != 'disabled'
                self.pet.show()
                self.scene = Scene(self.scene.bounds, (), fullscreen=mode == 'fullscreen')
                if mode == 'hidden':
                    self.pet.hide()
                if mode == 'busy':
                    self.pet.runtime.request('stretch', {'source': 'local'})
                self.tick_at(17, 59)
                self.tick_at(18, 0)
                self.assertNotEqual(self.pet.runtime.current, 'celebrate')
                self.assertFalse(self.pet.runtime.queue)
                self.pet.show()
                self.tick_at(18, 1)
                self.assertNotEqual(self.pet.runtime.current, 'celebrate')

    def test_ball_entry_motion_drag_hide_and_package_switch(self):
        self.pet.open_actions()
        self.assertTrue(self.pet.action_panel.ball_button.isEnabled())
        self.assertEqual(self.pet.toggle_ball(), 'started')
        self.assertFalse(self.pet.action_panel.isVisible())
        game, window = self.pet.ball_game, self.pet.ball_window
        self.assertTrue(window.isVisible())
        self.assertIs(window.parent(), self.pet)
        self.pet.advance_ball()
        self.assertEqual(self.pet.runtime.current, 'shuffle_east')
        self.pet.hide()
        self.assertIsNone(self.pet.ball_game)
        self.assertIsNone(self.pet.runtime.current)
        self.assertFalse(game.playing)
        self.pet.show()
        self.assertEqual(self.pet.toggle_ball(), 'started')
        self.pet.select_package('')
        self.assertIsNone(self.pet.ball_window)
        self.assertFalse(self.pet.ball_action.isEnabled())

    def test_ball_fullscreen_and_user_action_cleanup(self):
        self.assertEqual(self.pet.toggle_ball(), 'started')
        self.pet.advance_ball()
        self.scene = Scene(self.scene.bounds, (), fullscreen=True)
        self.pet.ball_checked = 0
        self.pet.advance_ball()
        self.assertIsNone(self.pet.ball_game)
        self.scene = Scene(self.scene.bounds, ())
        self.assertEqual(self.pet.toggle_ball(), 'started')
        self.assertEqual(self.pet.invoke_package_menu('menu'), 'started')
        self.assertIsNone(self.pet.ball_window)
        self.assertEqual(self.pet.runtime.context['source'], 'local')

    def test_settings_toggle_and_tight_space(self):
        dialog = SettingsDialog(self.pet.config, self.pet)
        dialog.checks['rituals_enabled'].setChecked(False)
        self.assertFalse(dialog.result_config(self.pet.config).rituals_enabled)
        dialog.deleteLater()
        self.scene = Scene(Rect(0, 0, 300, 600), ())
        self.assertEqual(self.pet.toggle_ball(), 'space')
        self.assertIsNone(self.pet.ball_window)


if __name__ == '__main__':
    unittest.main()
