"""Native desktop host for independently installed declarative character packages."""
from dataclasses import replace
from collections import deque
from datetime import datetime
import os
from pathlib import Path
import sys
import shutil
from time import monotonic
from tempfile import TemporaryDirectory

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTime, QTimer, QLockFile
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QMenu, QMessageBox,
    QSlider, QSpinBox, QStyle, QSystemTrayIcon, QTimeEdit, QVBoxLayout, QWidget, QTabWidget, QPushButton,
)

from .core import Config, ReminderTracker, bubble_visible, countdown, data_dir, load_config, save_config
from .buddy import BuddyClient
from .buddy_ui import BuddyDialog
from .packages import PackageLibrary, PackageError
from .behavior import BehaviorRuntime
from .package_player import PackagePlayer, package_icon
from .package_ui import PackageDialog
from .action_panel import ActionPanel
from .ui_theme import STYLE, card, label as ui_label, scroll, style_window
from .update_ui import UpdateController
from .character_sound import CharacterSound
from .autonomy import AutonomyController
from .desktop_scene import DesktopObserver
from .toys import Ball, BallGame
from .toy_ui import BallWindow
from .companionship import CompanionMemory
from .focus import FocusSession
from .focus_ui import FocusDialog
from . import __version__

PALETTES = {"奶油橘": ("#F5C997", "#FFF0D9"), "薄荷绿": ("#A9D5BB", "#E8F6EC"), "云朵蓝": ("#B3CDEC", "#ECF3FF")}
BUBBLE_MODES = {"始终展示": "always", "鼠标悬停时展示": "hover", "点击宠物后展示": "click", "悬停或点击时展示": "hover_click", "始终隐藏": "hidden"}


def startup_enabled():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.QueryValueEx(key, "OffworkPet")
        return True
    except OSError:
        return False


