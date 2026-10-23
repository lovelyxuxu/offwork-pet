from copy import deepcopy
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest

from offwork_pet.autonomy import AutonomyController
from offwork_pet.behavior import BehaviorRuntime
from offwork_pet.core import Config, load_config, save_config, validate
from offwork_pet.desktop_scene import Rect, Scene, Surface, select_monitor, visible_surfaces
from offwork_pet.packages import PackageError, PackageLibrary
from pack_fixtures import create_pack


def autonomous_pack(manifest, behavior):
    manifest['requires'] += ['behavior.autonomy/v1', 'desktop.motion/v1']
    behavior['animations']['movement']['frames'][0]['durationMs'] = 2000
    behavior['actions']['stretch']['motion'] = {'direction': 1, 'speed': 80, 'startMs': 0, 'endMs': 1800}
    behavior['autonomy'] = {'states': ['napping'], 'foot': [16, 32],
                            'activities': [{'action': 'stretch', 'weight': 2}]}


class AutonomousTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        archive, _ = create_pack(self.temp.name, mutate=autonomous_pack)
        self.package = PackageLibrary(Path(self.temp.name)/'library').install(archive)
        self.now = 100.0
        self.runtime = BehaviorRuntime(self.package, clock=lambda: self.now)
        self.controller = AutonomyController(self.runtime, clock=lambda: self.now, rng=random.Random(7))
        self.config = Config(activity_min_seconds=5, activity_max_seconds=5)
        self.scene = Scene(Rect(0, 0, 1400, 1000), (Surface('desktop', 0, 1400, 1000),))
        self.position = (300, 736)

    def step(self, seconds=.05, blocked=False):
        self.now += seconds
        self.runtime.tick()
        destination = self.controller.update(self.config, self.scene, self.position,
            (140, 264), (280, 280), 1, blocked)
        if destination:
            self.position = destination
        return destination

    def start(self):
        self.step(0)
        self.step(5)
        self.assertEqual(self.runtime.current, 'stretch')

    def test_elapsed_interval_motion_and_delay_from_completion(self):
        self.start()
        self.step(.1)
        self.assertGreater(self.position[0], 300)
        self.assertEqual(self.position[1], 736)
        self.step(3)
        self.assertIsNone(self.runtime.current)
        due = self.controller.next_due
        self.assertEqual(due, self.now+5)
        self.step(4.9)
        self.assertIsNone(self.runtime.current)
        self.step(.2)
        self.assertEqual(self.runtime.current, 'stretch')
        self.assertFalse(self.runtime.queue)

    def test_manual_remote_actions_preempt_and_no_catchup(self):
        self.start()
        self.controller.interrupt(self.config)
        self.runtime.request('stretch', {'source': 'remote'})
        before = self.position
        self.step(.1)
        self.assertEqual(self.position, before)
        self.assertEqual(self.runtime.context['source'], 'remote')
        self.step(300, blocked=True)
        self.step(100, blocked=True)
        self.step(0)
        self.assertIsNone(self.runtime.current)
        self.assertEqual(self.controller.next_due, self.now+5)
        self.assertFalse(self.runtime.queue)

    def test_landing_then_walking_and_following_moved_window(self):
        self.scene = Scene(self.scene.bounds, (Surface('ledge', 100, 1100, 700, 100), *self.scene.surfaces))
        self.position = (300, 400)
        self.start()
        self.step(.1)
        self.assertIsNone(self.runtime.current)
        self.step(.1)
        self.assertEqual(self.position[1], 436)
        self.step(.1)
        self.assertEqual(self.runtime.current, 'stretch')
        self.step(.1)
        before = self.position
        self.scene = Scene(self.scene.bounds, (Surface('ledge', 300, 1300, 740, 300), Surface('desktop', 0, 1400, 1000)))
        self.step(.1)
        self.assertAlmostEqual(self.position[0]-before[0], 208)
        self.assertAlmostEqual(self.position[1], 476)
        # Minimizing the supporting window removes it; descend to a real surface.
        self.scene = Scene(self.scene.bounds, (Surface('desktop', 0, 1400, 1000),))
        for _ in range(12):
            self.step(.1)
        self.assertEqual(self.position[1], 736)

    def test_stay_range_edges_and_off_disables_only_motion(self):
        self.config.roaming_mode = 'stay'
        self.position = (300, 200)
        self.start()
        for _ in range(17):
            self.step(.1)
        self.assertLessEqual(self.position[0], 420)
        self.assertEqual(self.position[1], 200)
        self.config.roaming_mode = 'off'
        self.step(10)
        self.step(10)
        self.assertIsNone(self.runtime.current)
        del self.runtime.data['actions']['stretch']['motion']
        self.step(5)
        self.assertEqual(self.runtime.current, 'stretch')

    def test_fullscreen_disabled_suspended_and_package_without_autonomy(self):
        self.start()
        self.scene = Scene(self.scene.bounds, self.scene.surfaces, fullscreen=True)
        self.step()
        self.assertIsNone(self.runtime.current)
        self.scene = Scene(self.scene.bounds, self.scene.surfaces)
        self.config.autonomous_enabled = False
        self.step(30)
        self.assertIsNone(self.runtime.current)
        self.config.autonomous_enabled = True
        self.runtime.suspend(True)
        self.step(30)
        self.assertIsNone(self.runtime.current)

    def test_screen_too_small_for_landing_never_moves_outside_bounds(self):
        self.scene = Scene(Rect(0, 0, 200, 200), (Surface('desktop', 0, 200, 200),))
        self.position = (0, 0)
        self.start()
        self.step(.1)
        self.assertEqual(self.position, (0, 0))
        self.assertIsNone(self.runtime.current)

    def test_single_trip_never_exceeds_distance_budget(self):
        self.runtime.data['actions']['stretch']['motion']['speed'] = 240
        self.start()
        for _ in range(18):
            self.step(.1)
        self.assertAlmostEqual(self.position[0]-300, 180)
        self.assertIsNone(self.runtime.current)
        self.runtime.suspend(False)
        self.controller.spec = {}
        self.step(30)
        self.assertIsNone(self.runtime.current)

    def test_nonrepetition_weights_and_interrupted_tasks_are_cleared(self):
        action = deepcopy(self.runtime.data['actions']['stretch'])
        self.runtime.data['actions']['second'] = action
        self.controller.spec['activities'].append({'action': 'second', 'weight': 2})
        self.controller.last_action = 'stretch'
        class Picker:
            weights = None
            def uniform(self, a, b):
                return a
            def choices(self, values, weights):
                self.weights = weights
                return [values[-1]]
        picker = Picker()
        self.controller.rng = picker
        self.step(0)
        self.step(5)
        self.assertEqual(picker.weights, [.4, 2])
        self.assertEqual(self.runtime.current, 'second')
        self.controller.interrupt(self.config, detach=True)
        self.assertIsNone(self.controller.pending_action)
        self.assertIsNone(self.controller.support)
        self.assertIsNone(self.controller.home)


