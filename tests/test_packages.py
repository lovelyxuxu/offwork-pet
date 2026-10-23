from copy import deepcopy
from pathlib import Path
import json
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from offwork_pet.packages import PackageLibrary, PackageError, load_package
from offwork_pet.behavior import BehaviorRuntime
from offwork_pet.package_player import FrameCache, PackagePlayer
from pack_fixtures import create_pack


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = PackageLibrary(self.root / 'library')

    def tearDown(self):
        self.temp.cleanup()

    def test_unrelated_action_sets_and_static_character(self):
        for identifier, state, action in [('test.cat', 'napping', 'stretch'), ('test.bot', 'charging', 'transform')]:
            archive, _ = create_pack(self.root, identifier, initial=state, action=action)
            package = self.library.install(archive)
            self.assertEqual(package.behavior['initialState'], state)
            self.assertEqual(list(package.behavior['actions']), [action])
        archive, _ = create_pack(self.root, 'test.still', static=True)
        self.assertEqual(self.library.install(archive).behavior['actions'], {})

    def test_bad_references_and_missing_images_preserve_installed_version(self):
        archive, _ = create_pack(self.root)
        old = self.library.install(archive)
        for mutate in [lambda m, b: b.update(initialState='missing'),
                       lambda m, b: b['actions']['stretch'].update(animation='missing'),
                       lambda m, b: b['animations']['breath']['frames'][0].update(file='missing.png'),
                       lambda m, b: b['rules'][0].update(then={'type': 'exec', 'command': 'bad'}),
                       lambda m, b: m.update(requires=['unknown/v1']),
                       lambda m, b: b['regions']['ear'].update(rect=[0, 0, 999, 999])]:
            archive, _ = create_pack(self.root, version='2.0.0', mutate=mutate)
            with self.assertRaises(PackageError):
                self.library.install(archive)
            self.assertEqual(self.library.load('test.cat').digest, old.digest)
        self.assertFalse(list(self.library.root.glob('.import-*')))

    def test_paths_duplicates_and_symlinks_are_rejected_before_install(self):
        for path in ('../outside.txt', '/absolute.txt', 'C:/outside.txt', 'assets\\outside.txt', 'CON.txt'):
            archive = self.root / 'bad.petpack'
            with ZipFile(archive, 'w') as zipped:
                zipped.writestr(path, 'bad')
            with self.assertRaises(PackageError):
                self.library.install(archive)
        with ZipFile(archive, 'w') as zipped:
            zipped.writestr('A.txt', 'one')
            zipped.writestr('a.txt', 'two')
        with self.assertRaises(PackageError):
            self.library.install(archive)
        with ZipFile(archive, 'w') as zipped:
            link = ZipInfo('link.txt')
            link.external_attr = 0o120777 << 16
            zipped.writestr(link, '/outside')
        with self.assertRaises(PackageError):
            self.library.install(archive)
        self.assertEqual(self.library.entries(), [])

    def test_update_conflict_rollback_and_last_character_deletion(self):
        archive, _ = create_pack(self.root)
        first = self.library.install(archive)
        self.assertEqual(self.library.install(archive).digest, first.digest)
        conflicting, _ = create_pack(self.root, mutate=lambda m, b: m.update(displayName='Different'))
        with self.assertRaises(PackageError):
            self.library.install(conflicting)
        updated, _ = create_pack(self.root, version='2.0.0')
        with patch.object(self.library, '_save', side_effect=OSError('disk full')):
            with self.assertRaises(PackageError):
                self.library.install(updated)
        self.assertEqual(self.library.load('test.cat').digest, first.digest)
        second = self.library.install(updated)
        self.assertNotEqual(first.root, second.root)
        self.library.remove('test.cat')
        self.assertEqual(self.library.entries(), [])
        self.assertEqual(self.library.install(archive).id, 'test.cat')

    def test_new_bundled_role_is_added_once_without_restoring_deleted_roles(self):
        bundle = self.root / 'bundle'
        bundle.mkdir()
        cat, _ = create_pack(self.root)
        shutil.copy2(cat, bundle / 'cat.petpack')
        self.library.seed(bundle)
        self.library.remove('test.cat')
        robot, _ = create_pack(self.root, 'test.robot')
        shutil.copy2(robot, bundle / 'robot.petpack')
        self.library.seed(bundle)
        self.assertEqual([item['id'] for item in self.library.entries()], ['test.robot'])
        self.library.remove('test.robot')
        PackageLibrary(self.library.root).seed(bundle)
        self.assertEqual(PackageLibrary(self.library.root).entries(), [])

    def test_legacy_seed_boolean_migrates_using_bundle_metadata(self):
        bundle = self.root / 'bundle'
        bundle.mkdir()
        (bundle / 'bundle.json').write_text(json.dumps({'legacyPackageIds': ['test.cat']}), encoding='utf-8')
        for name in ('test.cat', 'test.new'):
            archive, _ = create_pack(self.root, name)
            shutil.copy2(archive, bundle / (name+'.petpack'))
        self.library.index['seeded'] = True
        self.library.seed(bundle)
        self.assertEqual([item['id'] for item in self.library.entries()], ['test.new'])

    def test_bundled_update_migrates_known_contents_and_keeps_deleted_or_custom_roles(self):
        bundle = self.root / 'bundle'
        bundle.mkdir()
        archive, _ = create_pack(self.root)
        old = self.library.install(archive)
        self.library.index.update(seeded=True, seededPackages=['test.cat'])
        (bundle / 'bundle.json').write_text(json.dumps({'upgradeFrom': {'test.cat': [old.digest]}}), encoding='utf-8')
        new, _ = create_pack(self.root, version='2.0.0')
        shutil.copy2(new, bundle / 'cat.petpack')
        self.library.seed(bundle)
        self.assertEqual(self.library.load('test.cat').manifest['version'], '2.0.0')
        self.library.remove('test.cat')
        self.library.seed(bundle)
        self.assertEqual(self.library.entries(), [])
        custom, _ = create_pack(self.root, mutate=lambda m, b: m.update(displayName='My version'))
        custom_digest = self.library.install(custom).digest
        self.library.seed(bundle)
        self.assertEqual(self.library.load('test.cat').digest, custom_digest)

    def test_noah_lulu_package_preserves_source_frames_and_attribution(self):
        archive = Path(__file__).resolve().parents[1] / 'offwork_pet/assets/starter-packs/noah-lulu.petpack'
        package = self.library.install(archive)
        self.assertEqual(package.manifest['author'], 'noah')
        self.assertIn('noah-lulu', package.manifest['credits'][0]['source'])
        self.assertEqual(package.manifest['version'], '1.5.0')
        self.assertEqual(len(package.behavior['autonomy']['activities']), 6)
        self.assertEqual(len(package.behavior['pointerLook']['directions']), 16)
        self.assertEqual(len(package.behavior['interactions']), 7)
        self.assertIn('exploration', package.behavior['autonomy'])
        self.assertIn('ball', package.behavior['toys'])
        self.assertTrue({'look_right', 'look_left'} <= {entry['id'] for entry in package.behavior['menu']})
        self.assertEqual(len(package.behavior['animations']['run_right']['frames']), 9)
        runtime = BehaviorRuntime(package)
        self.assertEqual(runtime.entry('menu', 'wave'), 'started')
        self.assertEqual(runtime.current, 'wave')
        for action_id in ('look_right', 'look_left'):
            runtime.cancel()
            self.assertEqual(runtime.entry('menu', action_id), 'started')
            self.assertEqual(len(package.behavior['animations'][action_id]['frames']), 10)
        player = PackagePlayer(runtime)
        player.cache.image(package, runtime.frame()['file'])
        self.assertLessEqual(player.cache.bytes, player.cache.budget)
        player.close()

    def test_decoded_byte_cache_and_canvas_hit_testing(self):
        archive, _ = create_pack(self.root)
        package = self.library.install(archive)
        cache = FrameCache(4096)
        cache.image(package, 'body.png')
        self.assertEqual(cache.bytes, 4096)
        second = deepcopy(package)
        second.digest = 'different'
        cache.image(second, 'body.png')
        self.assertEqual(len(cache.items), 1)
        self.assertEqual(next(iter(cache.items))[0], 'different')
        player = PackagePlayer(BehaviorRuntime(package))
        self.assertTrue(player.contains(QPointF(140, 200)))
        self.assertFalse(player.contains(QPointF(10, 10)))
        player.close()
        self.assertEqual(player.cache.bytes, 0)

    def test_codex_lumei_installs_and_plays_independently(self):
        archive = Path(__file__).resolve().parents[1] / 'offwork_pet/assets/starter-packs/codex-lumei.petpack'
        package = self.library.install(archive)
        self.assertEqual(package.manifest['author'], 'FullStackHan')
        self.assertEqual(package.id, 'codex-lumei')
        self.assertIn('https://codex-pets.net/#/pets/lumei', package.manifest['credits'][0]['source'])
        self.assertTrue({'wave', 'jump', 'run_left', 'run_right', 'look_right', 'look_left'} <= package.behavior['actions'].keys())
        self.assertIn('look_right', {entry['id'] for entry in package.behavior['menu']})
        self.assertIn('look_left', {entry['id'] for entry in package.behavior['menu']})
        self.assertEqual(len(package.behavior['interactions']), 7)
        self.assertGreaterEqual(sum(frame['durationMs'] for frame in package.behavior['animations']['rest']['frames']), 5000)
        self.assertGreaterEqual(sum(frame['durationMs'] for frame in package.behavior['animations']['wave']['frames']), 1500)
        self.assertEqual(package.behavior['maxQueuedActions'], 1)
        for entry in package.behavior['menu']:
            runtime = BehaviorRuntime(package)
            self.assertEqual(runtime.entry('menu', entry['id']), 'started')
            player = PackagePlayer(runtime)
            self.assertFalse(player.cache.image(package, runtime.frame()['file']).isNull())
            player.close()


    def test_salary_cat_preserves_all_source_actions_and_returns_to_desk(self):
        import hashlib
        archive = Path(__file__).resolve().parents[1] / 'offwork_pet/assets/starter-packs/monthly-salary-cat.petpack'
        package = self.library.install(archive)
        self.assertEqual(package.manifest['author'], 'vjokaye')
        self.assertEqual(hashlib.sha256(package.asset('assets/spritesheet.webp').read_bytes()).hexdigest(),
                         '69c4853413844bc8a91b986583dc47c8bdb99270dcad2e461afd81a881c7e4cc')
        self.assertEqual(len(package.behavior['menu']), 9)
        self.assertEqual(len(package.behavior['interactions']), 10)
        self.assertEqual(package.manifest['version'], '1.3.0')
        self.assertEqual([entry['label'] for entry in package.behavior['menu']],
                         ['额', '拍一拍', '向右飘过', '向左飘过', '蛐蛐', '臭', '生气', '无聊', '意念回复'])
        labels = {entry['id']: entry['label'] for entry in package.behavior['menu']}
        for entry in package.behavior['interactions']:
            if entry['id'] in labels:
                self.assertEqual(entry['label'], labels[entry['id']])
            else:
                self.assertIn('gift', package.behavior['actions'][entry['invoke']['id']])
        self.assertEqual(package.behavior['animations']['base']['frames'][0]['rect'][1], 8*208)
        source_counts = {0: 6, 1: 8, 2: 8, 3: 4, 4: 5, 5: 8, 6: 6, 7: 6, 8: 6}
        seen = {}
        for entry in package.behavior['menu']:
            now = [100.0]
            runtime = BehaviorRuntime(package, clock=lambda: now[0])
            self.assertEqual(runtime.entry('menu', entry['id']), 'started')
            frames = package.behavior['animations'][entry['id']]['frames']
            row = frames[0]['rect'][1] // 208
            authored = frames[:-1]
            seen[row] = len({tuple(frame['rect']) for frame in authored})
            self.assertEqual(len(authored), source_counts[row] * 3)
            self.assertGreaterEqual(runtime.duration(entry['id']), 4.4)
            self.assertLessEqual(runtime.duration(entry['id']), 6.1)
            player = PackagePlayer(runtime)
            self.assertFalse(player.cache.image(package, runtime.frame()['file']).isNull())
            # The former ~2-second cutoff must leave this action playing.
            now[0] += 2.5
            runtime.tick()
            self.assertEqual(runtime.current, entry['id'])
            now[0] = runtime.started + runtime.duration(entry['id']) + .1
            runtime.tick()
            self.assertIsNone(runtime.current)
            self.assertEqual(runtime.frame()['rect'][1], 8*208)
            player.close()
        self.assertEqual(seen, source_counts)
        for event in ('pointer.click', 'pointer.hold', 'gesture.stroke'):
            runtime = BehaviorRuntime(package)
            self.assertEqual(runtime.event(event, (90, 40)), 'started')
            self.assertEqual(runtime.current, 'chair_rest')
        now = [100.0]
        runtime = BehaviorRuntime(package, clock=lambda: now[0])
        self.assertEqual(runtime.entry('menu', 'phone_break'), 'started')
        self.assertEqual(runtime.entry('menu', 'chair_rest'), 'queued')
        now[0] += runtime.duration('phone_break') + .1
        runtime.tick()
        self.assertEqual(runtime.current, 'chair_rest')


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        _, source = create_pack(self.temp.name)
        self.package = load_package(source)
        self.now = 100.0
        self.runtime = BehaviorRuntime(self.package, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def advance(self, seconds):
        self.now += seconds
        self.runtime.tick()

    def test_time_completion_and_declared_state_without_idle(self):
        self.assertEqual(self.runtime.event('pointer.click', (16, 16)), 'started')
        self.assertEqual(self.runtime.current, 'stretch')
        self.advance(.31)
        self.assertIsNone(self.runtime.current)
        self.assertEqual(self.runtime.state, 'napping')

    def test_queue_expiry_duplicate_cooldown_and_pause(self):
        self.assertEqual(self.runtime.entry('menu', 'menu'), 'started')
        self.assertEqual(self.runtime.entry('menu', 'menu'), 'cooldown')
        self.advance(.11)
        self.assertEqual(self.runtime.request('stretch', expires=self.now+.1), 'queued')
        self.assertEqual(self.runtime.request('stretch'), 'busy')
        self.advance(.21)
        self.assertEqual(self.runtime.queue, [])
        self.assertIn('expired', [item[0] for item in self.runtime.outcomes])
        self.runtime.suspend(True)
        self.assertEqual(self.runtime.request('stretch'), 'paused')
        self.advance(10)
        elapsed = self.runtime.state_elapsed
        self.runtime.suspend(False)
        self.advance(.1)
        self.assertAlmostEqual(self.runtime.state_elapsed, elapsed+.1)

    def test_higher_priority_interrupt_and_stale_instance_cleanup(self):
        spec = deepcopy(self.package.behavior['actions']['stretch'])
        spec.update(priority=20, onBusy='replace')
        self.package.behavior['actions']['other'] = spec
        self.runtime.request('stretch', {'source': 'remote'})
        self.assertEqual(self.runtime.request('other', {'source': 'local'}), 'started')
        self.assertEqual(self.runtime.current, 'other')
        revision = self.runtime.revision
        self.runtime.close()
        self.assertNotEqual(self.runtime.revision, revision)
        self.assertEqual(self.runtime.request('stretch'), 'paused')
        self.assertEqual(self.runtime.queue, [])

    def test_package_can_limit_pending_actions_without_fixed_action_names(self):
        self.runtime.data['maxQueuedActions'] = 1
        self.runtime.request('stretch')
        for key in ('a', 'b'):
            self.runtime.data['actions'][key] = {**deepcopy(self.package.behavior['actions']['stretch']), 'onBusy': 'enqueue'}
        self.assertEqual(self.runtime.request('a'), 'queued')
        self.assertEqual(self.runtime.request('b'), 'busy')
        self.assertEqual(len(self.runtime.queue), 1)

    def test_state_timer_and_rule_conditions_are_bounded(self):
        self.package.behavior['rules'].append({'id': 'timer', 'event': 'timer', 'afterMs': 100,
            'then': {'type': 'action', 'id': 'stretch'}})
        self.advance(.12)
        self.assertEqual(self.runtime.current, 'stretch')
        self.advance(.4)
        self.assertIsNone(self.runtime.current)
        self.advance(.4)
        self.assertIsNone(self.runtime.current)
        operation = {'type': 'random', 'choices': []}
        operation['choices'].append({'invoke': operation})
        with self.assertRaises(PackageError):
            self.runtime.invoke(operation)

    def test_transition_limit_recovers_once_then_stops_without_recursive_failure(self):
        for _ in range(32):
            self.runtime.set_state('napping')
        with self.assertRaises(PackageError) as failure:
            self.runtime.set_state('napping')
        self.runtime.fail(failure.exception)
        self.assertFalse(self.runtime.stopped)
        self.assertEqual(self.runtime.state, 'napping')
        self.runtime.fail(PackageError('failed again'))
        self.assertTrue(self.runtime.stopped)


if __name__ == '__main__':
    unittest.main()
