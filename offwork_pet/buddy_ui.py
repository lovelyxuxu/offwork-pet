"""Small modeless companion panel, separate from local pet interactions."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from .credentials import normalize_endpoint

NAMES = {'lulu': '噜噜', 'lumei': '噜妹'}


class BuddyDialog(QDialog):
    def __init__(self, pet):
        super().__init__()
        self.pet, self.client = pet, pet.buddy
        self.setWindowTitle('下班搭子 · 搭子')
        self.setMinimumWidth(440)
        self.setStyleSheet('QDialog {background:#FAF8F4;} QLabel,QCheckBox {color:#55483E;font-size:13px;} QLineEdit {padding:7px;} QPushButton {padding:8px 14px;}')
        root = QVBoxLayout(self)
        title = QLabel('把陪伴，轻轻送过去。')
        title.setStyleSheet('font-size:22px;font-weight:600;margin:12px 0;')
        root.addWidget(title)
        form = QFormLayout()
        self.endpoint = QLineEdit(pet.config.relay_url)
        self.endpoint.setPlaceholderText('https://你的搭子服务地址')
        form.addRow('服务地址', self.endpoint)
        root.addLayout(form)
        self.toggle = QPushButton()
        self.toggle.clicked.connect(self.toggle_network)
        root.addWidget(self.toggle)
        self.connection = QLabel()
        self.connection.setWordWrap(True)
        root.addWidget(self.connection)
        self.partner = QLabel()
        self.partner.setStyleSheet('font-size:17px;font-weight:600;margin:14px 0;')
        self.partner.setWordWrap(True)
        root.addWidget(self.partner)
        self.note = QLineEdit(pet.config.buddy_note)
        self.note.setPlaceholderText('本机备注称呼（不发送给对方）')
        self.note.setMaxLength(24)
        self.note.editingFinished.connect(self.save_note)
        root.addWidget(self.note)
        invitation = QHBoxLayout()
        self.generate = QPushButton('生成邀请')
        self.generate.clicked.connect(self.client.invite)
        self.copy = QPushButton('复制邀请码')
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.client.invitation['code']) if self.client.invitation else None)
        self.revoke = QPushButton('撤销')
        self.revoke.clicked.connect(self.client.revoke)
        for button in (self.generate, self.copy, self.revoke):
            invitation.addWidget(button)
        root.addLayout(invitation)
        self.invite_label = QLabel('主动生成后分享给想连接的人，不要公开分享。')
        self.invite_label.setWordWrap(True)
        root.addWidget(self.invite_label)
        redeem = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText('输入对方的 8 位邀请码')
        self.code.setMaxLength(8)
        self.join = QPushButton('连接搭子')
        self.join.clicked.connect(lambda: self.client.redeem(self.code.text().strip().upper()))
        redeem.addWidget(self.code)
        redeem.addWidget(self.join)
        root.addLayout(redeem)
        actions = QHBoxLayout()
        self.pinch = QPushButton('捏捏脸')
        self.bonk = QPushButton('轻捶一下')
        self.pinch.clicked.connect(lambda: self.client.queue_action('pinch'))
        self.bonk.clicked.connect(lambda: self.client.queue_action('bonk'))
        actions.addWidget(self.pinch)
        actions.addWidget(self.bonk)
        root.addLayout(actions)
        self.paused = QCheckBox('暂停接收互动')
        self.paused.setChecked(pet.config.paused)
        self.paused.toggled.connect(self.pause_changed)
        root.addWidget(self.paused)
        self.remote_text = QCheckBox('显示互动小提示（独立于倒计时气泡）')
        self.remote_text.setChecked(pet.config.remote_text)
        self.remote_text.toggled.connect(lambda checked: self.set_option('remote_text', checked))
        root.addWidget(self.remote_text)
        self.remote_sound = QCheckBox('收到互动时播放提示音')
        self.remote_sound.setChecked(pet.config.remote_sound)
        self.remote_sound.toggled.connect(lambda checked: self.set_option('remote_sound', checked))
        root.addWidget(self.remote_sound)
        self.unlink_button = QPushButton('解除搭子连接…')
        self.unlink_button.clicked.connect(self.unlink)
        root.addWidget(self.unlink_button)
        self.message = QLabel('不启用联网时，所有本地功能照常可用。')
        self.message.setWordWrap(True)
        self.message.setStyleSheet('color:#9A745D;margin:12px 0;')
        root.addWidget(self.message)
        hint = QLabel('在线状态不代表对方本人看见互动。\n对方不在线或暂停时不补发动作。')
        hint.setStyleSheet('color:#93877B;font-size:12px;')
        root.addWidget(hint)
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
            try:
                endpoint = normalize_endpoint(self.endpoint.text())
            except ValueError as error:
                self.message.setText(str(error))
                return
            if self.pet.config.relay_url != endpoint:
                self.pet.config.buddy_note = ''
            self.pet.config.relay_url = endpoint
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
        self.toggle.setText('关闭联网（保留搭子关系）' if client.enabled else '开启联网')
        self.endpoint.setEnabled(not client.enabled)
        pending = bool(client.identity and client.identity.record.get('pending_unlink'))
        paired = client.relation is not None
        status = {'available': '可互动', 'paused': '暂不接收互动', 'offline': '暂不可达'}.get(client.status, '状态未知')
        if not client.connected:
            status = '状态未知'
        if paired:
            character = NAMES.get(client.relation.get('character'), '宠物')
            self.partner.setText(f"{self.pet.config.buddy_note or '我的搭子'} · {character} · {status}")
        else:
            self.partner.setText('还没有连接搭子')
        for widget in (self.generate, self.code, self.join):
            widget.setEnabled(client.ready and not paired and not pending)
        for button in (self.copy, self.revoke):
            button.setEnabled(client.ready and bool(client.invitation))
        for button in (self.pinch, self.bonk):
            button.setEnabled(client.ready and paired and client.status == 'available' and not pending)
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
