"""Independent placement, editable drafts, drag positioning and persistence."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from offwork_pet.core import Config, load_config, save_config, validate
from offwork_pet.layout_ui import LayoutEditor, element_rect, fit_messages
from offwork_pet.overlay_layout import COMMON, ELEMENTS, Element, element_settings


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = QWidget()
        self.pet.config = Config()
        self.pet.write_config = Mock(return_value=True)
        self.pet.chat = SimpleNamespace(apply_layout=Mock())
        self.editor = LayoutEditor(self.pet)
        self.editor.show()
        self.app.processEvents()

    def tearDown(self):
        self.editor.reject()
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()

    def test_parameter_draft_cancel_and_successful_save(self):
        self.editor.controls['font_size'].setValue(14)
        self.editor.controls['opacity'].setValue(35)
        self.editor.controls['seconds'].setValue(8)
        self.editor.controls['fade_seconds'].setValue(4)
        self.editor.controls['max_messages'].setValue(7)
        self.editor.controls['width'].setValue(480)
        self.editor.controls['height'].setValue(180)
        self.assertEqual(self.editor.handles['chat_messages'].width(), 482)
        self.assertEqual(self.editor.handles['chat_messages'].height(), 205)
        self.editor.controls['x'].setValue(80)
        self.assertEqual(self.pet.config.desktop_elements, {})
        self.assertEqual(self.editor.draft['chat_messages']['x'], 80)
        self.editor.reject()
        self.assertEqual(self.pet.config.desktop_elements, {})
        self.assertTrue(all(not handle.isVisible() for handle in self.editor.handles.values()))
        # New editor opens from the saved state, not the rejected draft.
        self.editor.deleteLater()
        self.editor = LayoutEditor(self.pet)
        self.editor.show()
        self.editor.controls['font_size'].setValue(12)
        self.editor.controls['x'].setValue(60)
        self.editor.controls['fade_seconds'].setValue(4)
        self.editor.controls['max_messages'].setValue(7)
        self.editor.controls['height'].setValue(180)
        self.editor.save()
        self.assertEqual(self.pet.config.desktop_elements['chat_messages']['x'], 60)
        self.pet.chat.apply_layout.assert_called_once()
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            save_config(self.pet.config, path)
            loaded, warning = load_config(path)
            self.assertFalse(warning)
            self.assertEqual(loaded.desktop_elements, self.pet.config.desktop_elements)

    def test_height_and_count_discard_old_messages_without_a_backlog(self):
        settings = element_settings({}, 'chat_messages')
        settings.update(height=28, max_messages=3)
        entries = [('旧消息', 1), ('中间消息', 2), ('最新消息', 3)]
        self.assertEqual(fit_messages(entries, settings), [('最新消息', 3)])
        settings.update(height=240, max_messages=2)
        self.assertEqual(fit_messages(entries, settings), entries[-2:])

    def test_drag_updates_position_and_does_not_move_input(self):
        key = 'chat_messages'
        handle = self.editor.handles[key]
        before = handle.pos()
        input_before = self.editor.handles['chat_input'].pos()
        local = QPoint(12, 12)
        start = handle.mapToGlobal(local)
        for kind, global_point, button, buttons in (
            (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseMove, start - QPoint(50, 40), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, start - QPoint(50, 40), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
        ):
            event = QMouseEvent(kind, QPointF(local), QPointF(global_point), button, buttons, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(handle, event)
        self.assertEqual(handle.pos(), before - QPoint(50, 40))
        self.assertEqual(self.editor.handles['chat_input'].pos(), input_before)
        self.assertEqual(self.pet.config.desktop_elements, {})

    def test_screen_disappears_falls_back_and_bounds_are_visible(self):
        layout = {'chat_messages': {'screen': 'removed-display', 'x': 32000, 'y': 32000}}
        area = self.app.primaryScreen().availableGeometry()
        self.assertTrue(area.contains(element_rect(layout, 'chat_messages', 120)))

    def test_save_failure_keeps_draft_and_original_config(self):
        self.pet.write_config.return_value = False
        self.editor.controls['font_size'].setValue(16)
        self.editor.save()
        self.assertEqual(self.pet.config.desktop_elements, {})
        self.assertTrue(self.editor.isVisible())
        self.pet.chat.apply_layout.assert_not_called()

    def test_registry_addition_gets_preview_and_controls(self):
        ELEMENTS['future_notice'] = Element('新元素', ('新元素示例',), dict(COMMON), .3, .3)
        try:
            self.editor.deleteLater()
            self.editor = LayoutEditor(self.pet)
            self.editor.show()
            self.editor.select('future_notice')
            self.assertIn('future_notice', self.editor.handles)
            self.editor.controls['opacity'].setValue(40)
            self.assertEqual(self.editor.draft['future_notice']['opacity'], 40)
        finally:
            self.editor.reject()
            ELEMENTS.pop('future_notice')

    def test_invalid_parameters_rejected(self):
        for layout in ([], {'chat_messages': {'opacity': 0}}, {'chat_messages': {'width': '360'}},
                       {'chat_input': {'color': 'invalid'}}, {'chat_messages': {'seconds': float('nan')}},
                       {'chat_messages': {'height': 0}}, {'chat_messages': {'max_messages': 21}},
                       {'chat_messages': {'fade_seconds': -1}}):
            with self.assertRaises(ValueError):
                validate(Config(desktop_elements=layout))
