"""Alert flare envelope, hotkey parsing and rally-signal reception (offscreen safe)."""
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from offwork_pet.alert import AlertController, alert_intensity
from offwork_pet.buddy import BuddyClient
from offwork_pet.core import Config, alert_buttons, load_config, parse_hotkey, save_config, validate
from offwork_pet.alert_ui import AlertButtonsDialog


class HotkeyTests(unittest.TestCase):
    def test_custom_buttons_round_trip_and_legacy_hotkeys(self):
        legacy = Config(alert_hotkeys={'danger': 'Ctrl+Shift+R'})
        self.assertEqual(alert_buttons(legacy)[2]['hotkey'], 'Ctrl+Shift+R')
        custom = {'id': 'meeting', 'name': '开会啦', 'color': '#7964CB', 'hotkey': '', 'level': 'warning'}
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config.json'
            save_config(Config(alert_buttons=[custom]), path)
            config, warning = load_config(path)
            self.assertFalse(warning)
            self.assertEqual(alert_buttons(config), [custom])

    def test_custom_button_validation(self):
        base = {'id': 'meeting', 'name': '开会啦', 'color': '#7964CB', 'hotkey': '', 'level': 'warning'}
        for changes in ({'name': ''}, {'name': 'a\nb'}, {'color': '#1234'}, {'color': 'red; border: none'},
                        {'id': '../x'}, {'level': 'explode'}, {'hotkey': 'Enter'}):
            with self.assertRaises(ValueError, msg=changes):
                validate(Config(alert_buttons=[dict(base, **changes)]))
        with self.assertRaises(ValueError):
            validate(Config(alert_buttons=[base, dict(base)]))

    def test_parse_combinations(self):
        self.assertEqual(parse_hotkey('Ctrl+Alt+R'), (0x0002 | 0x0001, ord('R')))
        self.assertEqual(parse_hotkey('ctrl+shift+f12'), (0x0002 | 0x0004, 0x7B))
        self.assertEqual(parse_hotkey('Win+1'), (0x0008, ord('1')))

    def test_reject_invalid_combinations(self):
        for text in ('R', 'Ctrl++R', 'Ctrl+Ctrl+R', 'Ctrl+Enter', '', 'Ctrl+Alt+F13', 'Ctrl+Alt+', None):
            with self.assertRaises(ValueError, msg=text):
                parse_hotkey(text)

    def test_config_validation(self):
        validate(Config())
        for kwargs in ({'alert_seconds': 1}, {'alert_seconds': 16}, {'alert_seconds': '5'},
                       {'alert_hotkeys': {'safe': 'R'}},
                       {'alert_hotkeys': {'safe': 'Ctrl+Alt+R', 'danger': 'ctrl+alt+r'}},
                       {'alert_hotkeys': {'unknown': 'Ctrl+Alt+X'}},
                       {'rally_name': 'x' * 25}, {'rally_receive': 'yes'}):
            with self.assertRaises(ValueError, msg=kwargs):
                validate(Config(**kwargs))


class EnvelopeTests(unittest.TestCase):
    def test_fade_in_hold_and_release(self):
        self.assertEqual(alert_intensity(-0.1, 5, 'safe'), 0.0)
        self.assertEqual(alert_intensity(0, 5, 'safe'), 0.0)
        self.assertEqual(alert_intensity(5, 5, 'safe'), 0.0)
        self.assertAlmostEqual(alert_intensity(1, 5, 'safe'), 1.0)
        self.assertAlmostEqual(alert_intensity(4.0, 5, 'safe'), 1.0)
        self.assertLess(alert_intensity(4.95, 5, 'safe'), 0.1)

    def test_danger_breathes_within_bounds(self):
        values = [alert_intensity(1 + index / 20, 5, 'danger') for index in range(40)]
        self.assertGreater(max(values), 0.95)
        self.assertLess(min(values), 0.8)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in values))


class AlertGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.config = Config()
        self.buddy = BuddyClient(self.config, persist=False)
        self.buddy.service_features = {'rally/v1'}
        self.controller = AlertController(self.config, self.buddy, enable_hotkeys=False)
        self.overlay = self.controller.overlay

    def tearDown(self):
        self.controller.shutdown()
        self.buddy.stop()
        self.buddy.deadlines.stop()
        self.buddy.deleteLater()
        self.controller.deleteLater()
        self.app.processEvents()

    def message(self, **overrides):
        msg = {'type': 'rally_alert', 'rally_id': 'r1', 'alert_id': 'a1', 'level': 'warning',
               'from': '小美', 'expires_at': time.time() + 5}
        msg.update(overrides)
        return msg

    def test_overlay_trigger_paint_and_stop(self):
        self.overlay.trigger('danger', 5, '小明')
        self.app.processEvents()
        self.assertTrue(self.overlay.isVisible())
        self.assertEqual(self.overlay.level, 'danger')
        self.assertEqual(self.overlay.source, '小明')
        self.assertFalse(self.overlay.grab().toImage().isNull())
        self.overlay.stop()
        self.assertFalse(self.overlay.isVisible())

    def test_receive_shows_flare_with_source(self):
        self.buddy.rally = {'id': 'r1'}
        self.buddy.rally_event.emit(self.message())
        self.assertTrue(self.overlay.isVisible())
        self.assertEqual(self.overlay.level, 'warning')
        self.assertEqual(self.overlay.source, '小美')

    def test_custom_signal_uses_sender_text_and_color(self):
        self.buddy.rally = {'id': 'r1'}
        self.buddy.rally_event.emit(self.message(label='老板来了', color='#7864CB'))
        self.assertEqual(self.overlay.label, '老板来了')
        self.assertEqual(self.overlay.color, '#7864CB')
        self.assertTrue(self.overlay.isVisible())

    def test_custom_signal_invalid_metadata_is_not_displayed(self):
        self.buddy.rally = {'id': 'r1'}
        self.buddy.rally_event.emit(self.message(label='坏\n消息', color='#7964CB'))
        self.assertFalse(self.overlay.isVisible())

    def test_caption_is_small_and_inside_bottom_right_work_area(self):
        self.overlay.trigger('warning', 5, '长来源名字' * 5, label='很长的自定义警报名称' * 3)
        rect, font, text = self.overlay.caption_layout()
        area = self.app.primaryScreen().availableGeometry().translated(-self.overlay._origin)
        self.assertTrue(area.contains(rect.toAlignedRect()))
        self.assertGreater(rect.center().x(), area.center().x())
        self.assertGreater(rect.center().y(), area.center().y())
        self.assertLessEqual(rect.height(), 22)
        self.assertLessEqual(rect.width(), 320)
        self.assertEqual(font.pointSize(), 8)
        self.assertLess(len(text), len(self.overlay.label + ' · 来自 ' + self.overlay.source))

    def test_custom_buttons_trigger_and_hotkeys_refresh_after_in_place_edit(self):
        buttons = alert_buttons(self.config)
        buttons.append({'id': 'meeting', 'name': '开会啦', 'color': '#7964CB', 'level': 'warning', 'hotkey': 'Ctrl+Alt+M'})
        self.config.alert_buttons = buttons
        self.controller.hotkeys = Mock()
        self.controller.register_hotkeys()
        self.controller.hotkeys.reset_mock()
        self.config.alert_buttons[-1]['hotkey'] = 'Ctrl+Shift+M'
        self.controller.update_config(self.config)
        self.assertEqual(self.controller.hotkeys.register.call_args.args[0]['meeting'], 'Ctrl+Shift+M')
        self.controller.trigger('meeting')
        self.assertEqual((self.overlay.label, self.overlay.color), ('开会啦', '#7964CB'))
        self.buddy.rally = {'id': 'r1'}
        self.buddy.ready = True
        self.buddy.service_features.add('rally-signals/v1')
        with patch.object(self.buddy, 'rally_alert') as send:
            self.controller.trigger('meeting')
            send.assert_called_once_with('warning', label='开会啦', color='#7964CB')

    def test_button_editor_saves_draft_and_rejects_duplicate_hotkeys(self):
        editor = AlertButtonsDialog(alert_buttons(self.config))
        try:
            editor.rows[1]['hotkey'] = editor.rows[0]['hotkey']
            editor.save()
            self.assertIsNone(editor.result_buttons)
            self.assertIn('重复', editor.feedback.text())
            editor.remove_row(editor.rows[1])
            editor.rows[0]['name'].setText('放心摸鱼')
            editor.save()
            self.assertEqual(editor.result_buttons[0]['name'], '放心摸鱼')
            self.assertFalse(self.config.alert_buttons)
        finally:
            editor.deleteLater()

    def test_receive_stays_out_of_system_notifications(self):
        self.buddy.rally = {'id': 'r1'}
        parent = self.buddy
        parent.tray = Mock()
        self.controller.setParent(parent)
        with patch('offwork_pet.alert.QApplication.beep') as beep:
            self.buddy.rally_event.emit(self.message(level='danger'))
            self.assertTrue(self.overlay.isVisible())
            self.assertEqual(self.overlay.source, '小美')
            parent.tray.showMessage.assert_not_called()
            beep.assert_not_called()
            self.config.alert_sound = True
            self.buddy.rally_event.emit(self.message(level='danger', alert_id='sound-opt-in'))
            beep.assert_called_once_with()

    def test_receive_filters_invalid_signals(self):
        self.buddy.rally = {'id': 'r1'}
        for overrides in ({'rally_id': 'other'}, {'level': 'explode'}, {'alert_id': None},
                          {'expires_at': time.time() - 1}, {'expires_at': 'soon'}):
            self.buddy.rally_event.emit(self.message(**overrides))
            self.assertFalse(self.overlay.isVisible(), overrides)
        self.config.paused = True
        self.buddy.rally_event.emit(self.message())
        self.assertFalse(self.overlay.isVisible())
        self.config.paused = False
        self.config.rally_receive = False
        self.buddy.rally_event.emit(self.message())
        self.assertFalse(self.overlay.isVisible())
        self.config.rally_receive = True
        self.buddy.rally = None
        self.buddy.rally_event.emit(self.message())
        self.assertFalse(self.overlay.isVisible())

    def test_duplicate_alert_does_not_restart_flare(self):
        self.buddy.rally = {'id': 'r1'}
        self.buddy.rally_event.emit(self.message())
        started = self.overlay.started
        self.buddy.rally_event.emit(self.message())
        self.assertEqual(self.overlay.started, started)
        self.buddy.rally_event.emit(self.message(alert_id='a2', level='danger'))
        self.assertGreater(self.overlay.started, started)
        self.assertEqual(self.overlay.level, 'danger')

    def test_trigger_without_rally_stays_local(self):
        notices = []
        self.controller.notice.connect(notices.append)
        self.controller.trigger('safe')
        self.assertTrue(self.overlay.isVisible())
        self.assertEqual(self.overlay.level, 'safe')
        self.assertTrue(any('本机' in notice for notice in notices))

    def test_trigger_broadcasts_when_joined(self):
        self.buddy.rally = {'id': 'r1'}
        self.buddy.ready = True
        with patch.object(self.buddy, 'rally_alert') as send:
            self.controller.trigger('danger')
            send.assert_called_once_with('danger')
        self.buddy.service_features = set()
        with patch.object(self.buddy, 'rally_alert') as send:
            self.controller.trigger('danger')
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
