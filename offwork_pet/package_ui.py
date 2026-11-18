"""Install and manage character content without exposing paths or rule syntax."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QLineEdit)

from .packages import PackageError
from .ui_theme import card, label, style_window


class PackageDialog(QDialog):
    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.setWindowTitle('角色管理')
        self.setMinimumSize(550, 520)
        style_window(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        root.addWidget(label('选择今天的搭子', 'heading'))
        root.addWidget(label('每一个角色，都有自己的动作与小脾气。', 'subtitle'))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.selection)
        root.addWidget(self.list)
        surface, detail_layout = card()
        detail_row = QHBoxLayout()
        self.preview = QLabel()
        self.preview.setFixedHeight(100)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail_row.addWidget(self.preview)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        detail_row.addWidget(self.detail, 1)
        detail_layout.addLayout(detail_row)
        root.addWidget(surface)
        names = QHBoxLayout()
        self.nickname = QLineEdit()
        self.nickname.setPlaceholderText('给这个搭子起个昵称（只存本机）')
        self.nickname.setMaxLength(24)
        names.addWidget(self.nickname)
        rename = QPushButton('保存昵称')
        rename.clicked.connect(self.rename)
        names.addWidget(rename)
        reset = QPushButton('重置记忆')
        reset.clicked.connect(self.reset_memory)
        names.addWidget(reset)
        root.addLayout(names)
        self.dialogue_button = QPushButton('角色台词 · 选择、导入与试读…')
        self.dialogue_button.clicked.connect(self.open_dialogue)
        root.addWidget(self.dialogue_button)
        self.dialogue_dialog = None
        row = QHBoxLayout()
        add = QPushButton('导入角色包…')
        add.clicked.connect(self.import_package)
        self.use = QPushButton('使用这个角色')
        self.use.setProperty('variant', 'primary')
        self.use.clicked.connect(self.activate)
        self.delete = QPushButton('删除角色')
        self.delete.setProperty('variant', 'danger')
        self.delete.clicked.connect(self.remove)
        for button in (add, self.use, self.delete):
            row.addWidget(button)
        root.addLayout(row)
        create = QPushButton('制作自己的角色…')
        create.clicked.connect(pet.open_creator)
        root.addWidget(create)
        browse = QPushButton('浏览在线角色…')
        browse.clicked.connect(self.browse_online)
        root.addWidget(browse)
        self.workshop = None
        self.message = QLabel('导入 .petpack 文件即可使用，不需要手动解压。')
        self.message.setObjectName('muted')
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.message)
        self.refresh()

    def browse_online(self):
        from .workshop_ui import WorkshopDialog
        if self.workshop is None:
            self.workshop = WorkshopDialog(self)
        self.workshop.show()
        self.workshop.raise_()
        if not self.workshop.list.count():
            self.workshop.load()

    def refresh(self):
        self.list.clear()
        active_row = 0
        for entry in self.pet.library.entries():
            item = QListWidgetItem(entry['displayName'] + (' · 使用中' if entry['id'] == self.pet.config.character else ''))
            item.setData(Qt.ItemDataRole.UserRole, entry['id'])
            if entry['id'] == self.pet.config.character:
                active_row = self.list.count()
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(active_row)
        else:
            self.preview.clear()
            self.detail.setText('还没有角色。导入一个角色包，开始陪伴。')
            self.use.setEnabled(False)
            self.delete.setEnabled(False)

    def selected(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def open_dialogue(self):
        identifier = self.selected()
        if identifier:
            from .dialogue_ui import DialogueDialog
            if self.dialogue_dialog:
                self.dialogue_dialog.close()
                self.dialogue_dialog.deleteLater()
            self.dialogue_dialog = DialogueDialog(self.pet, identifier)
            self.dialogue_dialog.show()
            self.dialogue_dialog.raise_()

    def selection(self, *_):
        identifier = self.selected()
        self.dialogue_button.setEnabled(identifier is not None)
        self.use.setEnabled(identifier is not None)
        self.delete.setEnabled(identifier is not None)
        if identifier:
            entry = self.pet.library.index['packages'][identifier]
            memory = self.pet.companion_memory.entry(identifier)
            self.nickname.setText(memory['nickname'])
            self.detail.setText(f"作者：{entry['author']}\n版本：{entry['version']}\n{self.pet.companion_memory.describe(identifier)}")
            try:
                icon = QPixmap(str(self.pet.library.path(identifier) / entry['thumbnail']))
                self.preview.setPixmap(icon.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            except PackageError:
                self.preview.clear()

    def rename(self):
        key = self.selected()
        if key:
            self.pet.companion_memory.entry(key)['nickname'] = self.nickname.text().strip()
            self.pet.write_config()
            if self.pet.action_panel:
                self.pet.action_panel.refresh()
            self.message.setText('昵称已保存。')

    def reset_memory(self):
        key = self.selected()
        if key and QMessageBox.question(self, '重置记忆', '清除这个角色的昵称与互动偏好？') == QMessageBox.StandardButton.Yes:
            self.pet.companion_memory.data.pop(key, None)
            self.pet.companion_memory.last.pop(key, None)
            if self.pet.runtime and self.pet.runtime.package.id == key:
                self.pet.runtime.preferences = self.pet.companion_memory.entry(key)['favorites'] if self.pet.config.personality_enabled else {}
            self.pet.write_config()
            self.selection()

    def import_package(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入角色包', '', '角色包 (*.petpack)')
        if not path:
            return
        try:
            # Release the active package before updating it only after the new file validated.
            package = self.pet.import_package(path)
            self.refresh()
            self.message.setText(f'已导入并启用：{package.name}')
        except (PackageError, OSError) as error:
            self.message.setText(str(error))

    def activate(self):
        identifier = self.selected()
        if identifier is None:
            return
        self.message.setText('正在准备角色…')
        def finished(error):
            if error is None:
                self.refresh()
            self.message.setText(str(error) if error else '已切换角色。')
        self.pet.character_loader.request(identifier, finished)

    def remove(self):
        identifier = self.selected()
        if identifier is None:
            return
        if QMessageBox.question(self, '删除角色', '删除此角色及其素材？之后可以重新导入。') != QMessageBox.StandardButton.Yes:
            return
        try:
            active = identifier == self.pet.config.character
            if active:
                self.pet.select_package(None)
            self.pet.library.remove(identifier)
            if active and self.pet.library.entries():
                self.pet.select_package(self.pet.library.entries()[0]['id'])
            self.refresh()
            self.message.setText('已删除角色。')
        except (PackageError, OSError) as error:
            if active:
                try:
                    self.pet.select_package(identifier)
                except (PackageError, OSError):
                    pass
            self.message.setText(str(error))
