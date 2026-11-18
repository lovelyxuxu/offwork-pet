"""Wheel scrolling must never edit values without an explicit focused click."""
import unittest

from PySide6.QtCore import QPoint, QPointF, Qt, QTime
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDoubleSpinBox,
    QLineEdit, QSlider, QSpinBox, QTimeEdit, QVBoxLayout, QWidget)

from offwork_pet.ui_theme import scroll, style_window


class InputWheelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QDialog()
        style_window(self.window)
        root = QVBoxLayout(self.window)
        body = QWidget()
        form = QVBoxLayout(body)
        self.spin = QSpinBox()
        self.spin.setValue(10)
        self.double = QDoubleSpinBox()
        self.double.setValue(10)
        self.combo = QComboBox()
        self.combo.addItems(['A', 'B', 'C'])
        self.combo.setCurrentIndex(1)
        self.clock = QTimeEdit(QTime(12, 30))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setValue(50)
        self.other = QLineEdit()
        self.controls = [self.spin, self.double, self.combo, self.clock, self.slider]
        for control in self.controls + [self.other]:
            form.addWidget(control)
        body.setMinimumHeight(1000)
        self.area = scroll(body)
        root.addWidget(self.area)
        self.window.resize(360, 320)
        self.window.show()
        self.app.setActiveWindow(self.window)
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def wheel(self, widget, delta=-120):
        point = widget.rect().center()
        event = QWheelEvent(QPointF(point), QPointF(widget.mapToGlobal(point)), QPoint(), QPoint(0, delta),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(widget, event)
        self.app.processEvents()

    def value(self, widget):
        if isinstance(widget, QComboBox):
            return widget.currentIndex()
        if isinstance(widget, QTimeEdit):
            return widget.time()
        return widget.value()

    def test_hover_and_automatic_focus_scroll_page_without_editing(self):
        for widget in self.controls:
            self.area.verticalScrollBar().setValue(0)
            widget.setFocus()  # Merely focusing from code/window startup is insufficient.
            before = self.value(widget)
            self.wheel(widget)
            self.assertEqual(self.value(widget), before, type(widget).__name__)
            self.assertGreater(self.area.verticalScrollBar().value(), 0, type(widget).__name__)

    def test_click_selected_controls_can_wheel_edit_then_blur_revokes_it(self):
        for widget in self.controls:
            self.app.setActiveWindow(self.window)
            self.area.ensureWidgetVisible(widget)
            self.app.processEvents()
            QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())
            if isinstance(widget, QComboBox):
                widget.hidePopup()
                widget.setFocus()
            if isinstance(widget, QTimeEdit):
                widget.setSelectedSection(QTimeEdit.Section.MinuteSection)
            before = self.value(widget)
            self.wheel(widget)
            self.assertNotEqual(self.value(widget), before, type(widget).__name__)
            QTest.mouseClick(self.other, Qt.MouseButton.LeftButton)
            before = self.value(widget)
            self.wheel(widget)
            self.assertEqual(self.value(widget), before, type(widget).__name__)

    def test_spin_editor_child_is_also_protected(self):
        editor = self.spin.lineEdit()
        self.wheel(editor)
        self.assertEqual(self.spin.value(), 10)
        QTest.mouseClick(editor, Qt.MouseButton.LeftButton)
        self.wheel(self.spin)
        self.assertEqual(self.spin.value(), 9)
