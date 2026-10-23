"""Visual authoring of data-only packs, using the same validator and player as the pet."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_DEFLATED

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .behavior import BehaviorRuntime
from .pack_builder import make_template, add_action, write_json
from .package_player import PackagePlayer
from .packages import PackageError, PackageLibrary, load_package
from .ui_theme import label, style_window


class PackPreview(QWidget):
    region_selected = Signal(list)

    def __init__(self, parent):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.setMaximumSize(360, 360)
        self.player = self.runtime = None
        self.region = self.begin = None
        self.testing = False
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.advance)

    def load(self, package):
        if self.player:
            self.player.close()
        self.runtime = BehaviorRuntime(package)
        self.player = PackagePlayer(self.runtime)
        self.region = None
        self.update()

    def advance(self):
        if self.runtime:
            self.runtime.tick()
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor('#F1F0F7'))
        if not self.player:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '选择或拖入 PNG / GIF / WebP\n开始制作自己的角色')
            return
        p.translate(10, 10)
        self.player.draw(p)
        if self.region:
            p.setPen(QPen(QColor('#8A73CD'), 2, Qt.PenStyle.DashLine))
            p.setBrush(QColor(138, 115, 205, 35))
            p.drawRect(self.player.transform.mapRect(QRectF(*self.region)))

    def point(self, position):
        x, y = self.player.point(position-QPointF(10, 10))
        canvas = self.runtime.package.manifest['canvas']
        return max(0, min(canvas['width'], x)), max(0, min(canvas['height'], y))

    def mousePressEvent(self, event):
        if self.player and event.button() == Qt.MouseButton.LeftButton:
            self.begin = self.point(event.position())

    def mouseReleaseEvent(self, event):
        if self.player and self.begin is not None:
            end = self.point(event.position())
            if self.testing:
                self.runtime.event('pointer.click', end, {'source': 'preview'})
            else:
                x, y = min(self.begin[0], end[0]), min(self.begin[1], end[1])
                width, height = abs(end[0]-self.begin[0]), abs(end[1]-self.begin[1])
                if width >= 1 and height >= 1:
                    self.region = [x, y, width, height]
                    self.region_selected.emit(self.region)
            self.begin = None
            self.update()

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)


class CreatorDialog(QDialog):
    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.temp = TemporaryDirectory()
        self.folder = None
        self.dirty = False
        self.setWindowTitle('角色制作室')
        self.resize(820, 640)
        self.setAcceptDrops(True)
        style_window(self)
        root = QVBoxLayout(self)
        root.addWidget(label('把你的角色带到桌面上', 'heading'))
        root.addWidget(label('导入已有图片或动画，添加动作，圈选互动区域，然后导出分享。', 'muted'))
        toolbar = QHBoxLayout()
        for title, callback in [('选择基础图片 / GIF', self.choose_base), ('打开角色包', self.open_pack),
                                ('导出角色包', self.export), ('导出并使用', self.install)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        root.addLayout(toolbar)
        columns = QHBoxLayout()
        left = QVBoxLayout()
        self.preview = PackPreview(self)
        self.preview.region_selected.connect(self.set_region)
        left.addWidget(self.preview)
        self.try_input = QCheckBox('试互动：单击预览角色（关闭后可拖动圈区域）')
        self.try_input.toggled.connect(lambda value: setattr(self.preview, 'testing', value))
        left.addWidget(self.try_input)
        self.actions = QListWidget()
        self.actions.currentRowChanged.connect(self.select_action)
        left.addWidget(self.actions)
        add = QPushButton('添加动作图片 / GIF…')
        add.clicked.connect(self.choose_action)
        left.addWidget(add)
        columns.addLayout(left, 1)
        form = QFormLayout()
        self.name, self.identifier, self.author, self.version = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit('1.0.0')
        for title, widget in [('角色名称', self.name), ('角色标识', self.identifier), ('作者', self.author), ('版本', self.version)]:
            form.addRow(title, widget)
        self.source, self.license = QLineEdit(), QLineEdit()
        self.source.setMaxLength(400)
        self.license.setMaxLength(200)
        form.addRow('素材来源', self.source)
        form.addRow('素材授权', self.license)
        self.identifier.setPlaceholderText('例如 my.cat（字母、数字、点或横线）')
        self.action_label, self.speech = QLineEdit(), QLineEdit()
        self.speech.setMaxLength(200)
        form.addRow('动作名称', self.action_label)
        form.addRow('动作台词', self.speech)
        self.trigger = QComboBox()
        for title, event in [('仅菜单', ''), ('单击区域', 'pointer.click'), ('长按区域', 'pointer.hold'),
                ('抚摸区域', 'gesture.stroke'), ('下班前十分钟', 'schedule.soon'), ('下班时', 'schedule.offwork'), ('专注结束', 'focus.finished')]:
            self.trigger.addItem(title, event)
        form.addRow('触发方式', self.trigger)
        self.remote = QCheckBox('同时开放给搭子远程触发')
        form.addRow(self.remote)
        self.region_hint = label('可在左侧拖动圈选互动区域；未圈选时作用于整个角色。', 'muted')
        form.addRow(self.region_hint)
        save = QPushButton('应用设置并预览动作')
        save.setProperty('variant', 'primary')
        save.clicked.connect(self.apply_and_play)
        form.addRow(save)
        columns.addLayout(form, 1)
        root.addLayout(columns, 1)
        self.message = label('素材尺寸需要一致；导出时会检查素材与所有引用。', 'muted')
        root.addWidget(self.message)
        self.action_keys = []
        self.editing_key = None
        self.pending_region = None
        for widget in (self.name, self.identifier, self.author, self.version, self.source, self.license, self.action_label, self.speech):
            widget.textEdited.connect(lambda _: setattr(self, 'dirty', True))
        self.trigger.activated.connect(lambda _: setattr(self, 'dirty', True))
        self.remote.clicked.connect(lambda _: setattr(self, 'dirty', True))

    def guard_replace(self):
        return not self.dirty or QMessageBox.question(self, '替换当前草稿', '当前草稿尚未导出，确定换一个角色？') == QMessageBox.StandardButton.Yes

    def choose_base(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择基础素材', '', '图片与动画 (*.png *.gif *.webp)')
        if path:
            self.new_base(path)

    def new_base(self, path):
        if not self.guard_replace():
            return
        try:
            import uuid
            folder = Path(self.temp.name)/uuid.uuid4().hex
            make_template(SimpleNamespace(image=Path(path), id='creator.'+uuid.uuid4().hex[:8], name=Path(path).stem,
                author='我的创作', license='请由创作者补充实际素材授权', output=folder))
            self.folder, self.dirty = folder, True
            self.refresh()
        except (PackageError, OSError, ValueError) as error:
            self.message.setText(str(error))

    def open_pack(self):
        path, _ = QFileDialog.getOpenFileName(self, '打开角色包', '', '角色包 (*.petpack)')
        if not path or not self.guard_replace():
            return
        try:
            import uuid
            library = PackageLibrary(Path(self.temp.name)/uuid.uuid4().hex)
            package = library.install(path)
            self.folder, self.dirty = package.root, False
            self.refresh()
        except (PackageError, OSError, ValueError) as error:
            self.message.setText(str(error))

    def refresh(self):
        package = load_package(self.folder)
        self.name.setText(package.name)
        self.identifier.setText(package.id)
        self.author.setText(package.manifest['author'])
        self.version.setText(package.manifest['version'])
        credit = next(iter(package.manifest.get('credits', [])), {})
        self.source.setText(credit.get('source', ''))
        self.license.setText(credit.get('license', ''))
        self.preview.load(package)
        self.editing_key = None
        self.action_keys = list(package.behavior['actions'])
        self.actions.blockSignals(True)
        self.actions.clear()
        for key in self.action_keys:
            item = next((item for item in package.behavior.get('menu', []) if item['invoke'].get('id') == key), {})
            self.actions.addItem(item.get('label', key))
        self.actions.setCurrentRow(0 if self.action_keys else -1)
        self.actions.blockSignals(False)
        self.select_action()

    def selected(self):
        row = self.actions.currentRow()
        return self.action_keys[row] if 0 <= row < len(self.action_keys) else None

    def select_action(self, *_):
        key = self.selected()
        if self.editing_key and key != self.editing_key:
            try:
                self.save_edits(action_key=self.editing_key)
            except (PackageError, OSError, ValueError) as error:
                self.message.setText(str(error))
                self.actions.blockSignals(True)
                self.actions.setCurrentRow(self.action_keys.index(self.editing_key))
                self.actions.blockSignals(False)
                return
        self.editing_key = key
        self.pending_region = None
        self.preview.region = None
        if not key or not self.preview.runtime:
            return
        data = self.preview.runtime.data
        item = next((item for item in data.get('menu', []) if item['invoke'].get('id') == key), {})
        self.action_label.setText(item.get('label', key))
        self.speech.setText(data['actions'][key].get('text', ''))
        rule = next((r for r in data.get('rules', []) if r['id'] == 'editor.'+key), {})
        self.trigger.setCurrentIndex(max(0, self.trigger.findData(rule.get('event', ''))))
        self.remote.setChecked(any(item['invoke'].get('id') == key for item in data.get('interactions', [])))
        if rule.get('region') in data.get('regions', {}):
            self.pending_region = data['regions'][rule['region']]['rect']
            self.preview.region = self.pending_region

    def choose_action(self):
        if not self.folder:
            self.message.setText('先选择基础素材。')
            return
        path, _ = QFileDialog.getOpenFileName(self, '选择动作素材', '', '图片与动画 (*.png *.gif *.webp)')
        if not path:
            return
        try:
            import uuid
            self.save_edits()
            add_action(SimpleNamespace(folder=self.folder, image=Path(path), id='action_'+uuid.uuid4().hex[:8], label=Path(path).stem, remote=False))
            self.dirty = True
            self.refresh()
            self.actions.setCurrentRow(len(self.action_keys)-1)
        except (PackageError, OSError, ValueError) as error:
            self.message.setText(str(error))

    def set_region(self, rect):
        self.pending_region = rect
        self.dirty = True
        self.region_hint.setText('已圈选区域。选择单击、长按或抚摸后，点击“应用设置”。')

    def save_edits(self, action_key=None):
        if not self.folder:
            raise PackageError('请先选择基础素材或打开角色包')
        package = load_package(self.folder)
        manifest, data = deepcopy(package.manifest), deepcopy(package.behavior)
        manifest.update(id=self.identifier.text().strip(), displayName=self.name.text().strip(),
                        author=self.author.text().strip(), version=self.version.text().strip())
        credits = manifest.setdefault('credits', [])
        if not credits:
            credits.append({'author': self.author.text().strip()})
        credits[0].update(source=self.source.text().strip(), license=self.license.text().strip())
        key, event = action_key or self.selected(), self.trigger.currentData()
        if key:
            data['actions'][key]['text'] = self.speech.text().strip() or self.action_label.text().strip()
            entry = {'id': 'editor.'+key, 'label': self.action_label.text().strip(), 'invoke': {'type': 'action', 'id': key}}
            for group, enabled in [('menu', True), ('interactions', self.remote.isChecked())]:
                data[group] = [item for item in data.get(group, []) if item['invoke'].get('id') != key]
                if enabled:
                    data[group].append(entry)
            data['rules'] = [rule for rule in data.get('rules', []) if rule['id'] != 'editor.'+key]
            if event:
                rule = {'id': 'editor.'+key, 'event': event, 'then': {'type': 'action', 'id': key}}
                if self.pending_region and event.startswith(('pointer.', 'gesture.')):
                    region = 'editor.'+key
                    data.setdefault('regions', {})[region] = {'shape': 'rect', 'rect': self.pending_region}
                    rule['region'] = region
                data['rules'].append(rule)
                capability = ('schedule.events/v1' if event.startswith('schedule.') else 'focus.events/v1'
                              if event.startswith('focus.') else 'input.stroke/v1' if event.startswith('gesture.') else 'input.pointer/v1')
                if capability not in manifest['requires']:
                    manifest['requires'].append(capability)
        old_manifest = (self.folder/'manifest.json').read_bytes()
        behavior_path = self.folder/manifest['behavior']
        old_behavior = behavior_path.read_bytes()
        try:
            write_json(self.folder/'manifest.json', manifest)
            write_json(behavior_path, data)
            package = load_package(self.folder)
        except Exception:
            (self.folder/'manifest.json').write_bytes(old_manifest)
            behavior_path.write_bytes(old_behavior)
            raise
        self.preview.load(package)
        self.preview.region = self.pending_region
        return package

    def apply_and_play(self):
        try:
            self.save_edits()
            self.dirty = True
            if self.selected():
                self.preview.runtime.request(self.selected(), {'source': 'preview'})
            self.message.setText('设置已应用。可以试互动，或导出角色包。')
        except (PackageError, OSError, ValueError) as error:
            self.message.setText(str(error))

    def export(self, checked=False, target=None):
        try:
            package = self.save_edits()
            if target is None:
                path, _ = QFileDialog.getSaveFileName(self, '导出角色包', package.id+'.petpack', '角色包 (*.petpack)')
                if not path:
                    return None
                target = Path(path)
            target = Path(target)
            if target.resolve().is_relative_to(self.folder.resolve()):
                raise PackageError('导出文件应放在角色源目录之外')
            if target.suffix.lower() != '.petpack':
                target = target.with_suffix('.petpack')
            temporary = target.with_suffix('.petpack.tmp')
            try:
                with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
                    for path in sorted(self.folder.rglob('*')):
                        if path.is_file():
                            archive.write(path, path.relative_to(self.folder).as_posix())
                with TemporaryDirectory() as check:
                    PackageLibrary(check).install(temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            self.dirty = False
            self.message.setText('已导出：'+str(target))
            return target
        except (PackageError, OSError, ValueError) as error:
            self.message.setText(str(error))
            return None

    def install(self):
        target = self.export()
        if target:
            try:
                self.pet.import_package(target)
                self.message.setText('已导出并启用。')
            except (PackageError, OSError) as error:
                self.message.setText(str(error))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() in ('.png', '.gif', '.webp'):
            self.new_base(urls[0].toLocalFile())

    def closeEvent(self, event):
        if self.dirty:
            answer = QMessageBox.question(self, '保留你的创作', '导出角色包后再关闭？',
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel or answer == QMessageBox.StandardButton.Save and not self.export():
                event.ignore()
                return
            self.dirty = False
        event.accept()
