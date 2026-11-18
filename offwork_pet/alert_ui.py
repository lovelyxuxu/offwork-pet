"""Customizable signals, ephemeral chat entry and rally membership."""
from dataclasses import replace
import uuid
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QGridLayout, QHBoxLayout, QLineEdit,
    QMessageBox, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from .core import Config, alert_buttons, validate_alert_buttons, notification_hotkeys
from .ui_theme import card, label, scroll, style_window


def signal_button(item):
    name, color, hotkey = item['name'], item['color'], item.get('hotkey', '')
    button = QPushButton(name)
    button.setToolTip(name)
    button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setMinimumHeight(58)
    darker = QColor(color).darker(112).name()
    lighter = QColor(color).lighter(110).name()
    ink = '#202533' if QColor(color).lightnessF() > 0.65 else 'white'
    button.setStyleSheet(f'''QPushButton {{ color: {ink}; background: {color}; border: none;
        border-radius: 12px; font-size: 16px; font-weight: 700; }}
        QPushButton:hover {{ background: {lighter}; }}
        QPushButton:pressed {{ background: {darker}; }}''')
    caption = label(hotkey or '未设置热键', 'muted')
    caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
    column = QVBoxLayout()
    column.setSpacing(5)
    column.addWidget(button)
    column.addWidget(caption)
    return button, caption, column


class AlertButtonsDialog(QDialog):
    def __init__(self, buttons, parent=None):
        super().__init__(parent)
        self.setWindowTitle('自定义警报按钮')
        self.resize(590, 640)
        style_window(self)
        root = QVBoxLayout(self)
        root.addWidget(label('设置你自己的信号', 'heading'))
        root.addWidget(label('这里只修改名称、颜色和提醒方式。快捷键请在“外观与设置 → 统一快捷键设置”中修改。', 'subtitle'))
        body = QWidget()
        self.items = QVBoxLayout(body)
        self.items.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.addWidget(scroll(body), 1)
        self.rows = []
        self.result_buttons = None
        for item in buttons:
            self.add_row(item)
        controls = QHBoxLayout()
        self.add_button = QPushButton('添加按钮')
        self.add_button.clicked.connect(lambda: self.add_row())
        reset = QPushButton('恢复默认')
        reset.clicked.connect(self.reset_rows)
        controls.addWidget(self.add_button)
        controls.addWidget(reset)
        root.addLayout(controls)
        self.feedback = label('', 'muted')
        root.addWidget(self.feedback)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        actions.accepted.connect(self.save)
        actions.rejected.connect(self.reject)
        root.addWidget(actions)

    def add_row(self, item=None):
        if len(self.rows) >= 32:
            self.feedback.setText('最多可设置 32 个按钮')
            return
        item = item or {'id': uuid.uuid4().hex, 'name': '新提醒', 'color': '#7964CB', 'level': 'warning', 'hotkey': ''}
        surface, layout = card()
        first = QHBoxLayout()
        name = QLineEdit(item['name'])
        name.setMaxLength(24)
        name.setPlaceholderText('按钮名称 / 提醒文字')
        color = QPushButton(item['color'])
        first.addWidget(name, 1)
        first.addWidget(color)
        layout.addLayout(first)
        second = QHBoxLayout()
        shortcut = label(item.get('hotkey') or '未绑定快捷键', 'muted')
        effect = QComboBox()
        for title, key in (('平稳', 'safe'), ('轻呼吸', 'warning'), ('危险呼吸', 'danger')):
            effect.addItem(title, key)
        effect.setCurrentIndex(effect.findData(item['level']))
        remove = QPushButton('删除')
        second.addWidget(shortcut, 1)
        second.addWidget(effect)
        second.addWidget(remove)
        layout.addLayout(second)
        row = {'id': item['id'], 'widget': surface, 'name': name, 'color': item['color'],
               'color_button': color, 'hotkey': item.get('hotkey', ''), 'effect': effect}
        self.paint_color(row)
        color.clicked.connect(lambda: self.pick_color(row))
        remove.clicked.connect(lambda: self.remove_row(row))
        self.rows.append(row)
        self.items.addWidget(surface)

    def paint_color(self, row):
        row['color_button'].setText(row['color'])
        ink = '#202533' if QColor(row['color']).lightnessF() > 0.65 else 'white'
        row['color_button'].setStyleSheet(f"background: {row['color']}; color: {ink}; padding:8px;")

    def pick_color(self, row):
        color = QColorDialog.getColor(QColor(row['color']), self, '选择警报颜色')
        if color.isValid():
            row['color'] = color.name().upper()
            self.paint_color(row)

    def remove_row(self, row):
        self.rows.remove(row)
        self.items.removeWidget(row['widget'])
        row['widget'].hide()
        row['widget'].deleteLater()

    def reset_rows(self):
        shortcuts = {row['id']: row['hotkey'] for row in self.rows}
        for row in list(self.rows):
            self.remove_row(row)
        for item in alert_buttons(Config()):
            item['hotkey'] = shortcuts.get(item['id'], '')
            self.add_row(item)

    def save(self):
        buttons = [{'id': row['id'], 'name': row['name'].text().strip(), 'color': row['color'],
                    'hotkey': row['hotkey'],
                    'level': row['effect'].currentData()} for row in self.rows]
        try:
            validate_alert_buttons(buttons)
            if self.parent() is not None and hasattr(self.parent(), 'pet'):
                notification_hotkeys(replace(self.parent().pet.config, alert_buttons=buttons))
        except ValueError as error:
            self.feedback.setText(str(error))
            return
        self.result_buttons = buttons
        self.accept()


