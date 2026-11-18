"""Ephemeral delivery, no history, click-through flags and one-way mute."""
import time
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFocusEvent, QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from offwork_pet.app import Pet
from offwork_pet.core import Config, load_config, save_config, validate
from offwork_pet import chat_history
from tests import test_rally
from offwork_pet.alert_ui import AlertButtonsDialog
from offwork_pet.shortcut_ui import ShortcutDialog, preview_shortcut_hint


class ChatProtocolTests(unittest.TestCase):
    setUp = test_rally.RallyProtocolTests.setUp
    tearDown = test_rally.RallyProtocolTests.tearDown
    socket = test_rally.RallyProtocolTests.socket
    receive = test_rally.RallyProtocolTests.receive
    command = test_rally.RallyProtocolTests.command
    profile = test_rally.RallyProtocolTests.profile
    def test_chat_only_current_online_group_and_no_body_retained(self):
        with self.socket(self.a) as a, self.socket(self.b) as b, self.socket(self.c) as c:
            for socket in (a, b, c):
                self.profile(socket)
            self.assertEqual(self.command(a, 'rally_chat', text='hello', message_id='first')['code'], 'unjoined')
            group = self.command(a, 'rally_create', name='聊天组', member_name='小A')['data']['rally']
            self.command(b, 'rally_join', code=group['code'])
            before = list(self.client.app.state.relay.store.db.iterdump())
            result = self.command(a, 'rally_chat', text='这句只即时转发', message_id='chat1')
            self.assertTrue(result['ok'])
            self.assertEqual(result['data']['forwarded'], 1)
            event = self.receive(b, 'rally_chat')
            self.assertEqual((event['text'], event['from']), ('这句只即时转发', '小A'))
            self.assertEqual(list(self.client.app.state.relay.store.db.iterdump()), before)
            self.assertNotIn('这句只即时转发', repr(vars(self.client.app.state.relay.rally)))
            self.assertEqual(self.command(a, 'rally_chat', text='重复', message_id='chat1')['code'], 'duplicate')
            for text in ('', 'x' * 201, 'bad\nline'):
                self.assertEqual(self.command(a, 'rally_chat', text=text, message_id='invalid')['code'], 'format')
        with self.socket(self.a) as a:
            self.profile(a)
            self.assertEqual(self.command(a, 'rally_chat', text='离线不保存', message_id='offline')['data']['forwarded'], 0)
        with self.socket(self.b) as b:
            self.profile(b)
            # No replay: the next response is the explicitly requested heartbeat.
            b.send_json({'type': 'heartbeat'})
            while True:
                item = b.receive_json()
                self.assertNotEqual(item['type'], 'rally_chat')
                if item['type'] == 'heartbeat':
                    break


class ChatGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = Pet(Config(auto_update_check=False), persist=False, enable_alert_hotkeys=False)
        self.pet.buddy.rally = {'id': 'r1', 'name': '测试集会',
                                'members': [{'name': '小A', 'self': False}, {'name': '我自己', 'self': True}]}
        self.pet.buddy.ready = True
        self.pet.buddy.service_features = {'rally/v1', 'rally-chat/v1'}
        self.chat = self.pet.chat
        self.history_dir = TemporaryDirectory()
        self.addCleanup(self.history_dir.cleanup)
        history = patch('offwork_pet.chat_history.data_dir', return_value=Path(self.history_dir.name))
        history.start()
        self.addCleanup(history.stop)

    def tearDown(self):
        self.chat.shutdown()
        self.pet.alerts.shutdown()
        self.pet.visits.shutdown()
        self.pet.updates.shutdown()
        self.pet.buddy.stop()
        self.pet.buddy.deadlines.stop()
        self.pet.animation.stop()
        self.pet.tick_timer.stop()
        self.pet.save_timer.stop()
        self.pet.tray.hide()
        self.pet.hide()
        self.pet.deleteLater()
        self.app.processEvents()

    def message(self, identifier='msg', **changes):
        return dict(type='rally_chat', rally_id='r1', message_id=identifier, text='你好，在线搭子',
                    **{'from': '小A', 'expires_at': time.time() + 5}, **changes)

    def test_input_dismisses_when_switching_windows_but_ime_popup_is_preserved(self):
        self.chat.open()
        self.app.setActiveWindow(self.chat.composer)
        self.chat.composer.input.setFocus()
        self.chat.composer.input.setText('未发送内容')
        other = QDialog()
        try:
            other.show()
            self.app.setActiveWindow(other)
            QTest.qWait(150)
            self.assertFalse(self.chat.composer.isVisible())
            self.assertEqual(self.chat.composer.input.text(), '')
        finally:
            other.close()
        self.chat.open()
        self.app.setActiveWindow(self.chat.composer)
        edit = self.chat.composer.input
        edit.setFocus()
        edit.clearFocus()
        QApplication.sendEvent(edit, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.PopupFocusReason))
        QApplication.sendEvent(edit, QInputMethodEvent('中文候选', []))
        self.assertTrue(edit.composing)
        QTest.qWait(150)
        self.assertTrue(self.chat.composer.isVisible())
        QApplication.sendEvent(edit, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason))
        QTest.qWait(150)
        self.assertFalse(self.chat.composer.isVisible())
        self.assertEqual(edit.text(), '')
        self.assertFalse(edit.composing)

    def test_send_failure_has_ephemeral_click_through_feedback_without_focus(self):
        self.pet.rally_dialog = None
        self.app.processEvents()
        previous = self.app.activeWindow()
        self.chat.sent({'ok': False, 'message': '发送太快，请稍后再试'})
        notice = self.chat.failure_notice
        self.app.processEvents()
        self.assertTrue(notice.isVisible())
        self.assertIn('发送太快', notice.text)
        self.assertFalse(self.chat.composer.isVisible())
        self.assertEqual(self.app.activeWindow(), previous)
        self.assertTrue(notice.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        notice.timer.timeout.emit()
        self.assertEqual(notice.text, '')
        self.assertFalse(notice.isVisible())
        self.pet.config.notifications_muted = True
        self.chat.sent({'ok': False, 'message': '失败'})
        self.assertFalse(notice.isVisible())

    def test_alert_appearance_editor_does_not_unregister_any_global_keys(self):
        self.pet.buddy.ready = False
        self.pet.buddy.rally = None
        self.pet.open_rally()
        from unittest.mock import Mock
        keys = self.pet.alerts.hotkeys = Mock()
        keys.failed = []
        keys.register.return_value = []
        def during_edit(editor):
            keys.unregister.assert_not_called()
            return QDialog.DialogCode.Rejected
        try:
            with patch.object(AlertButtonsDialog, 'exec', during_edit):
                self.pet.rally_dialog.edit_buttons()
            keys.unregister.assert_not_called()
            keys.register.assert_not_called()
        finally:
            self.pet.alerts.hotkeys = None
            self.pet.rally_dialog.hide()

    def test_saved_and_cleared_shortcuts_refresh_the_preview_hint(self):
        hint = QLabel(preview_shortcut_hint(self.pet.config))
        self.pet.alerts.shortcuts_changed.connect(lambda: hint.setText(preview_shortcut_hint(self.pet.config)))
        editor = ShortcutDialog(self.pet)
        try:
            editor.fields['__chat'].setKeySequence('Ctrl+Shift+T')
            editor.fields['__mute'].clear()
            editor.save()
            self.assertIn('Ctrl+Shift+T：A', hint.text())
            self.assertIn('未绑定（可用托盘菜单关闭）', hint.text())
            self.assertNotIn('Ctrl+Alt+T：', hint.text())
        finally:
            editor.deleteLater()

    def test_three_messages_fade_then_clear_and_native_flags(self):
        for index in range(4):
            self.chat.receive(self.message(str(index)))
        overlay = self.chat.overlay
        self.assertEqual(len(overlay.entries), 3)
        flags = overlay.windowFlags()
        self.assertTrue(flags & Qt.WindowType.WindowTransparentForInput)
        self.assertTrue(flags & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.app.processEvents()
        pixels = overlay.grab().toImage()
        self.assertFalse(pixels.isNull())
        self.assertEqual(pixels.pixelColor(0, 0).alpha(), 0)
        alphas = [pixels.pixelColor(x, y).alpha() for y in range(pixels.height()) for x in range(pixels.width())]
        self.assertGreater(max(alphas), 0)
        self.assertLessEqual(max(alphas), round(255 * .65))
        self.assertLess(sum(alpha > 0 for alpha in alphas), len(alphas) * .35)
        with patch('offwork_pet.chat.time.monotonic', return_value=time.monotonic() + 6):
            overlay.advance()
        self.assertEqual(overlay.entries, [])
        self.assertFalse(overlay.isVisible())
        self.assertFalse(overlay.timer.isActive())

    def test_mute_repeated_hotkey_keeps_muted_and_manual_resume_no_replay(self):
        self.chat.receive(self.message())
        self.pet.alerts.overlay.trigger('danger', 5)
        notices = []
        with patch.object(self.pet.tray, 'showMessage', side_effect=lambda *args: notices.append(args)):
            for _ in range(4):
                self.pet.alerts.activate_hotkey('__mute')
        self.assertTrue(self.pet.config.notifications_muted)
        self.assertEqual(notices, [])
        self.assertFalse(self.pet.alerts.overlay.isVisible())
        self.assertEqual(self.chat.overlay.entries, [])
        self.assertIn('免打扰', self.pet.tray.toolTip())
        self.pet.tick()
        self.assertIn('免打扰', self.pet.tray.toolTip())
        self.chat.receive(self.message('muted'))
        self.pet.alerts.receive({'type': 'rally_alert', 'rally_id': 'r1', 'alert_id': 'x', 'level': 'danger',
                                 'expires_at': time.time() + 5})
        self.assertEqual(self.chat.overlay.entries, [])
        self.assertFalse(self.pet.alerts.overlay.isVisible())
        self.pet.notification_action.trigger()
        self.assertFalse(self.pet.config.notifications_muted)
        self.assertFalse(self.chat.overlay.isVisible())
        self.chat.receive(self.message('after-resume'))
        self.assertTrue(self.chat.overlay.isVisible())

    def test_input_clears_on_cancel_and_send_never_auto_retries(self):
        self.chat.open()
        composer = self.chat.composer
        self.assertTrue(composer.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(composer.windowFlags() & Qt.WindowType.NoDropShadowWindowHint)
        self.assertTrue(composer.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertFalse(composer.input.hasFrame())
        self.assertFalse(composer.input.placeholderText())
        self.assertFalse(composer.feedback.isVisible())
        self.assertEqual(composer.height(), 28)
        self.chat.composer.input.setText('取消后清空')
        self.chat.composer.reject()
        self.assertEqual(self.chat.composer.input.text(), '')
        with patch.object(self.pet.buddy, 'rally_chat') as send:
            self.chat.composer.input.setText('即时发出')
            self.chat.send()
            self.assertEqual(send.call_args.args[0], '即时发出')
            self.assertEqual(self.chat.composer.input.text(), '')
            self.assertFalse(self.chat.composer.isVisible())
            self.assertEqual(self.chat.overlay.entries, [])
        self.chat.sent({'ok': False, 'message': '连接失败'})
        self.assertFalse(self.chat.composer.isVisible())

    def test_stale_other_group_and_duplicates_ignored(self):
        message = self.message()
        for invalid in (dict(message, rally_id='other'), dict(message, expires_at=time.time() - 1)):
            self.chat.receive(invalid)
        self.assertFalse(self.chat.overlay.isVisible())
        self.chat.receive(message)
        self.chat.receive(message)
        self.assertEqual(len(self.chat.overlay.entries), 1)
        self.assertNotIn(message['text'], repr(self.chat.seen))

    def test_hover_holds_text_without_capturing_mouse_then_resumes_expiry(self):
        overlay = self.chat.overlay
        start = time.monotonic()
        with patch('offwork_pet.chat.time.monotonic', return_value=start):
            overlay.push('搭子', '鼠标放上来不消失', seconds=5)
            with patch('offwork_pet.chat.QCursor.pos', return_value=overlay.mapToGlobal(QPoint(12, 12))):
                overlay.advance()
                self.assertTrue(overlay.hovering)
                with patch('offwork_pet.chat.time.monotonic', return_value=start + 8):
                    overlay.advance()
                    self.assertTrue(overlay.isVisible())
                    self.assertAlmostEqual(overlay.entries[0][2] - (start + 8), 5)
            with patch('offwork_pet.chat.QCursor.pos', return_value=QPoint(-9999, -9999)):
                with patch('offwork_pet.chat.time.monotonic', return_value=start + 9):
                    overlay.advance()
                    self.assertFalse(overlay.hovering)
                with patch('offwork_pet.chat.time.monotonic', return_value=start + 15):
                    overlay.advance()
                    self.assertFalse(overlay.entries)
                    self.assertFalse(overlay.isVisible())
        self.assertTrue(overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput)

    def test_position_is_independent_and_preview_discards_real_messages(self):
        self.chat.receive(self.message('independent'))
        message_position = self.chat.overlay.pos()
        self.chat.open()
        input_position = self.chat.composer.pos()
        self.pet.move(self.pet.pos().x() - 90, self.pet.pos().y() - 80)
        self.chat.overlay.advance()
        self.chat.open()
        self.assertEqual(self.chat.overlay.pos(), message_position)
        self.assertEqual(self.chat.composer.pos(), input_position)
        self.chat.open_layout()
        self.assertEqual(self.chat.overlay.entries, [])
        self.chat.receive(self.message('ignored-preview'))
        self.assertEqual(self.chat.overlay.entries, [])
        self.chat.layout_editor.reject()
        self.assertEqual(self.chat.overlay.entries, [])
        self.pet.config.desktop_elements = {'chat_messages': {'seconds': 10, 'max_messages': 1, 'x': 25, 'y': 40}}
        self.chat.receive(self.message('configured'))
        self.assertGreater(self.chat.overlay.entries[0][2] - time.monotonic(), 9)
        self.assertEqual(self.chat.overlay.x(), self.app.primaryScreen().availableGeometry().x() + 25)
        self.pet.config.desktop_elements = {
            'chat_messages': {'height': 28, 'width': 300, 'max_messages': 3, 'seconds': 8, 'fade_seconds': 4},
            'chat_input': {'height': 60, 'width': 400},
        }
        self.chat.apply_layout()
        self.chat.receive(self.message('bounded-new'))
        self.assertEqual(len(self.chat.overlay.entries), 1)
        self.assertEqual(self.chat.overlay.entries[0][1], '你好，在线搭子')
        self.assertEqual((self.chat.overlay.width(), self.chat.overlay.height()), (300, 28))
        self.assertEqual((self.chat.composer.width(), self.chat.composer.height()), (400, 60))

    def test_config_persists_and_shortcuts_cannot_collide(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            save_config(Config(notifications_muted=True), path)
            config, warning = load_config(path)
            self.assertFalse(warning)
            self.assertTrue(config.notifications_muted)
            path.write_text(json.dumps({'alert_hotkeys': {'danger': 'Ctrl+Alt+D'}}), encoding='utf-8')
            legacy, warning = load_config(path)
            self.assertFalse(warning)
            self.assertEqual(legacy.mute_hotkey, '')
            self.assertEqual(legacy.alert_hotkeys['danger'], 'Ctrl+Alt+D')
        for values in ({'mute_hotkey': 'Ctrl+Alt+T'}, {'chat_hotkey': 'Ctrl+Alt+R'}, {'notifications_muted': 1}):
            with self.assertRaises(ValueError):
                validate(Config(**values))

    def test_history_logs_displayed_received_and_all_successful_sends(self):
        path = Path(self.history_dir.name) / 'chat-history.log'
        self.chat.receive(self.message('h1'))
        self.chat.receive(self.message('h1'))
        self.pet.config.notifications_muted = True
        self.chat.receive(self.message('h2'))
        self.pet.config.notifications_muted = False
        with patch.object(self.pet.buddy, 'rally_chat') as send:
            self.chat.composer.input.setText('自己发的')
            self.chat.send()
            send.call_args.args[1]({'ok': True, 'data': {'forwarded': 1}})
        lines = path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 小A → 测试集会：你好，在线搭子$')
        self.assertRegex(lines[1], r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 我 → 测试集会：自己发的$')

    def test_own_send_echoes_like_game_chat_and_obeys_mute_pause_and_rally_switch(self):
        path = Path(self.history_dir.name) / 'chat-history.log'
        def send_ok(text):
            with patch.object(self.pet.buddy, 'rally_chat') as send:
                self.chat.composer.input.setText(text)
                self.chat.send()
                send.call_args.args[1]({'ok': True, 'data': {'forwarded': 1}})
        send_ok('第一条')
        self.assertEqual(self.chat.overlay.entries[-1][:2], ('我自己', '第一条'))
        self.pet.config.notifications_muted = True
        send_ok('免打扰时发的')
        self.assertEqual(len(self.chat.overlay.entries), 1)
        self.assertIn('我 → 测试集会：免打扰时发的', path.read_text(encoding='utf-8'))
        self.pet.config.notifications_muted = False
        self.pet.config.paused = True
        send_ok('暂停时发的')
        self.assertEqual(len(self.chat.overlay.entries), 1)
        self.pet.config.paused = False
        with patch.object(self.pet.buddy, 'rally_chat') as send:
            self.chat.composer.input.setText('切换集会后确认')
            self.chat.send()
            self.pet.buddy.rally = {'id': 'r2', 'name': '别的集会', 'members': []}
            send.call_args.args[1]({'ok': True})
        self.assertEqual(len(self.chat.overlay.entries), 1)
        self.assertIn('我 → 测试集会：切换集会后确认', path.read_text(encoding='utf-8'))
        self.chat.open_layout()
        send_ok('预览模式不显示')
        self.assertEqual(self.chat.overlay.entries, [])
        self.chat.layout_editor.reject()
        self.chat.sent({'ok': False, 'message': '失败'}, ('失败文本', 'r2', '别的集会', '我自己'))
        self.assertEqual(self.chat.overlay.entries, [])
        self.assertNotIn('失败文本', path.read_text(encoding='utf-8'))

    def test_tray_menu_opens_chat_history_in_notepad(self):
        self.assertIn('聊天记录…', [action.text() for action in self.pet.menu.actions()])
        with patch('offwork_pet.app.subprocess.Popen') as popen:
            self.pet.open_chat_history()
        command = popen.call_args.args[0]
        self.assertEqual(command[0], 'notepad.exe')
        self.assertEqual(command[1], str(Path(self.history_dir.name) / 'chat-history.log'))
        self.assertTrue(Path(command[1]).exists())


class ChatHistoryTests(unittest.TestCase):
    def test_append_writes_one_readable_line(self):
        with TemporaryDirectory() as directory:
            with patch('offwork_pet.chat_history.data_dir', return_value=Path(directory)):
                chat_history.append('小A', '周末开黑', '今晚几点撤？')
                line = (Path(directory) / 'chat-history.log').read_text(encoding='utf-8')
        self.assertRegex(line, r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 小A → 周末开黑：今晚几点撤？\n$')

    def test_append_trims_oldest_half_over_two_mib(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'chat-history.log'
            path.write_text(('x' * 100 + '\n') * 22000, encoding='utf-8')
            with patch('offwork_pet.chat_history.data_dir', return_value=Path(directory)):
                chat_history.append('我', '周末开黑', '新消息')
            lines = path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 11001)
        self.assertTrue(lines[-1].endswith('我 → 周末开黑：新消息'))

    def test_append_swallows_oserror(self):
        with TemporaryDirectory() as directory:
            block = Path(directory) / 'block'
            block.write_text('占位', encoding='utf-8')
            with patch('offwork_pet.chat_history.history_path', return_value=block / 'chat-history.log'):
                chat_history.append('我', '周末开黑', '不应崩溃')