def set_startup(enabled):
    if sys.platform != "win32":
        return
    import subprocess
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            if getattr(sys, "frozen", False):
                args = [sys.executable]
            else:
                executable = Path(sys.executable).with_name("pythonw.exe")
                args = [str(executable if executable.exists() else Path(sys.executable)), str(Path(__file__).resolve().parents[1] / "main.py")]
            winreg.SetValueEx(key, "OffworkPet", 0, winreg.REG_SZ, subprocess.list2cmdline(args))
        else:
            try:
                winreg.DeleteValue(key, "OffworkPet")
            except FileNotFoundError:
                pass


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"下班搭子 · 设置 · v{__version__}")
        self.setMinimumSize(540, 560)
        self.resize(570, 660)
        style_window(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(12)
        root.addWidget(ui_label('让下班，值得期待。', 'heading'))
        root.addWidget(ui_label('按你的节奏，安排这位桌面搭子。', 'subtitle'))
        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        forms = []
        for name, heading, subtitle in (('下班日程', '每天的小期待', '设置工作日与下班时间。'),
                ('外观气泡', '舒服地待在桌面', '调整角色、大小与倒计时的展示。'),
                ('偏好设置', '陪伴的方式，由你决定', '互动、提醒和桌面行为。')):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            surface, layout = card(heading, subtitle)
            form = QFormLayout()
            form.setSpacing(14)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            layout.addLayout(form)
            page_layout.addWidget(surface)
            page_layout.addStretch()
            tabs.addTab(scroll(page), name)
            forms.append(form)
        form = forms[0]
        self.clock = QTimeEdit(QTime.fromString(config.off_time, "HH:mm"))
        self.clock.setDisplayFormat("HH:mm")
        form.addRow("平时几点下班", self.clock)
        days = QHBoxLayout()
        self.days = []
        for i, label in enumerate("一二三四五六日"):
            check = QCheckBox(label)
            check.setChecked(i in config.weekdays)
            self.days.append(check)
            days.addWidget(check)
        form.addRow("工作日", days)
        self.today = QCheckBox("今天使用临时时间（含非工作日）")
        self.today.setChecked(config.override_date == datetime.now().date().isoformat())
        self.today_clock = QTimeEdit(QTime.fromString(config.override_time, "HH:mm"))
        self.today_clock.setDisplayFormat("HH:mm")
        self.today_clock.setEnabled(self.today.isChecked())
        self.today.toggled.connect(self.today_clock.setEnabled)
        form.addRow(self.today)
        form.addRow("今天几点下班", self.today_clock)
        form = forms[1]
        self.character = QComboBox()
        library = getattr(parent, 'library', None)
        if library:
            for item in library.entries():
                self.character.addItem(item['displayName'], item['id'])
        self.character.addItem('暂不使用角色', '')
        self.character.setCurrentIndex(self.character.findData(config.character))
        form.addRow('宠物形象', self.character)
        self.color = QComboBox()
        self.color.addItems(PALETTES)
        self.color.setCurrentText(config.color)
        form.addRow("气泡主题", self.color)
        self.scale = QSlider(Qt.Orientation.Horizontal)
        self.scale.setRange(50, 200)
        self.scale.setValue(config.scale)
        self.size_label = QLabel(f"{config.scale}%")
        self.scale.valueChanged.connect(lambda value: self.size_label.setText(f"{value}%"))
        size_row = QHBoxLayout()
        size_row.addWidget(self.scale)
        size_row.addWidget(self.size_label)
        form.addRow("宠物大小", size_row)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(20, 100)
        self.opacity.setValue(config.opacity)
        self.opacity_label = QLabel(f"{config.opacity}%")
        self.opacity.valueChanged.connect(lambda value: self.opacity_label.setText(f"{value}%"))
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity)
        opacity_row.addWidget(self.opacity_label)
        form.addRow("不透明度", opacity_row)
        self.bubble_mode = QComboBox()
        for label, mode in BUBBLE_MODES.items():
            self.bubble_mode.addItem(label, mode)
        self.bubble_mode.setCurrentIndex(self.bubble_mode.findData(config.bubble_mode))
        form.addRow("气泡展示方式", self.bubble_mode)
        self.bubble_seconds = QSpinBox()
        self.bubble_seconds.setRange(2, 30)
        self.bubble_seconds.setSuffix(" 秒")
        self.bubble_seconds.setValue(config.bubble_seconds)
        self.bubble_mode.currentIndexChanged.connect(lambda _: self.bubble_seconds.setEnabled(self.bubble_mode.currentData() in ("click", "hover_click")))
        self.bubble_seconds.setEnabled(config.bubble_mode in ("click", "hover_click"))
        form.addRow("点击后停留", self.bubble_seconds)
        form = forms[2]
        self.autonomous = QCheckBox('让宠物自己活动')
        self.autonomous.setChecked(config.autonomous_enabled)
        form.addRow(self.autonomous)
        self.activity_min = QSpinBox()
        self.activity_max = QSpinBox()
        for control, value in ((self.activity_min, config.activity_min_seconds), (self.activity_max, config.activity_max_seconds)):
            control.setRange(5, 3600)
            control.setSuffix(' 秒')
            control.setValue(value)
        self.activity_min.valueChanged.connect(lambda value: self.activity_max.setMinimum(value))
        self.activity_max.setMinimum(self.activity_min.value())
        interval = QHBoxLayout()
        interval.addWidget(self.activity_min, 1)
        interval_label = QLabel('到')
        interval_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        interval_label.setFixedWidth(24)
        interval.addWidget(interval_label)
        interval.addWidget(self.activity_max, 1)
        form.addRow('随机活动间隔', interval)
        presets = QComboBox()
        for title, value in [('自定义', None), ('安静 · 60–120 秒', (60, 120)), ('自然 · 20–60 秒', (20, 60)), ('活泼 · 8–20 秒', (8, 20))]:
            presets.addItem(title, value)
        def preset_changed():
            value = presets.currentData()
            if value:
                self.activity_min.setValue(value[0])
                self.activity_max.setValue(value[1])
        presets.currentIndexChanged.connect(preset_changed)
        form.addRow('活动节奏', presets)
        self.roaming = QComboBox()
        for title, mode in [('仅原地动作', 'off'), ('当前位置附近', 'stay'), ('桌面与窗口边缘', 'free')]:
            self.roaming.addItem(title, mode)
        self.roaming.setCurrentIndex(self.roaming.findData(config.roaming_mode))
        form.addRow('自主活动范围', self.roaming)
        for control in (self.activity_min, self.activity_max, self.roaming):
            control.setEnabled(config.autonomous_enabled)
            self.autonomous.toggled.connect(control.setEnabled)
        form.addRow(ui_label('动作由角色包提供；操作、打开面板或全屏使用其他应用时暂歇。', 'muted'))
        self.checks = {}
        for key, label in (("exploration_enabled", "自由活动时主动寻找窗口落脚点（需角色支持）"),
                          ("internal_surfaces", "识别前台窗口内的工具栏和面板边界"),
                          ("personality_enabled", "记住互动偏好，让自主动作逐渐贴近你的习惯"),
                          ("topmost", "始终置顶"), ("interactive", "启用角色包的本地互动"), ("pointer_look_enabled", "空闲时看向鼠标（需角色包支持）"), ("reminders", "提前 30 分钟、10 分钟及到点提醒"), ("rituals_enabled", "下班前 10 分钟及到点时，让角色陪你迎接下班"), ("sound", "播放本地提示和角色声音"), ("click_through", "鼠标穿透（通过托盘菜单恢复）")):
            check = QCheckBox(label)
            check.setChecked(getattr(config, key))
            self.checks[key] = check
            form.addRow(check)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.checks["click_through"].setChecked(False)
            self.checks["click_through"].setEnabled(False)
        self.startup = QCheckBox("登录 Windows 时自动启动")
        self.startup.setChecked(startup_enabled())
        form.addRow(self.startup)
        self.update_check = QCheckBox('自动检查软件更新（启动时及每 4 小时）')
        self.update_check.setChecked(config.auto_update_check)
        form.addRow(self.update_check)
        update_button = QPushButton('检查更新…')
        update_button.setEnabled(bool(parent and hasattr(parent, 'open_updates')))
        if parent and hasattr(parent, 'open_updates'):
            update_button.clicked.connect(parent.open_updates)
        form.addRow('软件更新', update_button)
        hint = ui_label('右键打开动作面板 · Alt + 按住鼠标移动进行抚摸\nCtrl + 滚轮调大小 · Ctrl + Shift + 滚轮调透明度', 'muted')
        root.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty('variant', 'primary')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def validate_and_accept(self):
        if not any(day.isChecked() for day in self.days):
            QMessageBox.warning(self, "选择工作日", "请至少选择一个工作日。")
            return
        self.accept()

    def result_config(self, old):
        return replace(old, off_time=self.clock.time().toString("HH:mm"),
                       weekdays=[i for i, day in enumerate(self.days) if day.isChecked()],
                       override_date=datetime.now().date().isoformat() if self.today.isChecked() else "",
                       override_time=self.today_clock.time().toString("HH:mm"), color=self.color.currentText(),
                       character=self.character.currentData(), scale=self.scale.value(), opacity=self.opacity.value(),
                       bubble_mode=self.bubble_mode.currentData(), bubble_seconds=self.bubble_seconds.value(), auto_update_check=self.update_check.isChecked(),
                       autonomous_enabled=self.autonomous.isChecked(), activity_min_seconds=self.activity_min.value(),
                       activity_max_seconds=self.activity_max.value(), roaming_mode=self.roaming.currentData(),
                       **{key: check.isChecked() for key, check in self.checks.items()})


