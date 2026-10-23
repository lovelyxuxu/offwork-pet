"""Widget integration checks; use a Windows desktop for native font rendering."""
from time import monotonic
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QFocusEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from offwork_pet.app import Pet, SettingsDialog
from offwork_pet.core import Config


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = Pet(Config(character='lulu'), persist=False)
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
        self.assertIsNone(self.pet.press_position)

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


    def test_local_mode_does_not_connect_and_characters_are_selectable(self):
        self.assertFalse(self.pet.buddy.enabled)
        self.assertIsNone(self.pet.buddy.socket)
        self.assertIsNone(self.pet.buddy.identity)
        dialog = SettingsDialog(self.pet.config, self.pet)
        self.assertEqual(dialog.character.count(), len(self.pet.library.entries()) + 1)
        for character in ('lulu', 'lumei'):
            dialog.character.setCurrentIndex(dialog.character.findData(character))
            self.assertEqual(dialog.result_config(self.pet.config).character, character)
            self.pet.config.character = character
            self.pet.apply_config()
            self.assertFalse(self.pet.grab().isNull())
        dialog.deleteLater()

    def test_remote_expiry_dedup_and_local_action_priority(self):
        self.pet.buddy.ready = True
        self.pet.buddy.relation = {'id': 'relation', 'partner_id': 'other', 'character': 'lumei'}
        self.pet.buddy.clock_offset = 0
        event = {'interactionId': 'pinch', 'action_id': 'one', 'relation_id': 'relation',
                 'capabilityRevision': self.pet.runtime.revision, 'expires_at': time.time() + 4}
        with patch.object(self.pet.buddy, 'acknowledge') as ack:
            self.pet.receive_remote({**event, 'expires_at': time.time() - 1})
            ack.assert_called_with({**event, 'expires_at': unittest.mock.ANY}, False)
            self.assertIsNone(self.pet.runtime.current)
            self.pet.dragging = True
            self.pet.receive_remote(event)
            ack.assert_called_with(event, False)
            self.assertIsNone(self.pet.runtime.current)
            self.pet.dragging = False
            self.pet.receive_remote(event)
            self.assertEqual(self.pet.runtime.current, 'cheek_reply')
            self.pet.receive_remote(event)
            self.assertEqual(self.pet.runtime.queue, [])
            self.pet.set_paused(True)
            self.assertIsNone(self.pet.runtime.current)

    def test_remote_animation_budget(self):
        self.pet.buddy.ready = True
        self.pet.buddy.relation = {'id': 'relation', 'partner_id': 'other', 'character': 'lulu'}
        self.pet.remote_plays.extend([monotonic()] * 3)
        event = {'interactionId': 'bonk', 'action_id': 'new', 'relation_id': 'relation',
                 'capabilityRevision': self.pet.runtime.revision, 'expires_at': time.time() + 4}
        with patch.object(self.pet.buddy, 'acknowledge') as ack:
            self.pet.receive_remote(event)
            ack.assert_called_with(event, False)
        self.assertFalse(self.pet.runtime.queue)

    def test_delete_last_package_and_import_different_action_set(self):
        from pack_fixtures import create_pack
        from tempfile import TemporaryDirectory
        previous = self.pet.runtime
        previous_player = self.pet.player
        self.pet.select_package('lumei')
        self.assertTrue(previous.stopped)
        self.assertEqual(previous_player.cache.bytes, 0)
        self.pet.select_package(None)
        for entry in list(self.pet.library.entries()):
            self.pet.library.remove(entry['id'])
        self.assertEqual(self.pet.config.character, '')
        self.assertFalse(self.pet.grab().isNull())
        with TemporaryDirectory() as folder:
            archive, _ = create_pack(folder, 'third.party', initial='charging', action='transform', interaction='light_show')
            package = self.pet.library.install(archive)
            self.pet.select_package(package.id)
            self.assertEqual(self.pet.runtime.state, 'charging')
            self.pet.invoke_package_menu('menu')
            self.assertEqual(self.pet.runtime.current, 'transform')
            caps = self.pet.character_capabilities()
            self.assertEqual(caps['interactions'][0]['id'], 'light_show')
            self.assertNotIn('invoke', caps['interactions'][0])

    def test_stale_capabilities_and_local_role_pause(self):
        self.pet.buddy.ready = True
        self.pet.buddy.relation = {'id': 'r', 'partner_id': 'b'}
        old = self.pet.runtime.revision
        self.pet.select_package('lumei')
        event = {'interactionId': 'pinch', 'action_id': 'old', 'relation_id': 'r',
                 'capabilityRevision': old, 'expires_at': time.time()+4}
        with patch.object(self.pet.buddy, 'acknowledge') as ack:
            self.pet.receive_remote(event)
            ack.assert_called_with(event, False)
        self.assertIsNone(self.pet.runtime.current)

    def test_pointer_look_uses_scaled_canvas_and_yields_to_drag(self):
        self.pet.select_package('codex-lumei')
        self.pet.show()
        for scale in (100, 200):
            self.pet.config.scale = scale
            self.pet.runtime.pointer_look.reset()
            point = self.pet.player.transform.map(QPointF(296, 72)) * (scale/100)
            global_point = self.pet.mapToGlobal(point.toPoint())
            with patch('offwork_pet.app.QCursor.pos', return_value=global_point):
                self.pet.animate()
            self.assertEqual(self.pet.runtime.animation_id(), 'facing_04')
            self.assertIsNone(self.pet.runtime.current)
        self.pet.dragging = True
        with patch('offwork_pet.app.QCursor.pos', return_value=global_point):
            self.pet.animate()
        self.assertEqual(self.pet.runtime.animation_id(), 'rest')
        self.pet.select_package('monthly-salary-cat')
        self.assertIsNone(self.pet.runtime.pointer_look.animation)

    def test_pointer_look_resumes_after_click_action(self):
        for package_id in ('noah-lulu', 'codex-lumei'):
            with self.subTest(package=package_id):
                self.pet.select_package(package_id)
                runtime = self.pet.runtime
                now = 100.0
                runtime.clock = runtime.pointer_look.clock = lambda: now
                runtime.last_tick = runtime.pointer_look.last_tick = now
                runtime.pointer_look.reset()
                point = self.pet.player.transform.map(QPointF(96, 72)).toPoint()
                global_point = self.pet.mapToGlobal(point)
                cursor = self.pet.mapToGlobal(self.pet.player.transform.map(QPointF(296, 72)).toPoint())
                self.assertTrue(self.pet.cat_contains(point))
                with patch('offwork_pet.app.QCursor.pos', return_value=cursor):
                    self.pet.animate()
                    self.assertEqual(runtime.animation_id(), 'facing_04')
                    self.mouse(QEvent.Type.MouseButtonPress, point, global_point, Qt.MouseButton.LeftButton)
                    self.pet.animate()
                    self.assertIsNone(runtime.pointer_look.animation)
                    self.mouse(QEvent.Type.MouseButtonRelease, point, global_point, Qt.MouseButton.NoButton)
                    self.assertTrue(self.pet.click_timer.isActive())
                    self.pet.click_timer.stop()
                    self.pet.click_timer.timeout.emit()
                    self.assertEqual(runtime.current, 'wave')
                    now += runtime.duration('wave') + .1
                    self.pet.animate()
                    self.assertEqual(runtime.animation_id(), 'rest')
                    now += .4
                    self.pet.animate()
                    self.assertIsNone(runtime.current)
                    self.assertEqual(runtime.animation_id(), 'facing_04')

    def test_pointer_look_press_cleared_when_gesture_cancelled(self):
        for cancel in ('focus', 'hide', 'switch'):
            with self.subTest(cancel=cancel):
                self.pet.show()
                point = QPoint(140, 180)
                self.mouse(QEvent.Type.MouseButtonPress, point, self.pet.mapToGlobal(point), Qt.MouseButton.LeftButton)
                self.assertIsNotNone(self.pet.press_position)
                if cancel == 'focus':
                    QApplication.sendEvent(self.pet, QFocusEvent(QEvent.Type.FocusOut))
                elif cancel == 'hide':
                    self.pet.hide()
                else:
                    self.pet.select_package('noah-lulu')
                self.assertIsNone(self.pet.press_position)
                self.assertIsNone(self.pet.drag)
                self.assertFalse(self.pet.dragging)

    def test_pointer_look_resumes_after_drag(self):
        self.pet.select_package('noah-lulu')
        point = self.pet.player.transform.map(QPointF(96, 72)).toPoint()
        global_point = self.pet.mapToGlobal(point)
        self.mouse(QEvent.Type.MouseButtonPress, point, global_point, Qt.MouseButton.LeftButton)
        delta = QPoint(40, 20)
        self.mouse(QEvent.Type.MouseMove, point + delta, global_point + delta, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pet.dragging)
        self.mouse(QEvent.Type.MouseButtonRelease, point, global_point + delta, Qt.MouseButton.NoButton)
        self.assertIsNone(self.pet.press_position)
        self.assertFalse(self.pet.click_timer.isActive())
        runtime = self.pet.runtime
        runtime.clock = runtime.pointer_look.clock = lambda: 100.0
        runtime.last_tick = runtime.pointer_look.last_tick = 100.0
        runtime.pointer_look.reset()
        cursor = self.pet.mapToGlobal(self.pet.player.transform.map(QPointF(296, 72)).toPoint())
        with patch('offwork_pet.app.QCursor.pos', return_value=cursor):
            self.pet.animate()
        self.assertEqual(runtime.animation_id(), 'facing_04')

    def test_pointer_look_setting_save_cancel_and_resume(self):
        self.pet.select_package('noah-lulu')
        runtime = self.pet.runtime
        now = 100.0
        runtime.clock = runtime.pointer_look.clock = lambda: now
        runtime.last_tick = runtime.pointer_look.last_tick = now
        runtime.pointer_look.reset()
        cursor = self.pet.mapToGlobal(self.pet.player.transform.map(QPointF(296, 72)).toPoint())
        with patch('offwork_pet.app.QCursor.pos', return_value=cursor):
            self.pet.animate()
            self.assertEqual(runtime.animation_id(), 'facing_04')
            def change_setting(enabled, accept):
                def finish_dialog():
                    self.pet.dialog.checks['pointer_look_enabled'].setChecked(enabled)
                    if accept:
                        self.pet.dialog.accept()
                    else:
                        self.pet.dialog.reject()
                QTimer.singleShot(0, finish_dialog)
                self.pet.open_settings()
            change_setting(False, True)
            self.assertFalse(self.pet.config.pointer_look_enabled)
            self.pet.animate()
            self.assertIsNone(runtime.pointer_look.animation)
            # Disabling gaze leaves the package's click reaction available.
            self.pet.pet_cat()
            self.assertEqual(runtime.current, 'wave')
            now += runtime.duration('wave') + .1
            self.pet.animate()
            now += .4
            self.pet.animate()
            self.assertEqual(runtime.animation_id(), 'rest')
            change_setting(True, False)
            self.assertFalse(self.pet.config.pointer_look_enabled)
            change_setting(True, True)
            self.assertTrue(self.pet.config.pointer_look_enabled)
            now += .4
            self.pet.animate()
            self.assertEqual(runtime.animation_id(), 'facing_04')

    def test_failed_active_update_restores_index_and_previous_runtime(self):
        from pack_fixtures import create_pack
        from tempfile import TemporaryDirectory
        from offwork_pet.packages import PackageError
        original = self.pet.runtime
        digest = original.package.digest
        with TemporaryDirectory() as folder:
            archive, _ = create_pack(folder, 'lulu', version='2.0.0')
            with patch('offwork_pet.app.PackagePlayer', side_effect=PackageError('cannot prepare new runtime')):
                with self.assertRaises(PackageError):
                    self.pet.import_package(archive)
        self.assertIs(self.pet.runtime, original)
        self.assertFalse(original.stopped)
        self.assertEqual(self.pet.library.load('lulu').digest, digest)

    def test_action_shelf_stays_open_and_tracks_package_and_position(self):
        self.pet.open_actions()
        shelf = self.pet.action_panel
        self.app.processEvents()
        self.assertTrue(shelf.isVisible())
        self.assertTrue(shelf.local_buttons)
        shelf.local_buttons[0].click()
        self.assertTrue(shelf.isVisible())
        self.assertIsNotNone(self.pet.runtime.current)
        self.assertFalse(shelf.local_buttons[0].isEnabled())
        self.assertEqual(shelf.local_buttons[0].property('playState'), 'playing')
        self.pet.select_package('noah-lulu')
        self.assertEqual(shelf.local_key, self.pet.runtime.revision)
        self.assertEqual(len(shelf.local_buttons), len(self.pet.runtime.data['menu']))
        self.pet.move(100, 100)
        self.app.processEvents()
        self.assertTrue(shelf.screen().availableGeometry().contains(shelf.frameGeometry()))
        shelf.select_tab(1)
        self.assertFalse(shelf.remote_buttons)
        self.assertEqual(shelf.pages.currentIndex(), 1)
        shelf.close_button.click()
        self.assertFalse(shelf.isVisible())

    def test_remote_shelf_stays_open_and_updates_availability(self):
        from types import SimpleNamespace
        client = self.pet.buddy
        client.identity = SimpleNamespace(record={})
        client.ready = client.enabled = True
        client.status = 'available'
        client.relation = {'id': 'r', 'capabilities': {'revision': 'one', 'interactions': [
            {'id': 'wave', 'label': '挥挥手'}, {'id': 'jump', 'label': '跳一下'}]}}
        self.pet.open_actions(1)
        shelf = self.pet.action_panel
        with patch.object(client, 'queue_action') as send:
            shelf.remote_buttons[0].click()
            shelf.remote_buttons[1].click()
            self.assertEqual(send.call_count, 2)
        self.assertTrue(shelf.isVisible())
        client.ready = False
        client.changed.emit()
        self.assertTrue(all(not button.isEnabled() for button in shelf.remote_buttons))
        client.relation = None
        client.changed.emit()
        self.assertFalse(shelf.remote_buttons)
        self.pet.hide()
        self.assertFalse(shelf.isVisible())

    def test_update_reminder_is_once_per_version_and_never_installs_silently(self):
        controller = self.pet.updates
        document = {'release': {'version': '99.0.0', 'notes': 'a new release'}}
        with patch.object(controller, 'show') as show, patch.object(controller, 'start_helper') as install:
            controller.completed(('checked', document))
            controller.completed(('checked', document))
            self.assertEqual(show.call_count, 1)
            self.assertTrue(controller.available)
            install.assert_not_called()
            controller.completed(('checked', document), manual=True)
            self.assertEqual(show.call_count, 2)

    def test_update_dialog_and_settings_keep_automatic_check_preference(self):
        controller = self.pet.updates
        controller.show()
        controller.dialog.automatic.setChecked(False)
        self.assertFalse(self.pet.config.auto_update_check)
        settings = SettingsDialog(self.pet.config, self.pet)
        self.assertFalse(settings.result_config(self.pet.config).auto_update_check)
        settings.deleteLater()
        controller.dialog.hide()

    def test_manual_update_check_finishes_and_button_recovers(self):
        document = {'release': {'version': '0.1.0', 'notes': 'old release'}}
        with patch('offwork_pet.update_ui.fetch_manifest', return_value=document):
            self.pet.updates.check(manual=True)
            deadline = monotonic() + 3
            while self.pet.updates.task is not None and monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertIsNone(self.pet.updates.task)
            self.assertFalse(self.pet.updates.available)
            self.assertTrue(self.pet.updates.dialog.check.isEnabled())
            self.pet.updates.dialog.hide()

    def test_automatic_checks_repeat_and_respect_opt_out(self):
        from offwork_pet.update_ui import CHECK_INTERVAL_MS, RETRY_INTERVAL_MS
        controller = self.pet.updates
        with patch.object(self.pet, 'persist', True), patch.object(self.pet, 'write_config'):
            controller.configure_automatic()
            self.assertTrue(controller.check_timer.isActive())
            controller.completed(('checked', {'release': {'version': '0.1.0'}}))
            self.assertEqual(controller.check_timer.interval(), CHECK_INTERVAL_MS)
            controller.failed('network unavailable')
            self.assertEqual(controller.check_timer.interval(), RETRY_INTERVAL_MS)
            controller.set_automatic(False)
            self.assertFalse(controller.check_timer.isActive())
            with patch.object(controller, 'check') as check:
                controller.automatic_check()
                check.assert_not_called()

    def test_cancel_download_requests_interruption_without_quitting_pet(self):
        from unittest.mock import Mock
        controller = self.pet.updates
        task = Mock()
        task.isRunning.return_value = True
        controller.task = task
        controller.downloading = True
        with patch.object(self.pet, 'quit') as quit:
            controller.cancel_download()
            task.requestInterruption.assert_called_once()
            quit.assert_not_called()
        controller.task = None
        controller.downloading = False


if __name__ == "__main__":
    unittest.main()
