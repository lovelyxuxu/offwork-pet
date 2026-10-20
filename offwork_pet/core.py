"""Configuration and calendar logic, independent of the GUI."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
import json
import math
import os
from pathlib import Path


@dataclass
class Config:
    off_time: str = "18:00"
    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    override_date: str = ""
    override_time: str = "18:00"
    color: str = "奶油橘"
    character: str = "lulu"
    relay_url: str = ""
    network_enabled: bool = False
    paused: bool = False
    remote_text: bool = True
    remote_sound: bool = False
    buddy_note: str = ""
    scale: int = 100
    topmost: bool = True
    opacity: int = 100
    bubble_mode: str = "hover_click"
    bubble_seconds: int = 6
    interactive: bool = True
    reminders: bool = True
    sound: bool = False
    click_through: bool = False
    x: int | None = None
    y: int | None = None


def data_dir() -> Path:
    custom = os.environ.get('OFFWORK_HOME')
    return Path(custom) if custom else Path(os.environ.get("APPDATA", str(Path.home()))) / "OffworkPet"


def config_path() -> Path:
    return data_dir() / "config.json"


def validate(config: Config) -> Config:
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
    if config.color not in ("奶油橘", "薄荷绿", "云朵蓝"):
        raise ValueError("未知配色")
    if config.character not in ('lulu', 'lumei'):
        raise ValueError('未知形象')
    if not isinstance(config.relay_url, str) or len(config.relay_url) > 500:
        raise ValueError('服务地址不正确')
    if not isinstance(config.buddy_note, str) or len(config.buddy_note) > 24:
        raise ValueError('搭子备注最多 24 字')
    if config.override_date:
        datetime.strptime(config.override_date, "%Y-%m-%d")
    for name in ("topmost", "interactive", "reminders", "sound", "click_through", 'network_enabled', 'paused', 'remote_text', 'remote_sound'):
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
        known = Config.__dataclass_fields__
        return validate(Config(**{k: v for k, v in data.items() if k in known})), ""
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
