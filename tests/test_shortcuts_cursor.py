"""Caret visibility and unified shortcut editing, conflict checks and rollback."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QKeySequenceEdit, QVBoxLayout

from offwork_pet.alert_ui import AlertButtonsDialog
from offwork_pet.core import Config, alert_buttons, load_config, save_config
from offwork_pet.input_cursor import StealthLineEdit
from offwork_pet.shortcut_ui import ShortcutDialog


class ShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = QDialog()
        self.pet.config = Config()
        self.pet.rally_dialog = None
        self.pet.alerts = SimpleNamespace(hotkeys=None, update_config=Mock())
        self.pet.write_config = Mock(return_value=True)
        self.dialog = ShortcutDialog(self.pet)

    def tearDown(self):
        self.dialog.reject()
        self.pet.deleteLater()
        self.app.processEvents()

    def test_all_shortcuts_include_custom_alerts_and_save_together(self):
        self.assertEqual(set(self.dialog.fields), {'__chat', '__mute', 'safe', 'warning', 'danger'})
        self.dialog.fields['__chat'].setKeySequence(QKeySequence('Ctrl+Shift+T'))
        self.dialog.fields['danger'].setKeySequence(QKeySequence('Ctrl+Shift+R'))
        self.dialog.save()
        self.assertEqual(self.pet.config.chat_hotkey, 'Ctrl+Shift+T')
        self.assertEqual(alert_buttons(self.pet.config)[2]['hotkey'], 'Ctrl+Shift+R')
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            save_config(self.pet.config, path)
            config, warning = load_config(path)
            self.assertFalse(warning)
            self.assertEqual(config.chat_hotkey, 'Ctrl+Shift+T')
            self.assertEqual(config.alert_hotkeys['danger'], 'Ctrl+Shift+R')
        self.pet.config.alert_buttons.append({'id': 'meeting', 'name': '开会', 'color': '#7964CB', 'level': 'warning', 'hotkey': ''})
        extra = ShortcutDialog(self.pet)
        self.assertIn('meeting', extra.fields)
        extra.deleteLater()

    def test_conflicts_and_failed_save_leave_current_bindings_unchanged(self):
        self.dialog.fields['__chat'].setKeySequence(QKeySequence('Ctrl+Alt+R'))
        self.dialog.save()
        self.assertIn('重复', self.dialog.feedback.text())
        self.pet.write_config.assert_not_called()
        self.assertEqual(self.pet.config.chat_hotkey, 'Ctrl+Alt+T')
        self.dialog.fields['__chat'].setKeySequence(QKeySequence('Ctrl+Shift+T'))
        self.pet.write_config.return_value = False
        self.dialog.save()
        self.assertEqual(self.pet.config.chat_hotkey, 'Ctrl+Alt+T')
        self.assertFalse(self.pet.config.alert_buttons)
        self.assertIn('保存失败', self.dialog.feedback.text())

    def test_os_occupied_key_does_not_change_config(self):
        hotkeys = self.pet.alerts.hotkeys = Mock()
        hotkeys.register.return_value = [('__chat', 'Ctrl+Alt+T')]
        self.dialog.save()
        self.assertIn('占用', self.dialog.feedback.text())
        hotkeys.unregister.assert_called_once()
        self.pet.write_config.assert_not_called()

    def test_alert_editor_has_no_second_shortcut_editor(self):
        editor = AlertButtonsDialog(alert_buttons(self.pet.config))
        self.assertEqual(editor.findChildren(QKeySequenceEdit), [])
        editor.rows[0]['hotkey'] = 'Ctrl+Shift+G'
        editor.reset_rows()
        editor.save()
        self.assertEqual(editor.result_buttons[0]['hotkey'], 'Ctrl+Shift+G')
        editor.deleteLater()

    def test_bright_caret_survives_faint_text_and_blinks_and_moves(self):
        root = QVBoxLayout(self.pet)
        edit = StealthLineEdit()
        edit.setStyleSheet('background: transparent; border: none; color: rgba(120,128,140,25);')
        edit.setFixedSize(320, 40)
        root.addWidget(edit)
        self.pet.show()
        self.app.setActiveWindow(self.pet)
        edit.setFocus()
        self.app.processEvents()
        edit.reset_caret()
        image = edit.grab().toImage()
        rect = edit.cursorRect()
        pixels = [image.pixelColor(x, y) for y in range(max(0,rect.top()), min(image.height(),rect.bottom()+1))
                  for x in range(max(0,rect.left()), min(image.width(),rect.right()+1))]
        self.assertTrue(any(pixel.alpha() > 200 and pixel.lightness() < 100 for pixel in pixels))
        self.assertTrue(edit.caret_on)
        edit.blink_caret()
        self.assertFalse(edit.caret_on)
        edit.setText('abc 中文')
        edit.setCursorPosition(0)
        first = edit.cursorRect().left()
        edit.setCursorPosition(3)
        self.assertGreater(edit.cursorRect().left(), first)
        self.assertTrue(edit.caret_on)
        edit.hide()
        self.assertFalse(edit.caret_timer.isActive())
