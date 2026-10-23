from copy import deepcopy
from pathlib import Path
import random
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication

from offwork_pet.app import Pet
from offwork_pet.autonomy import AutonomyController
from offwork_pet.behavior import BehaviorRuntime
from offwork_pet.companionship import CompanionMemory
from offwork_pet.core import Config
from offwork_pet.creator_ui import CreatorDialog
from offwork_pet.desktop_scene import Rect, Scene, Surface
from offwork_pet.focus import FocusSession
from offwork_pet.pack_builder import add_action
from offwork_pet.packages import PackageLibrary
from pack_fixtures import create_pack
from test_play_features import playful_pack


def exploring_pack(manifest, behavior):
    playful_pack(manifest, behavior)
    manifest['requires'] += ['desktop.explore/v1', 'behavior.autonomy/v1', 'focus.events/v1', 'render.gifts/v1']
    behavior['autonomy'] = {'states': ['napping'], 'activities': [{'action': 'stretch'}],
        'exploration': {'observeAction': 'greet_orb', 'leftAction': 'shuffle_west',
            'rightAction': 'shuffle_east', 'jumpAction': 'greet_orb', 'restAction': 'greet_orb', 'jumpDurationMs': 900}}
    behavior['rules'].append({'id': 'focus_done', 'event': 'focus.finished', 'then': {'type': 'action', 'id': 'celebrate'}})
    behavior['actions']['gift_response'] = {**deepcopy(behavior['actions']['celebrate']),
        'gift': {'symbol': '★', 'label': '幸运星', 'durationMs': 12000}}
    behavior['interactions'].append({'id': 'star', 'label': '送颗星星', 'invoke': {'type': 'action', 'id': 'gift_response'}})