class DeclarationTests(unittest.TestCase):
    def test_invalid_motion_and_autonomy_declarations(self):
        mutations = [lambda m,b: m['requires'].remove('behavior.autonomy/v1'),
            lambda m,b: m['requires'].remove('desktop.motion/v1'),
            lambda m,b: b['autonomy'].update(states=['unknown']),
            lambda m,b: b['autonomy'].update(activities=[]),
            lambda m,b: b['autonomy'].update(foot=[100, 32]),
            lambda m,b: b['autonomy']['activities'][0].update(action='unknown'),
            lambda m,b: b['autonomy']['activities'][0].update(weight=float('inf')),
            lambda m,b: b['actions']['stretch']['motion'].update(direction=True),
            lambda m,b: b['actions']['stretch']['motion'].update(speed=-1),
            lambda m,b: b['actions']['stretch']['motion'].update(endMs=3000),
            lambda m,b: b['actions']['stretch']['motion'].update(startMs=1900)]
        with TemporaryDirectory() as folder:
            library = PackageLibrary(Path(folder)/'library')
            for index, mutate in enumerate(mutations):
                def configure(m, b):
                    autonomous_pack(m, b)
                    mutate(m, b)
                with self.subTest(index=index):
                    archive, _ = create_pack(folder, version=f'1.0.{index}', mutate=configure)
                    with self.assertRaises(PackageError):
                        library.install(archive)

    def test_old_settings_keep_defaults_and_roundtrip_new_fields(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'config.json'
            path.write_text('{"character":"custom","scale":125}', encoding='utf-8')
            config, warning = load_config(path)
            self.assertFalse(warning)
            self.assertEqual((config.character, config.scale, config.activity_min_seconds), ('custom', 125, 20))
            config.autonomous_enabled, config.roaming_mode = False, 'stay'
            config.activity_min_seconds, config.activity_max_seconds = 35, 95
            save_config(config, path)
            self.assertEqual(load_config(path)[0], config)
        for changes in ({'activity_min_seconds': True}, {'activity_max_seconds': 4},
                        {'activity_min_seconds': 100, 'activity_max_seconds': 30},
                        {'autonomous_enabled': 1}, {'roaming_mode': 'unknown'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(Config(**changes))

    def test_visible_segments_occlusion_and_negative_monitor_coordinates(self):
        bounds = Rect(-1920, -200, 0, 880)
        surfaces = visible_surfaces(bounds, [('front', Rect(-1200, 0, -700, 600)),
            ('back', Rect(-1800, 300, -100, 800)), ('hidden', Rect(-1000, 100, -800, 500))])
        back = [item for item in surfaces if item.key == 'back']
        self.assertEqual([(s.left, s.right, s.y) for s in back], [(-1800, -1200, 300), (-700, -100, 300)])
        self.assertFalse(any(item.key == 'hidden' for item in surfaces))
        self.assertEqual(surfaces[-1], Surface('desktop', -1920, 0, 880))

    def test_friendly_monitor_names_and_mixed_dpi_keep_native_origins(self):
        native = {'device1': Rect(0, 0, 1920, 1080), 'device2': Rect(-2560, -200, 0, 1240)}
        self.assertEqual(select_monitor(native, 'EDID product name', Rect(-2560, -200, -1280, 520), 2), native['device2'])
        self.assertEqual(select_monitor(native, 'device1', Rect(0, 0, 1920, 1080), 1), native['device1'])
        self.assertIsNone(select_monitor(native, 'unknown', Rect(100, 100, 500, 500), 1.5))

    def test_all_bundled_roles_opt_in_and_preserve_optional_walking(self):
        bundle = Path(__file__).resolve().parents[1]/'offwork_pet/assets/starter-packs'
        with TemporaryDirectory() as folder:
            library = PackageLibrary(folder)
            for archive in bundle.glob('*.petpack'):
                package = library.install(archive)
                self.assertTrue(package.behavior['autonomy']['activities'])
                if package.id in ('noah-lulu', 'codex-lumei', 'monthly-salary-cat'):
                    directions = {a['motion']['direction'] for a in package.behavior['actions'].values() if 'motion' in a}
                    self.assertEqual(directions, {-1, 1})
                else:
                    self.assertFalse(any('motion' in a for a in package.behavior['actions'].values()))


if __name__ == '__main__':
    unittest.main()
