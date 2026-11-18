"""Choose or import a character's dialogue, with isolated text previews."""
import random
from PySide6.QtCore import QTimer
from datetime import datetime
import math
from PySide6.QtWidgets import QComboBox, QDialog, QFileDialog, QHBoxLayout, QPushButton, QVBoxLayout
from .dialogue import DialogueError, scene_timing
from .ui_theme import card, label, style_window


class DialogueDialog(QDialog):
    def __init__(self, pet, character_id):
        super().__init__(pet)
        self.pet, self.character_id = pet, character_id
        self.setWindowTitle('角色台词')
        self.setMinimumWidth(500)
        style_window(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        entry = pet.library.index['packages'].get(character_id, {})
        root.addWidget(label(entry.get('displayName', '角色') + '的碎碎念', 'heading'))
        root.addWidget(label('选择一份台词，陪伴也有自己的口吻。', 'subtitle'))
        self.packs = QComboBox()
        root.addWidget(self.packs)
        self.detail = label('')
        root.addWidget(self.detail)
        surface, layout = card('试读一句')
        self.scenes = QComboBox()
        layout.addWidget(self.scenes)
        self.timing = label('', 'muted')
        layout.addWidget(self.timing)
        self.sample = label('选一个场景，看看它会说什么。')
        self.sample.setMinimumHeight(65)
        layout.addWidget(self.sample)
        self.next = QPushButton('再随机一句')
        self.next.clicked.connect(self.preview)
        layout.addWidget(self.next)
        self.play_preview = QPushButton('让桌宠说这句')
        self.play_preview.clicked.connect(self.preview_on_pet)
        layout.addWidget(self.play_preview)
        root.addWidget(surface)
        self.status = label('', 'muted')
        root.addWidget(self.status)
        self.feedback = label('这里展示文字，不播放声音。预览不会消耗正式提醒次数。', 'muted')
        root.addWidget(self.feedback)
        row = QHBoxLayout()
        add = QPushButton('导入台词包…')
        add.clicked.connect(self.import_pack)
        self.save = QPushButton('保存选择')
        self.save.setProperty('variant', 'primary')
        self.save.clicked.connect(self.activate)
        row.addWidget(add)
        row.addWidget(self.save)
        root.addLayout(row)
        self.packs.currentIndexChanged.connect(self.selection)
        self.scenes.currentIndexChanged.connect(self.preview)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(2000)
        self.status_timer.timeout.connect(self.refresh_status)
        self.refresh()

    def refresh(self, selected=None):
        active = self.pet.dialogue_library.selected(self.character_id, self.pet.config.dialogue_packs)
        selected = selected if selected is not None else (active['id'] if active else '')
        self.packs.blockSignals(True)
        self.packs.clear()
        self.packs.addItem('关闭这个角色的台词', '')
        for pack in self.pet.dialogue_library.entries(self.character_id):
            self.packs.addItem(pack['displayName'] + ' · v' + pack['version'], pack['id'])
        self.packs.setCurrentIndex(max(0, self.packs.findData(selected)))
        self.packs.blockSignals(False)
        self.selection()

    def selection(self, *_):
        pack = self.pet.dialogue_library.packs.get(self.packs.currentData())
        self.scenes.blockSignals(True)
        self.scenes.clear()
        if pack:
            count = sum(len(scene['lines']) for scene in pack['scenes'])
            self.detail.setText(f"{pack.get('description', '角色台词')}\n{len(pack['scenes'])} 个场景 · {count} 句 · 中文文字")
            for index, scene in enumerate(pack['scenes']):
                self.scenes.addItem(f"{scene['label']} · {len(scene['lines'])}句", index)
        else:
            self.detail.setText('不展示此角色的台词；角色动画和倒计时照常使用。')
        self.scenes.blockSignals(False)
        self.next.setEnabled(bool(pack))
        self.play_preview.setEnabled(bool(pack) and self.character_id == self.pet.config.character)
        self.preview()

    def preview(self, *_):
        pack = self.pet.dialogue_library.packs.get(self.packs.currentData())
        index = self.scenes.currentData()
        if pack and index is not None:
            self.timing.setText(scene_timing(pack['scenes'][index], pack, self.pet.config))
            candidates = [line for line in pack['scenes'][index]['lines'] if line != self.sample.text()]
            self.sample.setText(random.choice(candidates or pack['scenes'][index]['lines']))
        else:
            self.timing.clear()
            self.sample.setText('这个角色还没有可用台词，可导入对应的 .petdialogue 文件。')
        self.refresh_status()

    def refresh_status(self):
        runtime, config = self.pet.dialogue, self.pet.config
        if self.character_id != config.character:
            self.status.setText('当前未使用这个角色，自动场景只由正在使用的角色触发。')
            return
        if not config.dialogue_enabled or not runtime.pack:
            self.status.setText('当前未启用角色台词。')
            return
        idle = max(0, math.ceil((runtime.next_idle-runtime.elapsed)/60))
        care = max(0, math.ceil((3000-runtime.elapsed+runtime.last_care)/60))
        policy = '允许主动冒泡' if config.dialogue_proactive else '仅在气泡可见时展示'
        idle_status = f'普通碎碎念约还需 {idle} 分钟' if idle else '普通碎碎念已到候选时间'
        care_status = f'下一轮休息关心约还需 {care} 分钟。' if care else '休息关心已到候选时间，等待合适场景。'
        self.status.setText(f'{policy} · 已累计陪伴 {int(runtime.elapsed//60)} 分钟\n{idle_status}；' +
                            (care_status if config.dialogue_care_enabled else '休息关心已关闭。') +
                            '\n打开面板、全屏等状态会暂停计时与展示，关闭后继续，不再清零。')
        pack = self.pet.dialogue_library.packs.get(self.packs.currentData())
        index = self.scenes.currentData()
        if pack and index is not None:
            scene = pack['scenes'][index]
            timing = scene_timing(scene, pack, config)
            day = runtime.state.get('roles', {}).get(self.character_id, {}).get('days', {}).get(datetime.now().date().isoformat(), {})
            count = day.get(pack['id']+':'+scene.get('occurrenceGroup', scene['id']), 0)
            if count >= scene.get('maxPerDay', 100):
                timing += f' 今天已经展示 {count} 次，已达本场景当天上限。'
            self.timing.setText(timing)

    def preview_on_pet(self):
        error = self.pet.preview_dialogue(self.sample.text(), self.character_id)
        if error:
            self.feedback.setText(error)
        else:
            self.hide()

    def showEvent(self, event):
        self.refresh_status()
        self.status_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.status_timer.stop()
        super().hideEvent(event)

    def import_pack(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入角色台词包', '', '角色台词包 (*.petdialogue)')
        if path:
            try:
                previous = self.pet.dialogue_library.selected(self.character_id, self.pet.config.dialogue_packs)
                pack = self.pet.dialogue_library.install(path, self.character_id)
                self.pet.config.dialogue_packs.setdefault(self.character_id, previous['id'] if previous else '')
                self.refresh(pack['id'])
                self.feedback.setText('导入成功。点击“保存选择”后启用。')
            except (DialogueError, OSError) as error:
                self.feedback.setText(str(error))

    def activate(self):
        self.pet.config.dialogue_packs[self.character_id] = self.packs.currentData() or ''
        self.pet.refresh_dialogue()
        self.pet.write_config()
        self.feedback.setText('已保存，下次使用这个角色时继续沿用。')