class Pet(QWidget):
    def __init__(self, config, persist=True, *, relay_endpoint=None, package_directory=None, seed_starters=True):
        super().__init__()
        self.config = config
        self.persist = persist
        self.companion_memory = CompanionMemory(data_dir()/'companions.json' if persist else None)
        self.focus = FocusSession()
        self.focus_dialog = self.creator_dialog = None
        self.gift = None
        self.gift_until = 0
        self._temporary_library = TemporaryDirectory() if not persist and package_directory is None else None
        library_root = package_directory or (Path(self._temporary_library.name) if self._temporary_library else data_dir() / 'characters')
        self.library = PackageLibrary(library_root)
        if seed_starters:
            self.library.seed(Path(__file__).resolve().parent / 'assets' / 'starter-packs')
        self.runtime = self.player = None
        self.autonomy = None
        self.ball_window = self.ball_game = None
        self.desktop_observer = DesktopObserver()
        self.desktop_scene = None
        self.scene_screen = None
        self.scene_checked = 0
        self.autonomous_position = None
        self.package_dialog = None
        self.action_panel = None
        self.last_character_error = ''
        self.last_click_point = QPointF(140, 180)
        self.stroke_active = False
        self.stroke_distance = self.stroke_turns = 0
        self.stroke_previous = QPointF()
        self.stroke_direction = QPointF()
        self.stroke_last_emit = 0
        self.held = False
        self.hold_timer = QTimer(self)
        self.hold_timer.setSingleShot(True)
        self.hold_timer.timeout.connect(self.hold_character)
        self.phase = 0
        self.drag = None
        self.press_position = None
        self.dragging = False
        self.hovered = False
        self.clicked_until = 0.0
        self.reaction_until = 0.0
        self.reaction_index = 0
        self.reaction_message = ""
        self.gaze = QPointF(0, 0)
        self.last_activity = monotonic()
        self.double_click = False
        self.dialog = None
        self.buddy_dialog = None
        self.remote_message = ''
        self.remote_plays = deque(maxlen=3)
        self.current_relation_id = ''
        self.reminders = ReminderTracker()
        self.snapshot = countdown(datetime.now(), config)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(f"下班搭子 · v{__version__}")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.tray = QSystemTrayIcon(self)
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon)
        self.tray.setIcon(icon)
        self.menu = QMenu()
        self.menu.setStyleSheet(STYLE)
        self.menu.addAction('动作与互动…', self.open_actions)
        self.menu.addAction("显示宠物 / 恢复位置", self.restore)
        self.menu.addAction("设置…", self.open_settings)
        self.menu.addAction('检查更新…', self.open_updates)
        self.menu.addAction('角色管理…', self.open_packages)
        self.menu.addAction('制作角色…', self.open_creator)
        self.menu.addAction('专注陪伴…', self.open_focus)
        self.explore_action = self.menu.addAction('去附近探索', self.explore_now)
        self.ball_action = self.menu.addAction('玩小球', self.toggle_ball)
        self.ritual_action = self.menu.addAction('预览下班庆祝', self.preview_ritual)
        self.autonomous_action = self.menu.addAction('自主活动')
        self.autonomous_action.setCheckable(True)
        self.autonomous_action.triggered.connect(self.set_autonomous)
        self.stay_action = self.menu.addAction('在这里待着')
        self.stay_action.setCheckable(True)
        self.stay_action.triggered.connect(self.set_stay)
        # Kept as a compatibility object; actions are presented in the persistent shelf.
        self.character_menu = QMenu('角色动作', self)
        self.menu.addAction('搭子…', self.open_buddy)
        self.pause_action = self.menu.addAction('暂停接收远程互动')
        self.pause_action.setCheckable(True)
        self.pause_action.setChecked(config.paused)
        self.pause_action.triggered.connect(self.set_paused)
        self.pass_action = self.menu.addAction("鼠标穿透")
        self.pass_action.setCheckable(True)
        self.pass_action.triggered.connect(self.toggle_passthrough)
        self.menu.addAction("隐藏宠物", self.hide)
        self.menu.addSeparator()
        self.menu.addAction("退出下班搭子", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        else:
            self.config.click_through = False
        self.apply_config()
        self.restore(initial=True)
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self.pet_cat)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.write_config)
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(1000)
        self.animation = QTimer(self)
        self.animation.timeout.connect(self.animate)
        self.animation.start(50)
        self.buddy = BuddyClient(self.config, self, persist=persist, endpoint=relay_endpoint)
        self.buddy.capabilities_provider = self.character_capabilities
        self.buddy.changed.connect(self.buddy_changed)
        self.buddy.incoming.connect(self.receive_remote)
        self.updates = UpdateController(self)
        if self.config.network_enabled and persist:
            QTimer.singleShot(0, self.buddy.start)

    def open_buddy(self):
        self.interrupt_autonomy()
        if self.buddy_dialog is None:
            self.buddy_dialog = BuddyDialog(self)
        self.buddy_dialog.show()
        self.buddy_dialog.raise_()
        self.buddy_dialog.activateWindow()

    def open_actions(self, tab=0):
        self.interrupt_autonomy()
        if self.action_panel is None:
            self.action_panel = ActionPanel(self)
        self.action_panel.open(tab if type(tab) is int else 0)

    def open_updates(self):
        self.updates.check(manual=True)

    def open_packages(self):
        self.interrupt_autonomy()
        if self.package_dialog is None:
            self.package_dialog = PackageDialog(self)
        self.package_dialog.refresh()
        self.package_dialog.show()
        self.package_dialog.raise_()

    def character_capabilities(self):
        return self.runtime.package.snapshot(self.runtime.revision) if self.runtime and not self.runtime.stopped else None

    def import_package(self, archive):
        previous = dict(self.library.index['packages'])
        package = self.library.install(archive)
        try:
            self.select_package(package.id)
        except Exception:
            self.library.index['packages'] = previous
            self.library._save()
            if not any(item['directory'] == package.root.relative_to(self.library.root).as_posix() for item in previous.values()):
                if package.root.resolve().is_relative_to(self.library.root.resolve()):
                    shutil.rmtree(package.root)
            raise
        return package

    def select_package(self, identifier):
        new_runtime = new_player = None
        if identifier:
            package = self.library.load(identifier)
            new_runtime = BehaviorRuntime(package)
            new_player = PackagePlayer(new_runtime)
            # Prepare the first image before releasing the previous usable character.
            new_player.cache.image(package, new_runtime.frame()['file'])
        if hasattr(self, 'hold_timer'):
            self.hold_timer.stop()
        self.stroke_active = False
        self.drag = None
        self.press_position = None
        self.dragging = False
        if hasattr(self, 'click_timer'):
            self.click_timer.stop()
        if self.player:
            self.stop_ball()
            self.player.close()
        if hasattr(self, 'character_sound'):
            self.character_sound.stop()
        self.runtime, self.player = new_runtime, new_player
        if new_runtime:
            new_runtime.preferences = (self.companion_memory.entry(identifier)['favorites'] if self.config.personality_enabled else {})
        self.gift = None
        self.autonomy = AutonomyController(new_runtime) if new_runtime else None
        self.autonomous_position = None
        self.config.character = identifier or ''
        self.reaction_until = 0
        self.remote_message = ''
        self.last_character_error = ''
        self.character_menu.clear()
        if self.runtime:
            for item in sorted(self.runtime.data.get('menu', []), key=lambda entry: entry.get('order', 0)):
                action = self.character_menu.addAction(item['label'])
                action.triggered.connect(lambda checked=False, key=item['id']: self.invoke_package_menu(key))
        self.character_menu.setEnabled(bool(self.runtime and self.runtime.data.get('menu')))
        self.refresh_play_controls()
        icon = QIcon(package_icon(self.runtime.package)) if self.runtime else self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon)
        self.tray.setIcon(icon)
        if hasattr(self, 'buddy'):
            self.buddy.received.clear()
            self.buddy.update_config(self.config)
            self.buddy.changed.emit()
        if self.action_panel:
            self.action_panel.refresh()
        self.write_config()
        self.update()

    def runtime_call(self, callback):
        if not self.runtime:
            return 'unsupported'
        try:
            return callback()
        except (PackageError, OSError, KeyError, ValueError) as error:
            self.runtime.fail(error)
            self.last_character_error = str(error)
            if self.runtime.stopped and hasattr(self, 'buddy') and self.buddy.ready:
                self.buddy.profile()
            return 'error'

    def package_event(self, name, point=None, source='local'):
        if not self.runtime or not self.config.interactive:
            return 'disabled'
        if name != 'pointer.leave':
            self.interrupt_autonomy()
        position = self.player.point(point) if point is not None else None
        return self.runtime_call(lambda: self.runtime.event(name, position, {'source': source}))

    def invoke_package_menu(self, identifier):
        if self.runtime and self.config.interactive:
            self.interrupt_autonomy()
            self.runtime.activity()
            return self.runtime_call(lambda: self.runtime.entry('menu', identifier, {'source': 'local'}))
        return 'disabled'

    def hold_character(self):
        if self.drag is not None and not self.dragging and not self.stroke_active:
            self.held = True
            self.package_event('pointer.hold', self.last_click_point)

    def buddy_changed(self):
        relation_id = self.buddy.relation['id'] if self.buddy.relation else ''
        if relation_id != self.current_relation_id or not self.buddy.ready:
            self.clear_remote()
            self.current_relation_id = relation_id

    def set_paused(self, paused):
        self.config.paused = paused
        self.pause_action.setChecked(paused)
        if paused:
            self.clear_remote()
        self.buddy.update_config(self.config)
        self.write_config()
        self.buddy.changed.emit()

    def clear_remote(self):
        self.remote_message = ''
        self.gift = None
        if self.runtime:
            self.runtime.cancel('remote')
        if hasattr(self, 'character_sound'):
            self.character_sound.stop()
        self.update()

    def receive_remote(self, msg):
        import time
        valid = (self.buddy.ready and self.buddy.relation and not self.config.paused and not self.focus.active and self.isVisible()
                 and not self.dialog and msg.get('relation_id') == self.buddy.relation['id']
                 and self.runtime is not None and self.config.interactive and not self.dragging
                 and msg.get('capabilityRevision') == self.runtime.revision
                 and isinstance(msg.get('interactionId'), str) and isinstance(msg.get('action_id'), str)
                 and type(msg.get('expires_at')) in (int, float)
                 and 0 < msg['expires_at'] - time.time() - self.buddy.clock_offset <= 6)
        if not valid:
            self.buddy.acknowledge(msg, False)
            return
        key = (msg['relation_id'], msg['action_id'])
        if key in self.buddy.received:
            self.buddy.acknowledge(msg, True)
            return
        if len(self.remote_plays) == 3 and monotonic() - self.remote_plays[0] < 8:
            self.buddy.acknowledge(msg, False)
            return
        expires = monotonic() + msg['expires_at'] - time.time() - self.buddy.clock_offset
        self.interrupt_autonomy()
        result = self.runtime_call(lambda: self.runtime.entry('interactions', msg['interactionId'],
            {'source': 'remote', 'request': msg['action_id'], 'relation': msg['relation_id']}, expires))
        accepted = result in ('started', 'queued')
        if accepted:
            self.buddy.received[key] = monotonic() + 30
            self.remote_plays.append(monotonic())
        self.buddy.acknowledge(msg, accepted, result)

    def advance_remote(self):
        if self.runtime:
            self.runtime_call(self.runtime.tick)
            while self.runtime.effects:
                action_id, context = self.runtime.effects.popleft()
                action = self.runtime.data['actions'][action_id]
                if self.config.personality_enabled and context.get('source') in ('local', 'remote'):
                    self.companion_memory.record(self.runtime.package.id, action_id)
                    self.save_timer.start(1000)
                if context.get('source') == 'remote' and action.get('gift'):
                    self.gift = action['gift']
                    self.gift_until = monotonic()+self.gift.get('durationMs', 15000)/1000
                self.reaction_message = action.get('text', '')
                if context.get('source') == 'ritual':
                    self.reaction_until = monotonic()+self.runtime.duration(action_id)
                if context.get('source') == 'remote':
                    item = next((item for item in self.runtime.data.get('interactions', [])
                                 if item['invoke'].get('id') == action_id), {})
                    self.remote_message = action.get('text', item.get('label', ''))
                if action.get('sound') and (self.config.remote_sound if context.get('source') == 'remote' else self.config.sound):
                    if not hasattr(self, 'character_sound'):
                        self.character_sound = CharacterSound()
                    self.character_sound.play(self.runtime.package.asset(action['sound']))

    def hideEvent(self, event):
        self.interrupt_autonomy()
        self.gift = None
        self.desktop_observer.close()
        if self.action_panel:
            self.action_panel.hide()
        if self.runtime:
            self.runtime.suspend(True)
        self.hold_timer.stop()
        if hasattr(self, 'click_timer'):
            self.click_timer.stop()
        self.stroke_active = False
        self.drag = self.press_position = None
        self.dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if hasattr(self, 'buddy'):
            self.clear_remote()
            self.buddy.visible = False
            if self.buddy.ready:
                self.buddy.profile()
        super().hideEvent(event)

    def focusOutEvent(self, event):
        self.hold_timer.stop()
        if self.dragging and self.runtime:
            self.runtime.cancel('drag')
        if self.stroke_active:
            self.package_event('gesture.stroke.cancel', self.last_click_point)
        self.stroke_active = False
        self.drag = None
        self.press_position = None
        self.dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().focusOutEvent(event)

    def showEvent(self, event):
        if self.runtime:
            self.runtime.suspend(not self.config.interactive)
        if hasattr(self, 'buddy'):
            self.buddy.visible = True
            if self.buddy.ready:
                self.buddy.profile()
        super().showEvent(event)

    def write_config(self):
        if self.persist:
            try:
                save_config(self.config)
                self.companion_memory.save()
            except OSError as error:
                QMessageBox.warning(self, "保存失败", str(error))

    def apply_config(self):
        self.desktop_observer.internal_enabled = self.config.internal_surfaces
        current_id = self.runtime.package.id if self.runtime else ''
        if current_id != self.config.character:
            try:
                self.select_package(self.config.character)
            except (PackageError, OSError) as error:
                self.last_character_error = str(error)
                self.config.character = current_id
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.config.topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if self.config.click_through:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setFixedSize(round(280 * self.config.scale / 100), round(280 * self.config.scale / 100))
        self.setWindowOpacity(self.config.opacity / 100)
        icon = QIcon(package_icon(self.runtime.package)) if self.runtime else self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon)
        self.tray.setIcon(icon)
        if hasattr(self, 'buddy'):
            self.buddy.update_config(self.config)
        if hasattr(self, 'updates'):
            self.updates.configure_automatic()
        self.hovered = False
        self.clicked_until = 0.0
        self.pass_action.setChecked(self.config.click_through)
        self.autonomous_action.setChecked(self.config.autonomous_enabled)
        self.stay_action.setChecked(self.config.roaming_mode == 'stay')
        self.interrupt_autonomy(detach=True)
        self.refresh_play_controls()
        if self.runtime:
            self.runtime.preferences = (self.companion_memory.entry(self.runtime.package.id)['favorites'] if self.config.personality_enabled else {})
        self.snapshot = countdown(datetime.now(), self.config)
        self.tick()
        self.show()
        if self.action_panel:
            self.action_panel.refresh()

    def show_bubble(self, now=None):
        ritual = (self.runtime and self.runtime.current and self.runtime.context.get('source') == 'ritual'
                  and self.config.interactive and self.config.bubble_mode != 'hidden')
        return bool(ritual or bubble_visible(self.config.bubble_mode, self.hovered, self.clicked_until,
                              monotonic() if now is None else now))

    def cat_contains(self, point):
        return bool(self.player and self.runtime_call(lambda: self.player.contains(QPointF(point) / (self.config.scale / 100))) is True)

    def pet_cat(self):
        now = monotonic()
        self.clicked_until = now + self.config.bubble_seconds
        if self.config.interactive:
            self.package_event('pointer.click', self.last_click_point)
            self.reaction_until = now + (self.runtime.duration(self.runtime.current) if self.runtime and self.runtime.current else 0)
            self.reaction_index += 1
        self.last_activity = now
        self.update()

    def restore(self, initial=False):
        self.interrupt_autonomy(detach=True)
        if not initial:
            self.config.click_through = False
            self.apply_config()
        screens = QApplication.screens()
        point = QPoint(self.config.x or 0, self.config.y or 0)
        saved = self.config.x is not None and self.config.y is not None
        # Keep the entire pet inside one available desktop after display changes.
        screen = next((s for s in screens if s.availableGeometry().contains(point)), QApplication.primaryScreen())
        rect = screen.availableGeometry()
        if initial and saved and rect.contains(point):
            x, y = point.x(), point.y()
        else:
            x, y = rect.right() - self.width() - 36, rect.bottom() - self.height() - 24
        self.move(max(rect.left(), min(x, rect.right() - self.width() + 1)),
                  max(rect.top(), min(y, rect.bottom() - self.height() + 1)))
        self.show()

    def toggle_passthrough(self, checked):
        self.config.click_through = checked
        self.apply_config()
        self.write_config()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_settings()

    def open_settings(self):
        self.interrupt_autonomy()
        if self.dialog:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        original = replace(self.config)
        original_position = self.pos()
        pending_save = self.save_timer.isActive()
        self.save_timer.stop()
        self.click_timer.stop()
        self.dialog = SettingsDialog(self.config, self)
        self.clear_remote()
        self.dialog.scale.valueChanged.connect(self.resize_pet)
        self.dialog.opacity.valueChanged.connect(lambda value: self.setWindowOpacity(value / 100))
        accepted = False
        try:
            if self.dialog.exec() == QDialog.DialogCode.Accepted:
                accepted = True
                new_config = self.dialog.result_config(self.config)
                try:
                    if self.dialog.startup.isChecked() != startup_enabled():
                        set_startup(self.dialog.startup.isChecked())
                except OSError as error:
                    QMessageBox.warning(self.dialog, "开机启动设置失败", str(error))
                self.config = new_config
                self.apply_config()
                self.keep_on_screen()
                self.config.x, self.config.y = self.x(), self.y()
                self.write_config()
        finally:
            if not accepted:
                self.config = original
                self.apply_config()
                self.move(original_position)
                if pending_save:
                    self.save_timer.start(350)
            self.dialog.deleteLater()
            self.dialog = None

    def tick(self):
        if self.focus.tick():
            self.focus_changed()
            self.tray.showMessage('专注完成', '这一段辛苦了，起身伸个懒腰吧。', QSystemTrayIcon.MessageIcon.Information, 6000)
            self.play_ritual('focus.finished', preview=True)
        self.snapshot = countdown(datetime.now(), self.config)
        crossed = self.reminders.update(self.snapshot)
        self.tray.setToolTip(f"下班搭子 · {self.snapshot.display}")
        if crossed and self.config.reminders:
            threshold = crossed[0]
            message = "今天辛苦了，收工回家！" if threshold == 0 else f"还有 {threshold // 60} 分钟就下班啦。"
            self.tray.showMessage("下班搭子", message, QSystemTrayIcon.MessageIcon.Information, 6000)
            if self.config.sound:
                QApplication.beep()
        if crossed and crossed[0] in (600, 0) and self.config.rituals_enabled:
            self.play_ritual('schedule.offwork' if crossed[0] == 0 else 'schedule.soon')
        self.update()

    def animate(self):
        if self.isVisible():
            self.phase += 1
            if self.runtime:
                self.runtime.suspend(not self.config.interactive or bool(self.dialog))
            self.advance_remote()
            self.advance_ball()
            self.advance_autonomy()
            now = monotonic()
            local = self.mapFromGlobal(QCursor.pos())
            if self.runtime and self.player:
                position = self.player.point(QPointF(local) / (self.config.scale / 100))
                self.runtime_call(lambda: self.runtime.look_at(position,
                    enabled=self.config.pointer_look_enabled and self.config.interactive and not self.dialog and self.press_position is None
                    and not self.dragging and not self.stroke_active))
            over_cat = self.cat_contains(local)
            over_bubble = self.show_bubble(now) and QRectF(20, 8, 240, 98).contains(QPointF(local) / (self.config.scale / 100))
            entered = over_cat and not self.hovered
            left = self.hovered and not (over_cat or over_bubble)
            self.hovered = over_cat or over_bubble
            if entered:
                self.last_activity = now
                self.package_event('pointer.enter', QPointF(local) / (self.config.scale / 100))
            elif left:
                self.package_event('pointer.leave')
            target_x = max(-1.0, min(1.0, (local.x() / (self.config.scale / 100) - 141) / 240))
            target_y = max(-1.0, min(1.0, (local.y() / (self.config.scale / 100) - 178) / 180))
            self.gaze += (QPointF(target_x, target_y) - self.gaze) * 0.18
            self.update()

    def interrupt_autonomy(self, detach=False):
        self.stop_ball()
        if self.autonomy:
            self.autonomy.interrupt(self.config, detach=detach)
        self.autonomous_position = None

    def refresh_play_controls(self):
        enabled = bool(self.runtime and not self.runtime.stopped and self.config.interactive and not self.focus.active)
        ball = bool(enabled and self.runtime.data.get('toys', {}).get('ball') and not self.config.click_through)
        ritual = bool(enabled and any(rule['event'] == 'schedule.offwork' for rule in self.runtime.data.get('rules', [])))
        self.ball_action.setEnabled(ball)
        self.ball_action.setText('收起小球' if self.ball_game else '玩小球')
        self.ritual_action.setEnabled(ritual)
        self.explore_action.setEnabled(bool(enabled and self.autonomy and self.autonomy.explorer.spec
                                           and self.config.autonomous_enabled and self.config.roaming_mode == 'free'
                                           and self.config.exploration_enabled))
        return ball, ritual

    def open_focus(self):
        self.interrupt_autonomy()
        if self.focus_dialog is None:
            self.focus_dialog = FocusDialog(self)
        self.focus_dialog.show()
        self.focus_dialog.raise_()

    def focus_changed(self):
        self.interrupt_autonomy()
        if self.focus.active:
            self.clear_remote()
            self.desktop_observer.close()
        self.buddy.focus_paused = self.focus.active
        if self.buddy.ready:
            self.buddy.profile()
        self.refresh_play_controls()
        if self.action_panel:
            self.action_panel.refresh()
        self.update()

    def open_creator(self):
        self.interrupt_autonomy()
        from .creator_ui import CreatorDialog
        if self.creator_dialog is None:
            self.creator_dialog = CreatorDialog(self)
        self.creator_dialog.show()
        self.creator_dialog.raise_()

    def explore_now(self, checked=False):
        self.refresh_play_controls()
        if not self.explore_action.isEnabled():
            return
        self.interrupt_autonomy(detach=True)
        if self.action_panel:
            self.action_panel.hide()
        self.menu.hide()
        self.advance_autonomy()
        self.autonomy.explorer.next_due = 0

    def play_ritual(self, event, preview=False):
        if (not self.runtime or not self.config.interactive or not self.isVisible() or self.focus.active
                or self.runtime.stopped or self.runtime.suspended):
            return 'disabled'
        if not any(rule['event'] == event for rule in self.runtime.data.get('rules', [])):
            return 'unsupported'
        if (self.press_position is not None or self.dragging or self.stroke_active or self.dialog
                or QApplication.activeModalWidget() is not None):
            return 'busy'
        if not preview and (self.ball_game or self.menu.isVisible() or any(
                panel is not None and panel.isVisible() for panel in (self.action_panel, self.package_dialog, self.buddy_dialog, self.focus_dialog, self.creator_dialog))):
            return 'busy'
        if self.runtime.current and self.runtime.context.get('source') != 'autonomous':
            return 'busy'
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if self.desktop_observer.snapshot(screen).fullscreen:
            return 'busy'
        self.interrupt_autonomy()
        return self.runtime_call(lambda: self.runtime.event(event, context={'source': 'ritual'}))

    def preview_ritual(self, checked=False):
        result = self.play_ritual('schedule.offwork', preview=True)
        if self.action_panel:
            self.action_panel.feedback.setText('今天辛苦啦，提前彩排一下！' if result == 'started' else '等当前动作结束后再试试。')
        return result

    def stop_ball(self):
        if self.ball_game:
            self.ball_game.stop()
            self.ball_game = None
        if self.ball_window:
            window, self.ball_window = self.ball_window, None
            window.hide()
            window.deleteLater()
        if hasattr(self, 'ball_action'):
            self.ball_action.setText('玩小球')

    def toggle_ball(self, checked=False):
        if self.ball_game:
            self.stop_ball()
            return 'removed'
        if not self.refresh_play_controls()[0] or not self.isVisible() or self.runtime.suspended:
            return 'disabled'
        if self.runtime.state not in self.runtime.data['toys']['ball']['states']:
            return 'condition'
        if self.runtime.current and self.runtime.context.get('source') != 'autonomous':
            return 'busy'
        if self.dragging or self.stroke_active or self.press_position is not None or self.dialog:
            return 'busy'
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        scene = self.desktop_observer.snapshot(screen)
        if scene.fullscreen:
            return 'busy'
        scale = self.config.scale/100
        foot = self.player.transform.map(QPointF(*self.runtime.package.manifest['canvas']['anchor']))*scale
        floor = self.y()+foot.y()
        left, right = scene.bounds.left, scene.bounds.right-self.width()
        support = next((surface for surface in scene.surfaces if abs(surface.y-floor) < 24*scale
                        and surface.left <= self.x()+foot.x() <= surface.right), None)
        if support:
            left = max(left, support.left+60*scale-foot.x())
            right = min(right, support.right-60*scale-foot.x())
        if right-left < 140*scale or not left <= self.x() <= right:
            return 'space'
        self.interrupt_autonomy(detach=True)
        if self.action_panel:
            self.action_panel.hide()
        self.menu.hide()
        direction = 1 if self.x() < (left+right)/2 else -1
        x = max(left+foot.x(), min(right+foot.x(), self.x()+foot.x()+direction*160*scale))
        ball = Ball(x, left+foot.x(), right+foot.x())
        self.ball_game = BallGame(self.runtime, ball, (left, right), foot.x(), scale)
        self.ball_position = float(self.x())
        self.ball_screen = screen.name()
        self.ball_bounds = scene.bounds
        self.ball_support = support
        self.ball_checked = monotonic()
        self.ball_window = BallWindow(self.ball_game, floor, self)
        self.ball_window.removed.connect(self.stop_ball)
        self.ball_window.show()
        self.ball_action.setText('收起小球')
        return 'started'

    def advance_ball(self):
        if not self.ball_game:
            return
        if (not self.config.interactive or self.config.click_through or self.runtime.stopped
                or self.runtime.suspended or self.dragging or self.stroke_active or self.dialog
                or QApplication.activeModalWidget() is not None
                or any(panel is not None and panel.isVisible() for panel in (self.action_panel, self.package_dialog, self.buddy_dialog, self.focus_dialog, self.creator_dialog))):
            self.stop_ball()
            return
        now = monotonic()
        if now-self.ball_checked >= .5:
            screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
            scene = self.desktop_observer.snapshot(screen)
            if (screen.name() != self.ball_screen or scene.bounds != self.ball_bounds or scene.fullscreen
                    or self.ball_support is not None and self.ball_support not in scene.surfaces):
                self.stop_ball()
                return
            self.ball_checked = now
        position = self.runtime_call(lambda: self.ball_game.update(self.ball_position))
        if isinstance(position, (int, float)):
            self.ball_position = position
            self.move(round(position), self.y())
        self.ball_window.sync()

    def set_autonomous(self, enabled):
        self.config.autonomous_enabled = enabled
        self.autonomous_action.setChecked(enabled)
        self.interrupt_autonomy(detach=True)
        self.write_config()

    def set_stay(self, enabled):
        self.config.roaming_mode = 'stay' if enabled else 'free'
        self.stay_action.setChecked(enabled)
        self.interrupt_autonomy(detach=True)
        self.write_config()

    def advance_autonomy(self):
        if self.ball_game:
            return
        if not self.autonomy or not self.player:
            return
        if self.focus.active:
            self.interrupt_autonomy()
            return
        if not self.config.autonomous_enabled or not self.config.interactive or not self.autonomy.spec:
            if self.autonomy.active or self.autonomy.support or self.autonomy.pending_action:
                self.interrupt_autonomy(detach=True)
            return
        now = monotonic()
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if now-self.scene_checked >= .5 or self.desktop_scene is None or screen.name() != self.scene_screen:
            self.desktop_scene = self.desktop_observer.snapshot(screen)
            self.scene_checked, self.scene_screen = now, screen.name()
        blocked = (self.focus.active or self.press_position is not None or self.dragging or self.stroke_active or self.hovered
                   or self.menu.isVisible() or QApplication.activeModalWidget() is not None
                   or any(panel is not None and panel.isVisible()
                          for panel in (self.dialog, self.action_panel, self.package_dialog, self.buddy_dialog, self.focus_dialog, self.creator_dialog)))
        scale = self.config.scale/100
        foot = self.player.transform.map(QPointF(*self.runtime.data.get('autonomy', {}).get(
            'foot', self.runtime.package.manifest['canvas']['anchor']))) * scale
        actual = (self.x(), self.y())
        position = self.autonomous_position
        if position is None or (round(position[0]), round(position[1])) != actual:
            position = actual
        destination = self.runtime_call(lambda: self.autonomy.update(self.config, self.desktop_scene,
            position, (foot.x(), foot.y()), (self.width(), self.height()), scale, blocked))
        if isinstance(destination, tuple):
            self.autonomous_position = destination
            self.move(round(destination[0]), round(destination[1]))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(self.config.scale / 100, self.config.scale / 100)
        _, cream = PALETTES[self.config.color]
        def text(rect, content, size, color, bold=False):
            p.setPen(QColor(color))
            p.setFont(QFont("Microsoft YaHei UI", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, content)
        now = monotonic()
        reacting = self.config.interactive and now < self.reaction_until
        if self.show_bubble(now):
            p.setPen(QPen(QColor("#E9E2D7"), 1))
            p.setBrush(QColor(cream))
            p.drawRoundedRect(QRectF(20, 8, 240, 94), 22, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([QPointF(124, 100), QPointF(140, 114), QPointF(151, 100)]))
            caption = "距离下班还有" if self.snapshot.state == "working" else "今天辛苦啦" if self.snapshot.state == "done" else "好好享受今天"
            if self.focus.active:
                caption = '专注已暂停' if self.focus.paused_seconds is not None else '一起专注，慢慢来'
            text(QRectF(20, 16, 240, 25), caption, 10, "#9A8977")
            minutes, seconds = divmod(self.focus.remaining, 60)
            display = f'{minutes:02}:{seconds:02}' if self.focus.active else self.snapshot.display
            text(QRectF(20, 40, 240, 42), display, 24, "#55483E", True)
            foot = "快了快了，准备收工" if self.snapshot.state == "working" and self.snapshot.seconds <= 1800 else "搭子陪你，慢慢来" if self.snapshot.state == "working" else "生活时间，现在开始"
            if self.config.interactive:
                if self.dragging:
                    foot = "我们要搬去哪里呀"
                elif reacting:
                    foot = self.reaction_message
                elif self.hovered:
                    foot = "我在呢，陪你一起等下班"
            text(QRectF(20, 79, 240, 18), foot, 8, "#A29180")
        if self.gift and now < self.gift_until:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#FFF5E4'))
            p.drawRoundedRect(QRectF(2, 216, 68, 48), 12, 12)
            text(QRectF(3, 216, 66, 29), self.gift['symbol'], 19, '#A66F47')
            if self.config.remote_text:
                text(QRectF(3, 246, 66, 15), self.gift['label'], 8, '#9A745D')
        if self.player:
            self.runtime_call(lambda: self.player.draw(p))
        if not self.runtime or self.runtime.stopped:
            text(QRectF(20, 130, 240, 35), '右键打开角色管理', 14, '#9A745D')
            text(QRectF(20, 170, 240, 55), '导入角色包，开始陪伴' if not self.last_character_error else '角色播放异常，可切换或重新导入', 10, '#9A8977')
        if self.config.remote_text and self.runtime and self.runtime.current and self.runtime.context.get('source') == 'remote' and self.remote_message:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#FFFCF6'))
            p.drawRoundedRect(QRectF(39, 267, 202, 13), 6, 6)
            text(QRectF(39, 265, 202, 15), self.remote_message, 8, '#9A745D')

    def mousePressEvent(self, event):
        self.interrupt_autonomy(detach=True)
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = event.globalPosition().toPoint() - self.pos()
            self.press_position = event.globalPosition().toPoint()
            self.dragging = False
            self.held = False
            self.last_click_point = QPointF(event.position()) / (self.config.scale / 100)
            self.stroke_active = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier) and self.cat_contains(event.position())
            self.stroke_previous = QPointF(event.position())
            self.stroke_direction = QPointF()
            self.stroke_distance = self.stroke_turns = 0
            if self.stroke_active:
                self.package_event('gesture.stroke.start', self.last_click_point)
            elif self.cat_contains(event.position()):
                self.hold_timer.start(500)

    def mouseMoveEvent(self, event):
        if self.drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if self.stroke_active:
                delta = QPointF(event.position()) - self.stroke_previous
                if delta.manhattanLength() >= 3:
                    if delta.x()*self.stroke_direction.x() + delta.y()*self.stroke_direction.y() < 0:
                        self.stroke_turns += 1
                    self.stroke_distance += delta.manhattanLength()
                    self.stroke_direction = delta
                    self.stroke_previous = QPointF(event.position())
                    if self.stroke_turns >= 1 and self.stroke_distance >= 20 and monotonic()-self.stroke_last_emit > .25:
                        self.package_event('gesture.stroke', QPointF(event.position()) / (self.config.scale / 100))
                        self.stroke_last_emit = monotonic()
                        self.stroke_distance = self.stroke_turns = 0
                return
            if (event.globalPosition().toPoint() - self.press_position).manhattanLength() >= QApplication.startDragDistance():
                if not self.dragging:
                    self.hold_timer.stop()
                    if self.runtime:
                        self.runtime.cancel()
                    self.package_event('drag.start', self.last_click_point, 'drag')
                self.dragging = True
                self.click_timer.stop()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self.dragging:
                self.move(event.globalPosition().toPoint() - self.drag)
                self.keep_on_screen(event.globalPosition().toPoint())
                self.last_activity = monotonic()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_position = None
        if event.button() == Qt.MouseButton.LeftButton and self.drag is not None:
            self.hold_timer.stop()
            was_dragging = self.dragging
            self.drag = None
            self.dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if was_dragging:
                if self.runtime:
                    self.runtime.cancel('drag')
                self.package_event('drag.end', QPointF(event.position()) / (self.config.scale / 100))
                self.config.x, self.config.y = self.x(), self.y()
                self.write_config()
            elif self.stroke_active:
                self.package_event('gesture.stroke.end', QPointF(event.position()) / (self.config.scale / 100))
            elif self.cat_contains(event.position()) and not self.double_click and not self.held:
                # Wait out the double-click interval so opening settings doesn't pet.
                self.click_timer.start(QApplication.doubleClickInterval())
            self.stroke_active = False
        self.double_click = False

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.click_timer.stop()
        self.hold_timer.stop()
        self.double_click = True
        self.drag = None
        self.press_position = None
        self.open_settings()

    def keep_on_screen(self, point=None):
        screen = QApplication.screenAt(point or self.frameGeometry().center()) or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        self.move(max(rect.left(), min(self.x(), rect.right() - self.width() + 1)),
                  max(rect.top(), min(self.y(), rect.bottom() - self.height() + 1)))

    def wheelEvent(self, event):
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        step = 5 if delta > 0 else -5
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.config.opacity = max(20, min(100, self.config.opacity + step))
            self.setWindowOpacity(self.config.opacity / 100)
        else:
            self.resize_pet(max(50, min(200, self.config.scale + step)))
        self.save_timer.start(350)
        event.accept()

    def resize_pet(self, scale):
        self.interrupt_autonomy(detach=True)
        anchor = QPoint(self.x() + self.width() // 2, self.y() + self.height())
        self.config.scale = scale
        self.setFixedSize(round(280 * scale / 100), round(280 * scale / 100))
        self.move(anchor.x() - self.width() // 2, anchor.y() - self.height())
        self.keep_on_screen()
        self.config.x, self.config.y = self.x(), self.y()
        self.update()

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.action_panel and self.action_panel.isVisible():
            self.action_panel.place()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.action_panel and self.action_panel.isVisible():
            self.action_panel.place()

    def contextMenuEvent(self, event):
        self.open_actions()
        event.accept()

    def closeEvent(self, event):
        if self.tray.isVisible():
            event.ignore()
            self.hide()
        else:
            self.quit()

    def quit(self):
        if self.creator_dialog and not self.creator_dialog.close():
            return
        self.stop_ball()
        self.desktop_observer.close()
        self.updates.shutdown()
        self.buddy.stop()
        self.hold_timer.stop()
        if self.player:
            self.player.close()
        if self.buddy_dialog:
            self.buddy_dialog.hide()
        if self.action_panel:
            self.action_panel.hide()
        if self.package_dialog:
            self.package_dialog.hide()
        self.config.x, self.config.y = self.x(), self.y()
        self.write_config()
        self.tray.hide()
        QApplication.quit()


def main():
    import argparse
    import re
    parser = argparse.ArgumentParser(description='下班搭子 · Windows 桌面宠物')
    parser.add_argument('--profile', help='独立本机配置名，供双窗口联调使用')
    parser.add_argument('--update-ready', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.profile:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', args.profile):
            parser.error('profile 只能使用 1–32 位字母、数字、下划线或短横线')
        os.environ['OFFWORK_HOME'] = str(data_dir() / 'profiles' / args.profile)
    app = QApplication(sys.argv)
    app.setApplicationName("OffworkPet")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    lock_dir = data_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_dir / "offwork-pet.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "下班搭子已运行", "宠物已经在桌面上啦，可以从任务栏托盘打开设置。")
        return
    config, warning = load_config()
    pet = Pet(config)
    if args.update_ready:
        def acknowledge_startup():
            import json
            if not re.fullmatch(r'[0-9a-f]{32}', args.update_ready):
                return
            job = data_dir() / 'updates' / args.update_ready
            if job.is_dir() and (job / 'plan.json').is_file():
                (job / 'ready.json').write_text(json.dumps({'version': __version__}), encoding='utf-8')
        QTimer.singleShot(1500, acknowledge_startup)
    if warning:
        QTimer.singleShot(500, lambda: QMessageBox.warning(pet, "配置提示", warning))
    app.aboutToQuit.connect(lock.unlock)
    sys.exit(app.exec())
