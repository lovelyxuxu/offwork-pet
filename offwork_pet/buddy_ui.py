"""Small modeless companion panel, separate from local pet interactions."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget)

from .ui_theme import card, label, scroll, style_window


class BuddyDialog(QDialog):
    def __init__(self, pet):
        super().__init__()
        self.pet, self.client = pet, pet.buddy
        self.setWindowTitle('下班搭子 · 搭子')
        self.setMinimumSize(500, 580)
        self.resize(530, 700)
        style_window(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(12)
        root.addWidget(label('把陪伴，轻轻送过去。', 'heading'))
        root.addWidget(label('连上一个搭子，让两只宠物互相回应。', 'subtitle'))
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 4, 0)
        content.setSpacing(14)
        root.addWidget(scroll(body), 1)
        surface, layout = card('连接状态')
        row = QHBoxLayout()
        self.connection = label('')
        row.addWidget(self.connection, 1)
        self.toggle = QPushButton()
        self.toggle.setProperty('variant', 'primary')
        self.toggle.clicked.connect(self.toggle_network)
        row.addWidget(self.toggle)
        layout.addLayout(row)
        content.addWidget(surface)
        surface, layout = card('我的搭子')
        self.partner = label('', 'sectionTitle')
        layout.addWidget(self.partner)
        self.note = QLineEdit(pet.config.buddy_note)
        self.note.setPlaceholderText('给搭子取个称呼 · 仅自己可见')
        self.note.setMaxLength(24)
        self.note.editingFinished.connect(self.save_note)
        layout.addWidget(self.note)
        play = QPushButton('打开搭子互动面板  →')
        play.setProperty('variant', 'primary')
        play.clicked.connect(lambda: pet.open_actions(1))
        layout.addWidget(play)
        self.unlink_button = QPushButton('解除搭子连接…')
        self.unlink_button.setProperty('variant', 'quiet')
        self.unlink_button.clicked.connect(self.unlink)
        layout.addWidget(self.unlink_button)
        content.addWidget(surface)
        self.invite_card, layout = card('邀请一个人', '你生成邀请码给对方，或输入对方的邀请码。')
        invitation = QHBoxLayout()
        self.generate = QPushButton('生成邀请')
        self.generate.setProperty('variant', 'primary')
        self.generate.clicked.connect(self.client.invite)
        self.copy = QPushButton('复制')
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.client.invitation['code']) if self.client.invitation else None)
        self.revoke = QPushButton('撤销')
        self.revoke.clicked.connect(self.client.revoke)
        for button in (self.generate, self.copy, self.revoke):
            invitation.addWidget(button)
        layout.addLayout(invitation)
        self.invite_label = label('', 'muted')
        layout.addWidget(self.invite_label)
        redeem = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText('输入 8 位邀请码')
        self.code.setMaxLength(8)
        self.join = QPushButton('连接搭子')
        self.join.clicked.connect(lambda: self.client.redeem(self.code.text().strip().upper()))
        redeem.addWidget(self.code)
        redeem.addWidget(self.join)
        layout.addLayout(redeem)
        content.addWidget(self.invite_card)
        surface, layout = card('互动偏好')
        self.paused = QCheckBox('暂停接收搭子的互动')
        self.paused.setChecked(pet.config.paused)
        self.paused.toggled.connect(self.pause_changed)
        layout.addWidget(self.paused)
        self.remote_text = QCheckBox('显示互动小提示')
        self.remote_text.setChecked(pet.config.remote_text)
        self.remote_text.toggled.connect(lambda checked: self.set_option('remote_text', checked))
        layout.addWidget(self.remote_text)
        self.remote_sound = QCheckBox('收到互动时播放提示音')
        self.remote_sound.setChecked(pet.config.remote_sound)
        self.remote_sound.toggled.connect(lambda checked: self.set_option('remote_sound', checked))
        layout.addWidget(self.remote_sound)
        layout.addWidget(label('对方离线或暂停时，不补发动作。', 'muted'))
        content.addWidget(surface)
        content.addStretch()
        self.message = label('右键宠物打开互动面板，连续选择动作更方便。', 'feedback')
        root.addWidget(self.message)
        self.client.changed.connect(self.refresh)
        self.client.notice.connect(self.show_notice)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_invite)
        self.timer.start(1000)
        self.refresh()

    def show_notice(self, message):
        self.message.setText(message)

    def set_option(self, key, value):
        setattr(self.pet.config, key, value)
        self.pet.write_config()

    def pause_changed(self, value):
        self.pet.set_paused(value)

    def toggle_network(self):
        if self.client.enabled:
            self.client.stop()
            self.pet.config.network_enabled = False
        else:
            self.pet.config.network_enabled = True
            self.client.config = self.pet.config
            self.client.start()
        self.pet.write_config()
        self.refresh()

    def save_note(self):
        self.pet.config.buddy_note = self.note.text().strip()
        self.pet.write_config()
        self.refresh()

    def unlink(self):
        answer = QMessageBox.question(self, '解除搭子连接', '解除后需要重新邀请才能互动。确定解除吗？',
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.pet.clear_remote()
            self.client.unlink()

    def refresh_invite(self):
        if self.client.invitation:
            import time
            remaining = max(0, int(self.client.invitation['expires_at'] - time.time() - self.client.clock_offset))
            self.invite_label.setText(f"{self.client.invitation['code']}  ·  {remaining // 60:02}:{remaining % 60:02} 后失效")
        else:
            self.invite_label.setText('主动生成后分享给想连接的人，不要公开分享。')

    def refresh(self):
        client = self.client
        self.connection.setText(client.connection_text)
        self.toggle.setText('关闭联网' if client.enabled else '开启联网')
        pending = bool(client.identity and client.identity.record.get('pending_unlink'))
        paired = client.relation is not None
        status = {'available': '可互动', 'paused': '暂不接收互动', 'offline': '暂不可达'}.get(client.status, '状态未知')
        if not client.connected:
            status = '状态未知'
        if paired:
            capabilities = client.relation.get('capabilities') or {}
            character = capabilities.get('displayName', '宠物')
            self.partner.setText(f"{self.pet.config.buddy_note or '我的搭子'} · {character} · {status}")
        else:
            self.partner.setText('还没有连接搭子')
        for widget in (self.generate, self.code, self.join):
            widget.setEnabled(client.ready and not paired and not pending)
        for button in (self.copy, self.revoke):
            button.setEnabled(client.ready and bool(client.invitation))
        self.invite_card.setVisible(not paired)
        self.unlink_button.setEnabled(paired and not pending)
        self.note.setEnabled(paired)
        if not self.note.hasFocus():
            self.note.setText(self.pet.config.buddy_note)
        self.paused.blockSignals(True)
        self.paused.setChecked(self.pet.config.paused)
        self.paused.blockSignals(False)
        self.refresh_invite()

    def closeEvent(self, event):
        self.hide()
        event.ignore()