class LifeModelTests(unittest.TestCase):
    def test_focus_pause_resume_completion_once_and_cancel(self):
        now = [100.0]
        focus = FocusSession(lambda: now[0])
        focus.start(1)
        now[0] += 10
        focus.pause()
        self.assertEqual(focus.remaining, 50)
        now[0] += 100
        self.assertFalse(focus.tick())
        focus.resume()
        now[0] += 49
        self.assertFalse(focus.tick())
        now[0] += 1
        self.assertTrue(focus.tick())
        self.assertFalse(focus.tick())
        focus.start(20)
        focus.cancel()
        self.assertFalse(focus.active)

    def test_memory_is_local_per_role_bounded_and_persists(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'memory.json'
            now = [100.0]
            memory = CompanionMemory(path, lambda: now[0])
            memory.entry('a')['nickname'] = '小朋友'
            for _ in range(25):
                memory.record('a', 'custom_action')
            self.assertEqual(memory.entry('a')['interactions'], 1)
            now[0] += 11
            memory.record('a', 'custom_action')
            self.assertFalse(memory.entry('b')['favorites'])
            memory.save()
            loaded = CompanionMemory(path)
            self.assertEqual(loaded.entry('a')['favorites']['custom_action'], 2)
            self.assertEqual(loaded.entry('a')['nickname'], '小朋友')
            path.write_text('{broken', encoding='utf-8')
            self.assertEqual(CompanionMemory(path).data, {})

    def test_exploration_chain_jump_rest_and_target_loss(self):
        with TemporaryDirectory() as folder:
            archive, _ = create_pack(folder, mutate=exploring_pack)
            package = PackageLibrary(Path(folder)/'library').install(archive)
            now = [100.0]
            runtime = BehaviorRuntime(package, clock=lambda: now[0])
            controller = AutonomyController(runtime, clock=lambda: now[0], rng=random.Random(2))
            config = Config()
            ledge = Surface('window', 100, 1100, 800, 100)
            scene = Scene(Rect(0, 0, 1400, 1000), (ledge,))
            position = [300, 736]
            def step(seconds=.1):
                now[0] += seconds
                runtime.tick()
                target = controller.update(config, scene, tuple(position), (140, 264), (280, 280), 1)
                if target:
                    position[:] = target
            step(0)
            controller.explorer.next_due = 0
            step()
            self.assertEqual(controller.explorer.phase, 'observe')
            for _ in range(45):
                step()
            self.assertEqual(position[1], 536)
            self.assertEqual(controller.support, ledge)
            self.assertIn('window', controller.explorer.visited)
            self.assertFalse(controller.explorer.active)
            # Start another journey, then remove the destination while in flight.
            position[:] = [300, 736]
            controller.support = None
            controller.explorer.next_due = 0
            for _ in range(14):
                step()
            scene = Scene(scene.bounds, (Surface('desktop', 0, 1400, 1000),))
            for _ in range(35):
                step()
            self.assertAlmostEqual(position[1], 736)
            self.assertEqual(controller.support.key, 'desktop')
            controller.interrupt(config)
            self.assertIsNone(runtime.current)


class LifeGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        archive, _ = create_pack(self.temp.name, mutate=exploring_pack)
        self.pet = Pet(Config(character='', reminders=False), persist=False,
            package_directory=Path(self.temp.name)/'library', seed_starters=False)
        self.pet.animation.stop()
        self.pet.tick_timer.stop()
        self.pet.import_package(archive)
        self.scene = Scene(Rect(0, 0, 1400, 1000), (Surface('desktop', 0, 1400, 1000),))
        self.observer = patch.object(self.pet.desktop_observer, 'snapshot', return_value=self.scene)
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

    def test_gift_is_driven_by_receiving_pack_and_clears_on_hide(self):
        self.assertEqual(self.pet.runtime.entry('interactions', 'star', {'source': 'remote'}), 'started')
        self.pet.advance_remote()
        self.assertEqual(self.pet.gift['symbol'], '★')
        self.assertGreater(self.pet.gift_until, 0)
        self.assertEqual(self.pet.companion_memory.entry('test.cat')['interactions'], 1)
        self.pet.hide()
        self.assertIsNone(self.pet.gift)

    def test_focus_temporarily_pauses_remote_and_autonomy_and_finishes(self):
        now = [100.0]
        self.pet.focus = FocusSession(lambda: now[0])
        self.pet.focus.start(1)
        self.pet.focus_changed()
        self.assertTrue(self.pet.buddy.focus_paused)
        self.assertFalse(self.pet.config.paused)
        self.assertFalse(self.pet.explore_action.isEnabled())
        self.pet.advance_autonomy()
        self.assertIsNone(self.pet.runtime.current)
        now[0] += 60
        self.pet.tick()
        self.assertFalse(self.pet.focus.active)
        self.assertFalse(self.pet.buddy.focus_paused)
        self.assertEqual(self.pet.runtime.current, 'celebrate')

    def test_creator_gif_regions_rules_export_and_import(self):
        source = Path(self.temp.name)/'source.gif'
        first = Image.new('RGBA', (40, 40), '#FFBB99')
        first.save(source, save_all=True, append_images=[Image.new('RGBA', (40, 40), '#AABBDD')], duration=[80, 120], loop=0)
        editor = CreatorDialog(self.pet)
        try:
            editor.new_base(str(source))
            self.assertIsNotNone(editor.folder)
            add_action(SimpleNamespace(folder=editor.folder, image=source, id='stretching', label='伸伸懒腰', remote=False))
            editor.refresh()
            editor.name.setText('我的新搭子')
            editor.identifier.setText('creator.unique')
            editor.trigger.setCurrentIndex(editor.trigger.findData('pointer.click'))
            editor.set_region([0, 0, 20, 20])
            editor.remote.setChecked(True)
            exported = editor.export(target=Path(self.temp.name)/'created.petpack')
            self.assertIsNotNone(exported, editor.message.text())
            package = PackageLibrary(Path(self.temp.name)/'created-library').install(exported)
            self.assertEqual(package.name, '我的新搭子')
            self.assertEqual([f['durationMs'] for f in package.behavior['animations']['rest']['frames']], [80, 120])
            runtime = BehaviorRuntime(package)
            self.assertEqual(runtime.event('pointer.click', (30, 30)), 'unhandled')
            self.assertEqual(runtime.event('pointer.click', (10, 10)), 'started')
            self.assertEqual(runtime.current, 'stretching')
            self.assertEqual(len(package.behavior['interactions']), 1)
            self.assertFalse(editor.dirty)
        finally:
            editor.dirty = False
            editor.close()
            if editor.preview.player:
                editor.preview.player.close()
            editor.temp.cleanup()
            editor.deleteLater()


if __name__ == '__main__':
    unittest.main()
