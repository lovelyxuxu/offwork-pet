"""Configuration and calendar logic, independent of the GUI."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
import json
import math
import os
from pathlib import Path
import re
from .overlay_layout import validate_layout


@dataclass
class Config:
    off_time: str = "18:00"
    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    override_date: str = ""
    override_time: str = "18:00"
    color: str = "奶油橘"
    character: str = "offwork-seed"
    network_enabled: bool = False
    server_url: str = ''
    auto_update_check: bool = True
    paused: bool = False
    allow_visits: bool = True
    remote_text: bool = True
    remote_sound: bool = False
    buddy_note: str = ""
    scale: int = 100
    topmost: bool = True
    opacity: int = 100
    bubble_mode: str = "hover_click"
    bubble_seconds: int = 6
    dialogue_enabled: bool = True
    dialogue_proactive: bool = True
    dialogue_care_enabled: bool = True
    dialogue_packs: dict[str, str] = field(default_factory=dict)
    start_time: str = ""
    meal_times: dict[str, str] = field(default_factory=dict)
    interactive: bool = True
    pointer_look_enabled: bool = True
    autonomous_enabled: bool = True
    exploration_enabled: bool = True
    internal_surfaces: bool = True
    personality_enabled: bool = True
    activity_min_seconds: int = 20
    activity_max_seconds: int = 60
    roaming_mode: str = "free"
    reminders: bool = True
    rituals_enabled: bool = True
    sound: bool = False
    click_through: bool = False
    alert_seconds: int = 5
    alert_hotkeys: dict[str, str] = field(default_factory=lambda: {'safe': 'Ctrl+Alt+G', 'warning': 'Ctrl+Alt+Y', 'danger': 'Ctrl+Alt+R'})
    alert_buttons: list[dict] = field(default_factory=list)
    alert_sound: bool = False
    rally_receive: bool = True
    rally_name: str = ""
    notifications_muted: bool = False
    chat_hotkey: str = 'Ctrl+Alt+T'
    mute_hotkey: str = 'Ctrl+Alt+D'
    desktop_elements: dict[str, dict] = field(default_factory=dict)
    x: int | None = None
    y: int | None = None


HOTKEY_MODIFIERS = {'ctrl': 0x0002, 'alt': 0x0001, 'shift': 0x0004, 'win': 0x0008}

DEFAULT_ALERTS = (
    ('safe', '安全', '#2FBF71'), ('warning', '警戒', '#FFAA1A'), ('danger', '危险', '#FF3B30'),
)


def alert_buttons(config):
    """Read old hotkey settings until the user saves a custom button list."""
    if config.alert_buttons:
        return [dict(button) for button in config.alert_buttons]
    return [{'id': key, 'name': name, 'color': color, 'level': key,
             'hotkey': config.alert_hotkeys.get(key, '')} for key, name, color in DEFAULT_ALERTS]


def validate_alert_buttons(buttons):
    if not isinstance(buttons, list) or not 1 <= len(buttons) <= 32:
        raise ValueError('请保留 1–32 个警报按钮')
    identifiers, hotkeys = set(), set()
    for button in buttons:
        if not isinstance(button, dict):
            raise ValueError('警报按钮配置无效')
        key, name, color = (button.get(field) for field in ('id', 'name', 'color'))
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key) or key in identifiers:
            raise ValueError('警报按钮标识无效或重复')
        identifiers.add(key)
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 24
                or any(ord(character) < 32 or ord(character) == 127 for character in name)):
            raise ValueError('警报名称需要 1–24 字，不能包含换行或控制字符')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('请选择有效的警报颜色')
        if button.get('level') not in ('safe', 'warning', 'danger'):
            raise ValueError('未知提醒方式')
        shortcut = button.get('hotkey', '')
        if not isinstance(shortcut, str):
            raise ValueError('快捷键配置无效')
        if shortcut:
            combination = parse_hotkey(shortcut)
            if combination in hotkeys:
                raise ValueError('警报快捷键不能重复')
            hotkeys.add(combination)
    return buttons


def parse_hotkey(text):
    """Parse 'Ctrl+Alt+R' into Windows RegisterHotKey (modifiers, virtual-key)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('热键格式无效')
    parts = [part.strip().lower() for part in text.split('+')]
    if len(parts) < 2 or any(not part for part in parts):
        raise ValueError('热键需要至少一个修饰键和一个按键，例如 Ctrl+Alt+R')
    *modifiers, key = parts
    mods = 0
    for modifier in modifiers:
        if modifier not in HOTKEY_MODIFIERS or mods & HOTKEY_MODIFIERS[modifier]:
            raise ValueError('热键修饰键须为 Ctrl、Alt、Shift 或 Win，且不重复')
        mods |= HOTKEY_MODIFIERS[modifier]
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif re.fullmatch(r'f([1-9]|1[0-2])', key):
        vk = 0x6F + int(key[1:])
    else:
        raise ValueError('热键按键支持字母、数字或 F1–F12')
    return mods, vk


