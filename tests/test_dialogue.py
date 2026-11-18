from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, PropertyMock

from offwork_pet.core import Config, load_config, save_config
from offwork_pet.dialogue import DialogueError, DialogueLibrary, DialogueRuntime, atomic_json, read_pack, scene_timing, validate_pack

ASSET = Path(__file__).resolve().parents[1] / 'offwork_pet/assets/dialogue-packs/gugagaga-daily.petdialogue'


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = DialogueLibrary(self.root / 'packs')
        self.pack = self.library.install(ASSET)
        self.runtime = DialogueRuntime(self.library, self.root / 'history.json', random.Random(7))
        self.config = Config(character='gugagaga')
        self.runtime.switch('gugagaga', self.config.dialogue_packs)
        self.now = datetime(2026, 10, 27, 15)

    def tearDown(self):
        self.temp.cleanup()

    def test_content_has_360_unique_lines_and_30_optional_scenes(self):
        self.assertEqual(len(self.pack['scenes']), 30)
        lines = [line for scene in self.pack['scenes'] for line in scene['lines']]
        self.assertEqual((len(lines), len(set(lines))), (360, 360))
        small = deepcopy(self.pack)
        small['scenes'] = [small['scenes'][-1]]
        small['scenes'][0]['id'] = 'my-own-scene'
        small['scenes'][0]['label'] = '自己的话题'
        validate_pack(small)

    def test_every_minute_matches_one_launch_scene_without_assuming_work(self):
        scenes = [scene for scene in self.pack['scenes'] if scene.get('when', {}).get('firstLaunchOfDay') is True]
        for minute in range(1440):
            now = self.now.replace(hour=minute // 60, minute=minute % 60)
            matched = [scene for scene in scenes if self.runtime.matching(scene, 'app.launch', now, self.config, {})]
            self.assertEqual(len(matched), 1, now)
        self.assertFalse(any(self.runtime.matching(scene, 'schedule.work-start', self.now, self.config, {'observedCrossing': True}) for scene in self.pack['scenes']))

    def test_launch_uses_afternoon_and_restart_does_not_repeat_greeting(self):
        self.assertTrue(self.runtime.emit('app.launch', self.now, 100, self.config))
        self.assertIn(self.runtime.text, self.pack['scenes'][3]['lines'])
        again = DialogueRuntime(self.library, self.root / 'history.json')
        again.switch('gugagaga', {})
        self.assertFalse(again.emit('app.launch', self.now + timedelta(seconds=10), 200, self.config))
        self.assertTrue(again.emit('app.launch', self.now + timedelta(minutes=35), 2500, self.config))
        self.assertIn(again.text, self.pack['scenes'][6]['lines'])

    def test_launch_after_work_does_not_emit_work_start_or_offwork(self):
        self.config.start_time = '09:00'
        self.runtime.tick(self.now.replace(hour=19), 100, self.config)
        self.assertIn(self.runtime.text, self.pack['scenes'][4]['lines'])
        self.runtime.tick(self.now.replace(hour=19, second=1), 101, self.config)
        self.assertNotIn(self.runtime.text, self.pack['scenes'][7]['lines'])

    def test_shuffle_rounds_are_unique_and_boundary_does_not_repeat(self):
        scene = self.pack['scenes'][-1]
        for _ in range(20):
            before = self.runtime.recent[-1] if self.runtime.recent else None
            values = [self.runtime.draw(scene) for _ in range(12)]
            self.assertEqual(len(set(values)), 12)
            self.assertNotEqual(before, values[0])

    def test_wrong_role_and_invalid_import_leave_active_pack_untouched(self):
        before = list(self.library.root.iterdir())
        with self.assertRaises(DialogueError):
            self.library.install(ASSET, 'noah-lulu')
        self.assertEqual(before, list(self.library.root.iterdir()))
        self.assertIs(self.pack, self.library.selected('gugagaga', {}))

    def test_unknown_conditions_formats_and_host_override_are_rejected(self):
        for mutate in (lambda p: p.update(format='offwork-pet.dialogue/v99'),
                       lambda p: p['scenes'][0]['when'].update(screenIsWorking=True),
                       lambda p: p['selection'].update(respectHostVisibilityAndQuietSettings=False),
                       lambda p: p['scenes'][0].update(trigger='run.python'),
                       lambda p: p['scenes'][0]['when'].update(firstLaunchOfDay=1)):
            data = deepcopy(self.pack)
            mutate(data)
            with self.assertRaises(DialogueError):
                validate_pack(data)

    def test_duplicate_keys_large_files_and_same_version_changes_are_rejected(self):
        path = self.root / 'bad.petdialogue'
        path.write_text('{"id":"one","id":"two"}', encoding='utf-8')
        with self.assertRaises(DialogueError):
            read_pack(path)
        path.write_bytes(b' ' * (1024 * 1024 + 1))
        with self.assertRaises(DialogueError):
            read_pack(path)
        changed = deepcopy(self.pack)
        changed['scenes'][0]['lines'][0] = '同版不能换内容'
        atomic_json(path, changed)
        with self.assertRaises(DialogueError):
            self.library.install(path)
        self.assertEqual(self.library.packs[self.pack['id']], self.pack)

    def test_per_character_selection_off_persists_and_seed_does_not_enable(self):
        self.config.dialogue_packs['gugagaga'] = ''
        self.runtime.switch('gugagaga', self.config.dialogue_packs)
        self.assertIsNone(self.runtime.pack)
        self.library.seed(ASSET.parent)
        self.assertIsNone(self.library.selected('gugagaga', self.config.dialogue_packs))
        self.assertIsNone(self.library.selected('noah-lulu', {}))
        path = self.root / 'config.json'
        save_config(self.config, path)
        self.assertEqual(load_config(path)[0].dialogue_packs, {'gugagaga': ''})

    def test_multiple_packs_choose_only_one_and_preserve_explicit_choice(self):
        other = deepcopy(self.pack)
        other['id'] = 'gugagaga.another'
        other['displayName'] = '另一种口吻'
        path = self.root / 'another.petdialogue'
        atomic_json(path, other)
        self.library.install(path, 'gugagaga')
        self.assertEqual(len(self.library.entries('gugagaga')), 2)
        self.config.dialogue_packs['gugagaga'] = self.pack['id']
        self.runtime.switch('gugagaga', self.config.dialogue_packs)
        self.assertEqual(self.runtime.pack['id'], self.pack['id'])
        self.config.dialogue_packs['gugagaga'] = other['id']
        self.runtime.switch('gugagaga', self.config.dialogue_packs)
        self.assertEqual(self.runtime.pack['id'], other['id'])

    def test_corrupt_local_history_is_preserved_without_breaking_dialogue(self):
        path = self.root / 'bad-history.json'
        path.write_text('{"roles":{"gugagaga":{"last":{"bad":"not-a-time"}}}}', encoding='utf-8')
        original = path.read_bytes()
        runtime = DialogueRuntime(self.library, path)
        runtime.switch('gugagaga', {})
        self.assertTrue(runtime.emit('app.launch', self.now, 100, self.config))
        self.assertEqual(path.read_bytes(), original)

    def test_hover_only_accumulates_time_but_does_not_consume_hidden_lines(self):
        self.config.dialogue_proactive = False
        self.runtime.launch_pending = False
        self.runtime.elapsed = 3100
        self.runtime.tick(self.now, 100, self.config, displayable=False)
        self.runtime.tick(self.now + timedelta(seconds=1), 101, self.config, displayable=False)
        self.assertEqual(self.runtime.elapsed, 3101)
        self.assertEqual(self.runtime.text, '')
        self.runtime.tick(self.now + timedelta(seconds=2), 102, self.config, displayable=True)
        self.assertTrue(self.runtime.text)

    def test_switching_character_clears_previous_sentence(self):
        self.runtime.emit('app.launch', self.now, 100, self.config)
        self.runtime.switch('noah-lulu', {})
        self.assertEqual(self.runtime.text, '')
        self.assertIsNone(self.runtime.pack)

    def test_hidden_paused_focus_and_blocked_do_not_consume(self):
        self.config.bubble_mode = 'hidden'
        self.assertFalse(self.runtime.emit('app.launch', self.now, 100, self.config))
        self.config.bubble_mode = 'hover_click'
        self.assertFalse(self.runtime.emit('app.launch', self.now, 100, self.config, available=False))
        self.assertFalse(self.runtime.emit('app.launch', self.now, 100, self.config, focus=True))
        self.config.paused = True
        self.assertFalse(self.runtime.emit('app.launch', self.now, 100, self.config))
        self.config.paused = False
        self.assertTrue(self.runtime.emit('app.launch', self.now, 100, self.config))

    def test_sleep_does_not_accumulate_work_or_backfill_work_start(self):
        self.config.start_time = '09:00'
        now = self.now.replace(hour=8, minute=59)
        self.runtime.tick(now, 100, self.config)
        self.runtime.elapsed = 2700
        self.runtime.tick(now + timedelta(hours=2), 7300, self.config)
        self.assertEqual(self.runtime.elapsed, 0)
        self.assertNotIn(self.runtime.text, self.pack['scenes'][7]['lines'])

    def test_work_start_requires_observed_crossing_and_not_config_change(self):
        self.config.start_time = '09:00'
        self.runtime.launch_pending = False
        before = self.now.replace(hour=8, minute=59, second=59)
        self.runtime.tick(before, 100, self.config)
        self.runtime.tick(before + timedelta(seconds=1), 101, self.config)
        self.assertIn(self.runtime.text, self.pack['scenes'][7]['lines'])
        self.runtime.clear()
        self.config.start_time = '09:01'
        self.runtime.tick(before + timedelta(seconds=61), 102, self.config)
        self.assertEqual(self.runtime.text, '')

    def test_one_meal_per_occurrence_even_with_pre_meal_and_meal_scenes(self):
        self.assertTrue(self.runtime.emit('schedule.meal', self.now.replace(hour=11, minute=40), 100, self.config))
        self.runtime.clear()
        self.assertFalse(self.runtime.emit('schedule.meal', self.now.replace(hour=12, minute=10), 1900, self.config))

    def test_care_counts_effective_time_and_respects_shared_spacing(self):
        self.runtime.elapsed = 3000
        self.assertTrue(self.runtime.emit('companion.care', self.now, 100, self.config))
        self.runtime.clear()
        self.assertFalse(self.runtime.emit('companion.care', self.now + timedelta(minutes=1), 160, self.config))
        self.assertEqual(self.runtime.last_care, 3000)
        self.runtime.tick(self.now + timedelta(minutes=2), 220, self.config, available=False)
        self.assertEqual(self.runtime.elapsed, 3000)

    def test_short_suppression_pauses_without_starving_care_reminders(self):
        self.runtime.launch_pending = False
        self.runtime.tick(self.now, 100, self.config, automatic=False)
        for second in range(1, 3101):
            self.runtime.tick(self.now+timedelta(seconds=second), 100+second, self.config,
                              available=second % 60 != 0, automatic=False)
        self.assertGreaterEqual(self.runtime.elapsed, 3000)
        self.runtime.tick(self.now+timedelta(seconds=3101), 3201, self.config)
        self.assertEqual(self.runtime.active_event, 'companion.care')

    def test_display_is_suppressed_and_explicit_pause_still_resets(self):
        self.runtime.elapsed = 1500
        self.runtime.emit('app.launch', self.now, 100, self.config)
        self.runtime.tick(self.now, 100, self.config, available=False)
        self.assertEqual(self.runtime.text, '')
        self.assertEqual(self.runtime.elapsed, 1500)
        self.config.paused = True
        self.runtime.tick(self.now+timedelta(seconds=1), 101, self.config)
        self.assertEqual(self.runtime.elapsed, 0)

    def test_scene_times_follow_user_meal_settings(self):
        scene = next(scene for scene in self.pack['scenes'] if scene['id'] == 'guga.lunch-radar')
        self.assertIn('11:30–12:00', scene_timing(scene, self.pack, self.config))
        self.config.meal_times['lunch'] = '13:00'
        self.assertIn('12:30–13:00', scene_timing(scene, self.pack, self.config))

    def test_clock_rollback_cannot_repeat_observed_event(self):
        facts = {'observedCrossing': True}
        now = self.now.replace(hour=18)
        self.assertTrue(self.runtime.emit('schedule.offwork', now, 100, self.config, facts=facts))
        self.runtime.clear()
        self.assertFalse(self.runtime.emit('schedule.offwork', now - timedelta(minutes=1), 200, self.config, facts=facts))

    def test_known_meal_schedule_and_preview_do_not_mutate_runtime_counts(self):
        self.config.meal_times = {'lunch': '14:00'}
        self.assertFalse(self.runtime.emit('schedule.meal', self.now.replace(hour=12), 100, self.config))
        self.assertTrue(self.runtime.emit('schedule.meal', self.now.replace(hour=13, minute=40), 100, self.config))


class DialogueGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from offwork_pet.app import Pet
        self.pet = Pet(Config(character='gugagaga', auto_update_check=False), persist=False)
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

    def test_dialogue_import_selection_preview_is_isolated_and_role_bound(self):
        from offwork_pet.dialogue_ui import DialogueDialog
        dialog = DialogueDialog(self.pet, 'gugagaga')
        self.assertEqual(dialog.scenes.count(), 30)
        before = deepcopy(self.pet.dialogue.state)
        first = dialog.sample.text()
        dialog.preview()
        self.assertNotEqual(dialog.sample.text(), first)
        self.assertEqual(self.pet.dialogue.state, before)
        dialog.packs.setCurrentIndex(0)
        dialog.activate()
        self.assertIsNone(self.pet.dialogue.pack)
        self.assertEqual(self.pet.config.dialogue_packs['gugagaga'], '')
        dialog.close()

    def test_real_bubble_wraps_role_text_and_obeys_hidden(self):
        from offwork_pet.desktop_scene import Scene, Rect
        with patch.object(self.pet, 'dialogue_available', return_value=True):
            self.assertTrue(self.pet.dialogue_event('pointer.click'))
        self.assertTrue(self.pet.show_bubble())
        image = self.pet.grab().toImage()
        self.assertFalse(image.isNull())
        self.pet.config.bubble_mode = 'hidden'
        self.assertFalse(self.pet.show_bubble())
        self.pet.select_package('noah-lulu')
        self.assertEqual(self.pet.dialogue.text, '')

    def test_settings_roundtrip_and_proactive_optout(self):
        from offwork_pet.app import SettingsDialog
        dialog = SettingsDialog(self.pet.config, self.pet)
        dialog.start_enabled.setChecked(True)
        dialog.checks['dialogue_proactive'].setChecked(False)
        updated = dialog.result_config(self.pet.config)
        self.assertEqual(updated.start_time, '09:00')
        self.assertEqual(updated.meal_times['lunch'], '12:00')
        self.pet.config = updated
        self.pet.hovered = False
        self.pet.dialogue.text = '咕，试一下。'
        self.pet.dialogue.until = float('inf')
        self.assertFalse(self.pet.show_bubble())
        self.pet.hovered = True
        self.assertTrue(self.pet.show_bubble())
        dialog.close()

    def test_dialogue_render_never_reads_countdown_and_has_larger_font(self):
        from offwork_pet.dialogue_bubble import speech_layout
        self.pet.dialogue.text = '咕嘎报到！今天也一起慢慢来。'
        self.pet.dialogue.until = float('inf')
        with patch.object(type(self.pet.snapshot), 'display', new_callable=PropertyMock) as display:
            self.pet.grab()
            display.assert_not_called()
        formatted, font = speech_layout(self.pet.dialogue.text)
        self.assertGreaterEqual(font.pointSizeF(), 13)
        self.assertEqual(formatted.replace('\n', ''), self.pet.dialogue.text)

    def test_manual_speech_preview_is_visible_and_does_not_change_history(self):
        from offwork_pet.dialogue_ui import DialogueDialog
        dialog = DialogueDialog(self.pet, 'gugagaga')
        self.pet.config.dialogue_proactive = False
        before = deepcopy(self.pet.dialogue.state)
        choices = deepcopy(self.pet.config.dialogue_packs)
        dialog.preview_on_pet()
        self.assertEqual(self.pet.dialogue.active_event, 'preview')
        self.assertEqual(self.pet.dialogue.text, dialog.sample.text())
        self.assertTrue(self.pet.show_bubble())
        self.assertEqual(self.pet.dialogue.state, before)
        self.assertEqual(self.pet.config.dialogue_packs, choices)
        dialog.close()

    def test_all_shipped_lines_fit_readable_bubble_without_losing_text(self):
        from PySide6.QtGui import QFontDatabase, QFontMetricsF
        from PySide6.QtCore import QRectF, Qt
        from offwork_pet.dialogue_bubble import speech_layout
        for name in ('msyh.ttc', 'simyou.ttf'):
            path = Path('C:/Windows/Fonts') / name
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        for scene in read_pack(ASSET)['scenes']:
            for line in scene['lines']:
                formatted, font = speech_layout(line)
                self.assertEqual(formatted.replace('\n', ''), line)
                self.assertGreaterEqual(font.pointSizeF(), 13)
                metrics = QFontMetricsF(font).boundingRect(QRectF(0,0,232,1000), Qt.TextFlag.TextWordWrap, formatted)
                self.assertLessEqual(metrics.height(), 66)
