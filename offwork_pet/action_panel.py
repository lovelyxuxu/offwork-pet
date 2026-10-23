"""Persistent action shelf; every entry comes from the selected character package."""
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .ui_theme import card, label, scroll, style_window


class ActionPanel(QDialog):
    def __init__(self, pet):
        super().__init__(pet, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.pet = pet
        self.setWindowTitle('下班搭子 · 动作与互动')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(360)
        self.setMinimumHeight(330)
        self.resize(360, 400)
        style_window(self)
        self.setStyleSheet(self.styleSheet() + 'QFrame#panelSurface {border:1px solid #DDE1EB; border-radius:16px;} QPushButton#shelfClose {padding:0;font-size:22px;}')
        self.local_key = self.remote_key = None
        self.local_buttons, self.remote_buttons = [], []
        self.tracked_action = None
        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(200)
        self.playback_timer.timeout.connect(self.refresh_playback)
        self.setStyleSheet(self.styleSheet() + 'QPushButton[playState="playing"] {background:#ECE6FA;color:#6B55BF;border-color:#C7B9EA;} QPushButton[playState="queued"] {border-color:#B7ABD8;background:#F4F0FC;}')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        surface = QFrame()
        surface.setObjectName('panelSurface')
        outer.addWidget(surface)
        root = QVBoxLayout(surface)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        header = QHBoxLayout()
        self.title = label('陪它玩一会儿', 'sectionTitle')
        header.addWidget(self.title, 1)
        self.close_button = QPushButton('×')
        self.close_button.setObjectName('shelfClose')
        self.close_button.setAccessibleName('关闭动作面板')
        self.close_button.setProperty('variant', 'quiet')
        self.close_button.setFixedSize(30, 30)
        self.close_button.clicked.connect(self.hide)
        header.addWidget(self.close_button)
        root.addLayout(header)
        segments = QFrame()
        segments.setObjectName('segments')
        row = QHBoxLayout(segments)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        self.tabs = []
        for index, title in enumerate(('我的动作', '搭子互动')):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setProperty('segment', True)
            button.clicked.connect(lambda checked=False, i=index: self.select_tab(i))
            self.tabs.append(button)
            row.addWidget(button)
        root.addWidget(segments)
        self.context = label('', 'muted')
        root.addWidget(self.context)
        self.pages = QStackedWidget()
        self.grids = []
        for _ in range(2):
            host = QWidget()
            grid = QGridLayout(host)
            grid.setContentsMargins(0, 0, 3, 0)
            grid.setSpacing(8)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            grid.setAlignment(Qt.AlignmentFlag.AlignTop)
            self.grids.append(grid)
            self.pages.addWidget(scroll(host))
        root.addWidget(self.pages, 1)
        self.feedback = label('点击后面板保持打开，可以继续选其他动作。', 'muted')
        root.addWidget(self.feedback)
        play = QHBoxLayout()
        self.ball_button = QPushButton('玩小球')
        self.ball_button.setToolTip('拖动小球，松手让它滚动；右键小球收起。需要角色提供追球动作。')
        self.ball_button.clicked.connect(self.play_ball)
        self.ritual_button = QPushButton('下班彩排')
        self.ritual_button.setToolTip('提前看看角色迎接下班的反应，不改变下班时间。')
        self.ritual_button.clicked.connect(pet.preview_ritual)
        play.addWidget(self.ball_button)
        play.addWidget(self.ritual_button)
        self.explore_button = QPushButton('去逛逛')
        self.explore_button.clicked.connect(pet.explore_now)
        self.explore_button.setToolTip('观察附近可达的位置，移动过去，再休息一会儿。')
        play.addWidget(self.explore_button)
        root.addLayout(play)
        footer = QHBoxLayout()
        for text, callback in (('换角色', pet.open_packages), ('搭子', pet.open_buddy), ('专注', pet.open_focus), ('设置', pet.open_settings), ('···', self.more)):
            button = QPushButton(text)
            button.setProperty('variant', 'quiet')
            button.setStyleSheet('padding:8px 5px;')
            button.clicked.connect(callback)
            footer.addWidget(button)
        root.addLayout(footer)
        pet.buddy.changed.connect(self.refresh)
        pet.buddy.notice.connect(self.show_notice)
        self.select_tab(0)

    def more(self):
        self.pet.menu.popup(self.mapToGlobal(QPoint(0, self.height())))

    def select_tab(self, index):
        self.pages.setCurrentIndex(index)
        for i, tab in enumerate(self.tabs):
            tab.setChecked(i == index)
        self.feedback.setText('点击后面板保持打开，可以继续选其他动作。')
        self.refresh()

    def rebuild(self, grid, items, callback, empty):
        while grid.count():
            widget = grid.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        buttons = []
        for i, item in enumerate(sorted(items, key=lambda x: x.get('order', 0))):
            button = QPushButton(item['label'])
            button.setObjectName('actionTile')
            button.setToolTip(item['label'])
            button.setProperty('baseText', item['label'])
            operation = item.get('invoke', {})
            button.setProperty('actionId', operation.get('id') if operation.get('type') == 'action' else None)
            button.clicked.connect(lambda checked=False, item_id=item['id'], title=item['label']: callback(item_id, title))
            grid.addWidget(button, i // 2, i % 2)
            buttons.append(button)
        if not buttons:
            frame, layout = card(empty[0], empty[1])
            grid.addWidget(frame, 0, 0, 1, 2)
        return buttons

    def refresh(self):
        runtime, client = self.pet.runtime, self.pet.buddy
        ball, ritual = self.pet.refresh_play_controls()
        self.ball_button.setEnabled(ball)
        self.ritual_button.setEnabled(ritual)
        self.explore_button.setEnabled(self.pet.explore_action.isEnabled())
        local_key = runtime.revision if runtime else None
        if local_key != self.local_key or not self.grids[0].count():
            self.tracked_action = None
            self.local_key = local_key
            self.local_buttons = self.rebuild(self.grids[0], runtime.data.get('menu', []) if runtime else [],
                self.play_local, ('还没有可用动作', '先导入角色包，角色的动作会出现在这里。'))
        nickname = self.pet.companion_memory.entry(runtime.package.id)['nickname'] if runtime else ''
        self.title.setText(nickname or (runtime.package.name if runtime else '陪它玩一会儿'))
        self.refresh_playback()
        capabilities = (client.relation or {}).get('capabilities') or {}
        remote_key = ((client.relation or {}).get('id'), capabilities.get('revision'))
        if remote_key != self.remote_key or not self.grids[1].count():
            self.remote_key = remote_key
            self.remote_buttons = self.rebuild(self.grids[1], capabilities.get('interactions', []),
                self.play_remote, ('把陪伴送给搭子', '在下方“搭子设置”连接对方，互动动作会自动出现在这里。' if not client.relation
                                   else '对方上线并加载角色后，这里会显示可用互动。'))
        available = client.ready and bool(client.relation) and client.status == 'available'
        pending = bool(client.identity and client.identity.record.get('pending_unlink'))
        for button in self.remote_buttons:
            button.setEnabled(available and not pending)
        if self.pages.currentIndex() == 0:
            self.context.setText('只让自己的宠物表演 · 点击即可播放' if self.pet.config.interactive else '本地互动已关闭，可在设置中开启')
        elif not client.enabled:
            self.context.setText(client.connection_text if client.config.network_enabled else '联网未开启 · 本地动作照常可用')
        elif not client.ready:
            self.context.setText(client.connection_text)
        else:
            status = {'available': '可互动', 'paused': '暂停接收', 'offline': '离线'}.get(client.status, '状态未知')
            self.context.setText(f"{self.pet.config.buddy_note or '我的搭子'} · {status}" if client.relation else '尚未连接搭子')

    def play_local(self, identifier, title):
        if not self.pet.runtime or self.pet.runtime.revision != self.local_key:
            self.refresh()
            return
        result = self.pet.invoke_package_menu(identifier)
        item = next((item for item in self.pet.runtime.data.get('menu', []) if item['id'] == identifier), {})
        if result in ('started', 'queued') and item.get('invoke', {}).get('type') == 'action':
            self.tracked_action = item['invoke']['id']
        text = {'started': f'正在表演：{title}', 'queued': f'已排队：{title}', 'cooldown': '稍等一会儿，再试这个动作',
                'busy': '正在忙，等当前动作结束再试', 'paused': '本地互动已暂停', 'disabled': '本地互动已关闭',
                'unsupported': '这个动作已不可用', 'error': '动作暂时无法播放', 'condition': '这个动作暂时还不能触发'}
        self.feedback.setText(text.get(result, '已选择：' + title))
        self.refresh_playback()

    def play_ball(self):
        result = self.pet.toggle_ball()
        if result != 'started':
            self.feedback.setText({'space': '这儿有点挤，把宠物拖到更宽敞的位置再玩。',
                'condition': '角色当前状态暂时不能玩球。', 'disabled': '请先开启本地互动并关闭鼠标穿透。'
                }.get(result, '等当前动作结束后再玩小球。'))

    def refresh_playback(self):
        runtime = self.pet.runtime
        active = runtime.current if runtime else None
        queued = {request.id for request in runtime.queue} if runtime else set()
        for button in self.local_buttons:
            key = button.property('actionId')
            state = 'playing' if key and key == active else 'queued' if key in queued else ''
            enabled = bool(runtime and not runtime.stopped and not runtime.suspended and self.pet.config.interactive)
            button.setEnabled(enabled and state not in ('playing', 'queued'))
            if button.property('playState') != state:
                button.setProperty('playState', state)
                button.setText(('• ' if state == 'playing' else '⌛ ' if state == 'queued' else '') + button.property('baseText'))
                button.style().unpolish(button)
                button.style().polish(button)
                button.update()
        if self.tracked_action and self.tracked_action != active and self.tracked_action not in queued:
            self.tracked_action = None
            if self.pages.currentIndex() == 0:
                self.feedback.setText('可以继续选一个动作，或让它安静陪伴。')

    def showEvent(self, event):
        super().showEvent(event)
        self.playback_timer.start()

    def hideEvent(self, event):
        self.playback_timer.stop()
        super().hideEvent(event)

    def play_remote(self, identifier, title):
        capabilities = (self.pet.buddy.relation or {}).get('capabilities') or {}
        if ((self.pet.buddy.relation or {}).get('id'), capabilities.get('revision')) != self.remote_key:
            self.refresh()
            return
        self.pet.buddy.queue_action(identifier)

    def show_notice(self, message):
        if self.pages.currentIndex() == 1:
            self.feedback.setText(message)

    def place(self):
        from PySide6.QtWidgets import QApplication
        screen = QApplication.screenAt(self.pet.frameGeometry().center()) or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        height = min(400, rect.height() - 16)
        self.setFixedWidth(min(360, rect.width() - 16))
        self.setMinimumHeight(min(330, height))
        self.resize(min(360, rect.width() - 16), height)
        x = self.pet.x() + (self.pet.width() - self.width()) // 2
        y = self.pet.y() + self.pet.height() + 6
        if y + self.height() > rect.bottom() + 1:
            # Prefer a free side near the bottom, then the space above the pet.
            right = self.pet.x() + self.pet.width() + 8
            left = self.pet.x() - self.width() - 8
            if right + self.width() <= rect.right() + 1 or left >= rect.left():
                x = right if right + self.width() <= rect.right() + 1 else left
                y = self.pet.y() + self.pet.height() - self.height()
            else:
                y = self.pet.y() - self.height() - 6
        self.move(max(rect.left() + 8, min(x, rect.right() - self.width() - 7)),
                  max(rect.top() + 8, min(y, rect.bottom() - self.height() - 7)))

    def open(self, tab=0):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.pet.config.topmost)
        self.select_tab(tab)
        self.place()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self.hide()
        event.ignore()