def notification_hotkeys(config):
    """One shared registry prevents alert/chat/mute shortcut collisions."""
    mapping = {button['id']: button['hotkey'] for button in alert_buttons(config) if button.get('hotkey')}
    if any(button['id'] in ('__chat', '__mute') for button in alert_buttons(config)):
        raise ValueError('警报按钮标识不能使用 __chat 或 __mute')
    for key, value in (('__chat', config.chat_hotkey), ('__mute', config.mute_hotkey)):
        if not isinstance(value, str):
            raise ValueError('快捷键配置无效')
        if value:
            mapping[key] = value
    combinations = [parse_hotkey(value) for value in mapping.values()]
    if len(set(combinations)) != len(combinations):
        raise ValueError('聊天、免打扰和警报快捷键不能重复')
    return mapping


def data_dir() -> Path:
    custom = os.environ.get('OFFWORK_HOME')
    return Path(custom) if custom else Path(os.environ.get("APPDATA", str(Path.home()))) / "OffworkPet"


def config_path() -> Path:
    return data_dir() / "config.json"


def validate(config: Config) -> Config:
    if not isinstance(config.server_url, str):
        raise ValueError('服务器地址无效')
    if config.server_url:
        from .credentials import normalize_endpoint
        config.server_url = normalize_endpoint(config.server_url)
    from .dialogue import clock_minutes, identifier
    if config.start_time:
        clock_minutes(config.start_time)
    if not isinstance(config.start_time, str):
        raise ValueError('上班时间格式无效')
    if not isinstance(config.dialogue_packs, dict) or len(config.dialogue_packs) > 512 or any(
            not identifier(key) or (value != '' and not identifier(value)) for key, value in config.dialogue_packs.items()):
        raise ValueError('角色台词选择无效')
    if not isinstance(config.meal_times, dict) or set(config.meal_times) - {'breakfast', 'lunch', 'dinner'}:
        raise ValueError('饭点设置无效')
    for value in config.meal_times.values():
        clock_minutes(value)
    for value in (config.off_time, config.override_time):
        parsed = time.fromisoformat(value)
        if parsed.second or len(value) != 5:
            raise ValueError("下班时间格式必须为 HH:MM")
    if not config.weekdays or any(type(day) is not int or day not in range(7) for day in config.weekdays):
        raise ValueError("至少选择一个工作日")
    if type(config.scale) is not int or not 50 <= config.scale <= 200:
        raise ValueError("大小必须在 50% 到 200% 之间")
    if type(config.opacity) is not int or not 20 <= config.opacity <= 100:
        raise ValueError("不透明度必须在 20% 到 100% 之间")
    if config.bubble_mode not in ("always", "hover", "click", "hover_click", "hidden"):
        raise ValueError("未知气泡展示方式")
    if type(config.bubble_seconds) is not int or not 2 <= config.bubble_seconds <= 30:
        raise ValueError("点击展示时长必须在 2 到 30 秒之间")
    if (type(config.activity_min_seconds) is not int or type(config.activity_max_seconds) is not int
            or not 5 <= config.activity_min_seconds <= config.activity_max_seconds <= 3600):
        raise ValueError("自主活动间隔须在 5 到 3600 秒之间，最短间隔不能大于最长间隔")
    if config.roaming_mode not in ('off', 'stay', 'free'):
        raise ValueError("未知自主活动范围")
    if type(config.autonomous_enabled) is not bool:
        raise ValueError("自主活动开关必须为布尔值")
    if config.color not in ("奶油橘", "薄荷绿", "云朵蓝"):
        raise ValueError("未知配色")
    if not isinstance(config.character, str) or len(config.character) > 96:
        raise ValueError('角色标识不正确')
    if not isinstance(config.buddy_note, str) or len(config.buddy_note) > 24:
        raise ValueError('搭子备注最多 24 字')
    if type(config.alert_seconds) is not int or not 2 <= config.alert_seconds <= 15:
        raise ValueError('警报时长必须在 2 到 15 秒之间')
    if not isinstance(config.alert_hotkeys, dict) or set(config.alert_hotkeys) - {'safe', 'warning', 'danger'}:
        raise ValueError('警报热键配置无效')
    hotkeys = set()
    for text in config.alert_hotkeys.values():
        combination = parse_hotkey(text)
        if combination in hotkeys:
            raise ValueError('警报热键不能重复')
        hotkeys.add(combination)
    if not isinstance(config.alert_buttons, list):
        raise ValueError('警报按钮配置无效')
    if config.alert_buttons:
        validate_alert_buttons(config.alert_buttons)
    notification_hotkeys(config)
    validate_layout(config.desktop_elements)
    if type(config.notifications_muted) is not bool:
        raise ValueError('免打扰开关必须为布尔值')
    if not isinstance(config.rally_name, str) or len(config.rally_name) > 24:
        raise ValueError('集会名字最多 24 字')
    if config.override_date:
        datetime.strptime(config.override_date, "%Y-%m-%d")
    for name in ("topmost", "interactive", "pointer_look_enabled", "reminders", "rituals_enabled", "sound", "click_through", 'network_enabled', 'paused', 'allow_visits', 'remote_text', 'remote_sound', 'auto_update_check', 'exploration_enabled', 'internal_surfaces', 'personality_enabled', 'dialogue_enabled', 'dialogue_proactive', 'dialogue_care_enabled', 'alert_sound', 'rally_receive'):
        if type(getattr(config, name)) is not bool:
            raise ValueError("开关配置必须为布尔值")
    for name in ("x", "y"):
        if getattr(config, name) is not None and type(getattr(config, name)) is not int:
            raise ValueError("位置必须为整数")
    return config