class RallyDialog(QDialog):
    def __init__(self, pet):
        super().__init__()
        self.pet, self.client = pet, pet.buddy
        self.setWindowTitle('下班搭子 · 集会警报')
        self.setMinimumSize(500, 600)
        self.resize(530, 720)
        style_window(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(12)
        root.addWidget(label('把信号，送到整个集会。', 'heading'))
        root.addWidget(label('发出警报，或快捷说一句。只送达此刻在线的成员，不保存历史。', 'subtitle'))
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 4, 0)
        content.setSpacing(14)
        root.addWidget(scroll(body), 1)

        surface, layout = card('即时消息与免打扰')
        chat_button = QPushButton('输入即时消息…')
        chat_button.clicked.connect(lambda: self.pet.chat.open())
        layout.addWidget(chat_button)
        preview_button = QPushButton('桌面布局 / 预览模式…')
        preview_button.clicked.connect(lambda: self.pet.chat.open_layout())
        layout.addWidget(preview_button)
        self.notification_status = label('', 'muted')
        layout.addWidget(self.notification_status)
        shortcut_button = QPushButton('统一快捷键设置…')
        from .shortcut_ui import edit_shortcuts
        shortcut_button.clicked.connect(lambda: edit_shortcuts(self.pet))
        layout.addWidget(shortcut_button)
        content.addWidget(surface)

        surface, layout = card('发出警报', '本机立即泛光；已加入集会时同步广播给在线成员。')
        self.buttons_grid = QGridLayout()
        self.buttons_grid.setSpacing(12)
        self.signal_buttons = {}
        self.hotkey_captions = {}
        self._buttons_snapshot = None
        layout.addLayout(self.buttons_grid)
        self.customize_button = QPushButton('自定义按钮…')
        self.customize_button.clicked.connect(self.edit_buttons)
        layout.addWidget(self.customize_button)
        self.hotkey_status = label('', 'muted')
        layout.addWidget(self.hotkey_status)
        content.addWidget(surface)

        surface, layout = card('我的集会')
        self.rally_status = label('', 'sectionTitle')
        layout.addWidget(self.rally_status)
        self.members = QVBoxLayout()
        self.members.setSpacing(4)
        layout.addLayout(self.members)
        self.name = QLineEdit(pet.config.rally_name)
        self.name.setPlaceholderText('我在集会中的名字（默认使用角色名字）')
        self.name.setMaxLength(24)
        self.name.editingFinished.connect(self.save_name)
        layout.addWidget(self.name)
        self.join_widgets = QWidget()
        join_layout = QVBoxLayout(self.join_widgets)
        join_layout.setContentsMargins(0, 0, 0, 0)
        join_layout.setSpacing(10)
        create_row = QHBoxLayout()
        self.rally_title = QLineEdit()
        self.rally_title.setPlaceholderText('集会名称（如：下班互助组）')
        self.rally_title.setMaxLength(24)
        self.create = QPushButton('创建集会')
        self.create.setProperty('variant', 'primary')
        self.create.clicked.connect(self.create_rally)
        create_row.addWidget(self.rally_title, 1)
        create_row.addWidget(self.create)
        join_layout.addLayout(create_row)
        capacity = QHBoxLayout()
        capacity.addWidget(label('新集会人数上限'))
        self.create_limit = QSpinBox()
        self.create_limit.setRange(1, 1000)
        self.create_limit.setValue(20)
        self.create_limit.setSuffix(' 人')
        capacity.addWidget(self.create_limit)
        capacity.addStretch()
        self.capacity_create = QWidget()
        self.capacity_create.setLayout(capacity)
        join_layout.addWidget(self.capacity_create)
        join_row = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText('输入 8 位集会码')
        self.code.setMaxLength(8)
        self.join = QPushButton('加入集会')
        self.join.setProperty('variant', 'primary')
        self.join.clicked.connect(lambda: self.client.rally_join(self.code.text().strip().upper(), self.pet.config.rally_name))
        join_row.addWidget(self.code, 1)
        join_row.addWidget(self.join)
        join_layout.addLayout(join_row)
        layout.addWidget(self.join_widgets)
        self.member_widgets = QWidget()
        member_layout = QVBoxLayout(self.member_widgets)
        member_layout.setContentsMargins(0, 0, 0, 0)
        member_layout.setSpacing(10)
        code_row = QHBoxLayout()
        self.code_label = label('', 'sectionTitle')
        code_row.addWidget(self.code_label, 1)
        self.copy = QPushButton('复制集会码')
        self.copy.clicked.connect(self.copy_code)
        code_row.addWidget(self.copy)
        member_layout.addLayout(code_row)
        member_layout.addWidget(label('把集会码分享给想加入的人，不要公开分享。', 'muted'))
        self.capacity_owner = QWidget()
        capacity = QHBoxLayout(self.capacity_owner)
        capacity.setContentsMargins(0, 0, 0, 0)
        capacity.addWidget(label('人数上限'))
        self.member_limit = QSpinBox()
        self._limit_snapshot = None
        self.member_limit.setRange(1, 1000)
        capacity.addWidget(self.member_limit)
        self.limit_save = QPushButton('保存上限')
        self.limit_save.clicked.connect(lambda: self.client.rally_set_limit(self.member_limit.value()))
        capacity.addWidget(self.limit_save)
        member_layout.addWidget(self.capacity_owner)
        self.capacity_hint = label('', 'muted')
        member_layout.addWidget(self.capacity_hint)
        self.leave = QPushButton('离开集会…')
        self.leave.setProperty('variant', 'danger')
        self.leave.clicked.connect(self.leave_rally)
        member_layout.addWidget(self.leave)
        layout.addWidget(self.member_widgets)
        self.network_hint = QPushButton('开启联网…')
        self.network_hint.setProperty('variant', 'quiet')
        self.network_hint.clicked.connect(self.pet.open_buddy)
        layout.addWidget(self.network_hint)
        self.upgrade_hint = label('当前搭子服务尚未支持集会，请等待服务升级；本机按钮仍可使用。', 'muted')
        layout.addWidget(self.upgrade_hint)
        content.addWidget(surface)

        surface, layout = card('警报偏好')
        seconds_row = QHBoxLayout()
        seconds_row.addWidget(label('每次警报持续'))
        self.seconds = QSpinBox()
        self.seconds.setRange(2, 15)
        self.seconds.setSuffix(' 秒')
        self.seconds.setValue(pet.config.alert_seconds)
        self.seconds.valueChanged.connect(lambda value: self.set_option('alert_seconds', value))
        seconds_row.addWidget(self.seconds)
        seconds_row.addStretch()
        layout.addLayout(seconds_row)
        self.receive = QCheckBox('接收集会成员的警报')
        self.receive.setChecked(pet.config.rally_receive)
        self.receive.toggled.connect(lambda checked: self.set_option('rally_receive', checked))
        layout.addWidget(self.receive)
        self.sound = QCheckBox('危险警报播放提示音')
        self.sound.setChecked(pet.config.alert_sound)
        self.sound.toggled.connect(lambda checked: self.set_option('alert_sound', checked))
        layout.addWidget(self.sound)
        layout.addWidget(label('暂停接收远程互动时也不接收集会警报；警报只存在几秒，不保存任何历史。', 'muted'))
        content.addWidget(surface)
        content.addStretch()
        self.message = label('试试按下危险按钮，看看屏幕边框的变化。', 'feedback')
        root.addWidget(self.message)
        self.client.changed.connect(self.refresh)
        self.client.notice.connect(self.show_notice)
        self.pet.alerts.notice.connect(self.show_notice)
        self.refresh()

    def show_notice(self, message):
        self.message.setText(message)

    def create_rally(self):
        limit = self.create_limit.value() if 'rally-settings/v1' in self.client.service_features else None
        self.client.rally_create(self.rally_title.text().strip() or '我们的集会', limit)

    def rebuild_buttons(self):
        buttons = alert_buttons(self.pet.config)
        if buttons == self._buttons_snapshot:
            return
        self._buttons_snapshot = buttons
        while self.buttons_grid.count():
            widget = self.buttons_grid.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        self.signal_buttons, self.hotkey_captions = {}, {}
        for index, item in enumerate(buttons):
            button, caption, column = signal_button(item)
            button.clicked.connect(lambda checked=False, key=item['id']: self.pet.alerts.trigger(key))
            host = QWidget()
            host.setLayout(column)
            self.buttons_grid.addWidget(host, index // 3, index % 3)
            self.signal_buttons[item['id']] = button
            self.hotkey_captions[item['id']] = caption
        for column in range(3):
            self.buttons_grid.setColumnStretch(column, 1)

    def edit_buttons(self):
        editor = AlertButtonsDialog(alert_buttons(self.pet.config), self)
        try:
            if editor.exec() == QDialog.DialogCode.Accepted:
                self.pet.config.alert_buttons = editor.result_buttons
                self.pet.config.alert_hotkeys = {item['id']: item['hotkey'] for item in editor.result_buttons
                                               if item['id'] in ('safe', 'warning', 'danger') and item['hotkey']}
                self.pet.write_config()
                self.pet.alerts.update_config(self.pet.config)
        finally:
            editor.deleteLater()
            self.refresh()

    def set_option(self, key, value):
        setattr(self.pet.config, key, value)
        self.pet.write_config()

    def save_name(self):
        name = self.name.text().strip()
        if name == self.pet.config.rally_name:
            return
        self.pet.config.rally_name = name
        self.pet.write_config()
        if self.client.rally:
            self.client.rally_rename(name)

    def copy_code(self):
        if self.client.rally:
            QApplication.clipboard().setText(self.client.rally['code'])
            self.show_notice('集会码已复制')

    def leave_rally(self):
        rally = self.client.rally
        if not rally:
            return
        text = '你是集会创建者，离开将解散整个集会。确定离开吗？' if rally.get('owner_self') else '离开后不再接收这个集会的警报。确定离开吗？'
        answer = QMessageBox.question(self, '离开集会', text,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.client.rally_leave()

    def refresh(self):
        self.rebuild_buttons()
        client = self.client
        rally = client.rally
        online = client.ready and client.rally_supported
        if rally:
            count = len(rally['members'])
            active = sum(1 for member in rally['members'] if member['status'] == 'available')
            limit = rally.get('member_limit', 12)
            self.rally_status.setText(f"{rally['name']} · {count}/{limit} 位成员 · {active} 位在线")
            self.code_label.setText(f"集会码  {rally['code']}")
        else:
            self.rally_status.setText('还没有加入集会' if online else '联网并加入集会后，警报会同步给成员')
        while self.members.count():
            item = self.members.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if rally:
            status_text = {'available': '在线', 'paused': '暂停', 'offline': '离线'}
            for member in rally['members']:
                suffix = '（我）' if member.get('self') else ''
                row = label(f"{member['name']}{suffix} · {status_text.get(member['status'], '状态未知')}", 'muted')
                self.members.addWidget(row)
        self.join_widgets.setVisible(online and not rally)
        self.member_widgets.setVisible(bool(rally))
        settings = 'rally-settings/v1' in client.service_features
        maximum = client.rally_max_members
        self.capacity_create.setVisible(settings)
        if settings:
            self.create_limit.setMaximum(maximum)
        owner = bool(rally and rally.get('owner_self'))
        self.capacity_owner.setVisible(settings and owner)
        self.limit_save.setEnabled(online)
        if rally:
            self.member_limit.setRange(min(len(rally['members']), maximum), maximum)
            snapshot = (rally['id'], rally.get('member_limit', 12))
            if snapshot != self._limit_snapshot:
                self.member_limit.setValue(rally.get('member_limit', 12))
                self._limit_snapshot = snapshot
        self.capacity_hint.setText(f'创建者可调整人数上限；当前服务允许最多 {maximum} 人。' if settings else '当前服务使用旧版人数设置。')
        self.name.setEnabled(bool(rally))
        if not self.name.hasFocus():
            self.name.setText(self.pet.config.rally_name)
        self.network_hint.setVisible(not client.enabled)
        self.upgrade_hint.setVisible(client.enabled and client.ready and not client.rally_supported)
        controller = self.pet.alerts
        config = self.pet.config
        self.notification_status.setText(
            ('免打扰中：消息和警报不展示，恢复请使用托盘菜单。' if config.notifications_muted else '通知已开启。')
            + f'\n输入消息：{config.chat_hotkey or "未设置"}；只关闭通知：{config.mute_hotkey or "未设置"}')
        if not any(item.get('hotkey') for item in alert_buttons(self.pet.config)):
            self.hotkey_status.setText('未设置全局快捷键，可直接点击按钮。')
        elif controller.hotkeys is None:
            self.hotkey_status.setText('全局热键在当前环境不可用，可直接点击按钮。')
        elif controller.hotkeys.failed:
            failed = '、'.join(text for _, text in controller.hotkeys.failed)
            self.hotkey_status.setText(f'热键 {failed} 注册失败（可能被其他软件占用），请在“统一快捷键设置”中修改。')
        else:
            self.hotkey_status.setText('全局热键已注册，在任何软件中按下都能触发。')

    def closeEvent(self, event):
        self.hide()
        event.ignore()
