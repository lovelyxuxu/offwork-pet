"""One place to configure all global keyboard shortcuts, including custom alerts."""
from dataclasses import replace

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QKeySequenceEdit, QLineEdit,
                              QVBoxLayout, QWidget)

from .core import alert_buttons, notification_hotkeys, validate
from .ui_theme import label, scroll, style_window


SHORTCUT_FIELDS = ('chat_hotkey', 'mute_hotkey', 'alert_buttons', 'alert_hotkeys')


def shortcut_values(config):
    return {key: getattr(config, key) for key in SHORTCUT_FIELDS}


def preview_shortcut_hint(config):
    chat = config.chat_hotkey or '未绑定（可用按钮输入）'
    mute = config.mute_hotkey or '未绑定（可用托盘菜单关闭）'
    return (f'{chat}：A 输入消息\n{mute}：只关闭 A 的通知，重复按仍关闭\n'
            '恢复通知：右键各自的托盘图标 → 开启消息与集会通知\n'
            'B 可通过下面按钮发言；文字和警报不挡鼠标。')


class ShortcutDialog(QDialog):
    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.setWindowTitle('统一快捷键设置')
        self.resize(500, 580)
        style_window(self)
        root = QVBoxLayout(self)
        root.addWidget(label('所有全局快捷键，在这里配置。', 'sectionTitle'))
        root.addWidget(label('支持修饰键 + 字母、数字或 F1–F12。清空表示不绑定；保存前统一检查重复和占用。', 'muted'))
        body = QWidget()
        form = QFormLayout(body)
        self.fields = {}
        self.buttons = alert_buttons(pet.config)
        rows = [('__chat', '输入即时消息', pet.config.chat_hotkey),
                ('__mute', '关闭通知（只关闭）', pet.config.mute_hotkey)]
        rows += [(item['id'], '集会警报 · ' + item['name'], item.get('hotkey', '')) for item in self.buttons]
        for key, title, value in rows:
            widget = QKeySequenceEdit(QKeySequence(value))
            widget.setMaximumSequenceLength(1)
            widget.setClearButtonEnabled(True)
            editor = widget.findChild(QLineEdit)
            if editor:
                editor.setPlaceholderText('点击后按下组合键')
            form.addRow(title, widget)
            self.fields[key] = widget
        root.addWidget(scroll(body), 1)
        root.addWidget(label('固定输入操作：Enter 发送，Esc 取消。\n鼠标操作：Ctrl + 滚轮缩放，Ctrl + Shift + 滚轮调透明度，Alt + 拖动抚摸。\n恢复通知仍需在托盘菜单手动开启。', 'muted'))
        self.feedback = label('', 'feedback')
        self.feedback.hide()
        root.addWidget(self.feedback)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Save).setText('保存全部快捷键')
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        actions.accepted.connect(self.save)
        actions.rejected.connect(self.reject)
        root.addWidget(actions)

    def save(self):
        values = {key: widget.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                  for key, widget in self.fields.items()}
        buttons = [dict(item, hotkey=values[item['id']]) for item in self.buttons]
        candidate = replace(self.pet.config, chat_hotkey=values['__chat'], mute_hotkey=values['__mute'],
                            alert_buttons=buttons, alert_hotkeys={item['id']: item['hotkey'] for item in buttons
                                if item['id'] in ('safe', 'warning', 'danger') and item['hotkey']})
        try:
            validate(candidate)
            mapping = notification_hotkeys(candidate)
        except ValueError as error:
            self.show_error(str(error))
            return
        hotkeys = self.pet.alerts.hotkeys
        if hotkeys:
            try:
                failed = hotkeys.register(mapping)
            finally:
                hotkeys.unregister()
            if failed:
                self.show_error('快捷键被其他程序占用或注册失败：' + '、'.join(text for _, text in failed))
                return
        previous = shortcut_values(self.pet.config)
        for key, value in shortcut_values(candidate).items():
            setattr(self.pet.config, key, value)
        if self.pet.write_config() is False:
            for key, value in previous.items():
                setattr(self.pet.config, key, value)
            self.show_error('保存失败，快捷键未修改，请重试。')
            return
        self.pet.alerts.update_config(self.pet.config)
        if self.pet.rally_dialog:
            self.pet.rally_dialog.refresh()
        self.accept()

    def show_error(self, text):
        self.feedback.setText(text)
        self.feedback.show()


def edit_shortcuts(pet):
    dialog = ShortcutDialog(pet)
    if pet.alerts.hotkeys:
        pet.alerts.hotkeys.unregister()
    try:
        dialog.exec()
    finally:
        pet.alerts.register_hotkeys()
        dialog.deleteLater()