def load_config(path: Path | None = None) -> tuple[Config, str]:
    path = path or config_path()
    if not path.exists():
        return Config(), ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "bubble_mode" not in data and "bubble" in data:
            data["bubble_mode"] = "always" if data["bubble"] else "hidden"
        if data.get("character") in ("lulu", "lumei"):
            data["character"] = "gugagaga"
        if "server_url" not in data and data.get("network_enabled") is True:
            # Already-online installations used this address before server selection.
            data["server_url"] = "http://106.54.27.8"
        known = Config.__dataclass_fields__
        config = Config(**{k: v for k, v in data.items() if k in known})
        # New default shortcuts must not invalidate a user's existing alert keys.
        occupied = {parse_hotkey(item['hotkey']) for item in alert_buttons(config) if item.get('hotkey')}
        for key in ('chat_hotkey', 'mute_hotkey'):
            value = getattr(config, key)
            if key not in data and value and parse_hotkey(value) in occupied:
                setattr(config, key, '')
            elif value:
                occupied.add(parse_hotkey(value))
        return validate(config), ""
    except (OSError, ValueError, TypeError, AttributeError):
        return Config(), f"配置读取失败，已使用默认设置。原文件保留在 {path}"


def save_config(config: Config, path: Path | None = None) -> None:
    validate(config)
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def bubble_visible(mode: str, hovered: bool, clicked_until: float, now: float) -> bool:
    """Use monotonic time so changing the system clock cannot extend a click."""
    return (mode == "always"
            or (mode in ("hover", "hover_click") and hovered)
            or (mode in ("click", "hover_click") and now < clicked_until))


@dataclass(frozen=True)
class Countdown:
    state: str
    seconds: int
    target: datetime | None

    @property
    def display(self) -> str:
        if self.state == "rest":
            return "今天休息"
        if self.state == "done":
            return "已下班！"
        hours, remainder = divmod(self.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"


def countdown(now: datetime, config: Config) -> Countdown:
    override = config.override_date == now.date().isoformat()
    if now.weekday() not in config.weekdays and not override:
        return Countdown("rest", 0, None)
    clock = config.override_time if override else config.off_time
    target = datetime.combine(now.date(), time.fromisoformat(clock))
    seconds = max(0, math.ceil((target - now).total_seconds()))
    return Countdown("done" if now >= target else "working", seconds, target)


class ReminderTracker:
    """Notify only on observed crossings; launching after work never fires."""
    def __init__(self):
        self.previous: Countdown | None = None
        self.sent: set[tuple[datetime, int]] = set()

    def update(self, current: Countdown) -> list[int]:
        previous, self.previous = self.previous, current
        if not previous or current.target is None or previous.target != current.target:
            return []
        self.sent = {key for key in self.sent if key[0].date() == current.target.date()}
        crossed = [threshold for threshold in (1800, 600, 0)
                   if previous.seconds > threshold >= current.seconds
                   and (current.target, threshold) not in self.sent]
        for threshold in crossed:
            self.sent.add((current.target, threshold))
        return [min(crossed)] if crossed else []
