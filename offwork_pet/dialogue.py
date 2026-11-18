"""Data-only, character-bound dialogue packs and a quiet local scheduler."""
from collections import deque
from datetime import timedelta
import json
import math
from pathlib import Path
import random
import re
import uuid

FORMAT = 'offwork-pet.dialogue/v1'
MAX_BYTES = 1024 * 1024
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z')
EVENTS = {'app.launch', 'schedule.work-start', 'schedule.meal', 'companion.care',
          'companion.idle', 'focus.started', 'focus.finished', 'schedule.soon',
          'schedule.offwork', 'pointer.click', 'gesture.stroke', 'drag.end'}
CONDITIONS = {'localTime', 'firstLaunchOfDay', 'secondsSinceLastGreetingAtLeast',
              'workScheduleKnown', 'workingDay', 'observedCrossing', 'meal',
              'relativeWindowMinutes', 'focusActive', 'careTopic',
              'companionElapsedMinutesAtLeast', 'focusCompleted', 'weekday', 'dragMoved'}


class DialogueError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise DialogueError(message)


def clock_minutes(value):
    require(isinstance(value, str) and re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', value), '时间须为 HH:MM')
    return int(value[:2]) * 60 + int(value[3:])


def plain(value, maximum=160):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and not any(ord(char) < 32 for char in value))


def identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, '文件含有重复字段')
        result[key] = value
    return result


def read_pack(path):
    path = Path(path)
    require(path.stat().st_size <= MAX_BYTES, '台词包不能超过 1 MiB')
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise DialogueError('台词包不是有效的数据文件') from error
    return validate_pack(data)


def validate_pack(data):
    require(isinstance(data, dict), '台词包须为对象')
    require(set(data) <= {'format', 'id', 'version', 'displayName', 'characterId', 'locale',
                         'medium', 'description', 'credits', 'selection', 'presentation',
                         'scheduleDefaults', 'scenes'}, '台词包包含未知字段，请使用兼容版本')
    require(data.get('format') == FORMAT, '不支持此台词包格式，请更新客户端或使用正式台词包')
    require(all(identifier(data.get(key)) for key in ('id', 'characterId')), '台词包或角色标识无效')
    require(isinstance(data.get('version'), str) and re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}', data['version']), '台词包版本须为数字 x.y.z')
    require(plain(data.get('displayName'), 64) and data.get('medium') == 'text', '目前只支持有名称的文字台词包')
    require(data.get('locale') == 'zh-CN', '目前只支持中文台词包')
    require(plain(data.get('description', '角色台词'), 300), '台词包说明过长')
    require(isinstance(data.get('credits', {}), dict) and len(data.get('credits', {})) <= 16, '署名格式无效')
    require(all(plain(k, 64) and plain(v, 400) for k, v in data.get('credits', {}).items()), '署名内容无效')
    settings = data.get('selection', {})
    supported = {'activePacksPerCharacter': 1, 'linesPerAppearance': 1,
                 'strategy': 'shuffle-bag-per-scene', 'avoidRecentLines': 12,
                 'avoidRecentSemanticGroups': 2, 'onMissingScene': 'skip',
                 'onCharacterChange': 'discard-pending-and-switch-pool',
                 'onResume': 'drop-expired', 'respectHostVisibilityAndQuietSettings': True}
    require(isinstance(settings, dict) and all(k in supported and type(v) is type(supported[k]) and v == supported[k]
                                             for k, v in settings.items()), '台词包的选择策略不受支持')
    presentation = data.get('presentation', {})
    require(isinstance(presentation, dict) and set(presentation) <= {'seconds', 'sound', 'wordWrap'}, '展示设置无效')
    require(type(presentation.get('seconds', 8)) is int and 2 <= presentation.get('seconds', 8) <= 20, '展示时长须为 2～20 秒')
    require(presentation.get('sound', False) is False and presentation.get('wordWrap', True) is True, '本版台词包只支持换行文字')
    meals = data.get('scheduleDefaults', {})
    require(isinstance(meals, dict) and set(meals) <= {'breakfast', 'lunch', 'dinner'}, '饭点设置无效')
    for value in meals.values():
        clock_minutes(value)
    scenes = data.get('scenes')
    require(isinstance(scenes, list) and 1 <= len(scenes) <= 128, '场景数量须为 1～128 个')
    seen, total = set(), 0
    for scene in scenes:
        require(isinstance(scene, dict) and set(scene) <= {'id', 'label', 'trigger', 'when', 'priority',
                'cooldownSeconds', 'semanticGroup', 'maxPerDay', 'occurrenceGroup', 'lines'}, '场景含有未知字段')
        require(identifier(scene.get('id')) and scene['id'] not in seen, '场景标识无效或重复')
        seen.add(scene['id'])
        require(plain(scene.get('label'), 64) and isinstance(scene.get('trigger'), str) and scene['trigger'] in EVENTS, '场景名称或事件无效')
        for key, default, low, high in (('priority', 10, 0, 100), ('cooldownSeconds', 0, 0, 86400), ('maxPerDay', 100, 1, 100)):
            value = scene.get(key, default)
            require(type(value) is int and low <= value <= high, '场景次数、优先级或冷却无效')
        for key in ('semanticGroup', 'occurrenceGroup'):
            require(key not in scene or identifier(scene[key]), '场景分组标识无效')
        lines = scene.get('lines')
        require(isinstance(lines, list) and 1 <= len(lines) <= 128 and all(plain(line, 48) for line in lines), '每个场景须有 1～128 条不超过 48 字的台词')
        require(len(set(lines)) == len(lines), '同一场景含有重复台词')
        total += len(lines)
        when = scene.get('when', {})
        require(isinstance(when, dict) and set(when) <= CONDITIONS, '未知场景条件，不能忽略后使用')
        for key, value in when.items():
            if key == 'localTime':
                require(isinstance(value, list) and len(value) == 2, '时间范围须有起点和终点')
                require(clock_minutes(value[0]) != clock_minutes(value[1]), '时间范围不能为空')
            elif key == 'weekday':
                require(isinstance(value, list) and 1 <= len(value) <= 7 and all(type(n) is int and 0 <= n <= 6 for n in value), '星期条件无效')
            elif key == 'relativeWindowMinutes':
                require(isinstance(value, list) and len(value) == 2 and all(type(n) is int and -720 <= n <= 720 for n in value) and value[0] < value[1] and 'meal' in when, '饭点时间范围无效')
            elif key == 'meal':
                require(value in ('breakfast', 'lunch', 'dinner'), '饭点名称无效')
            elif key == 'careTopic':
                require(identifier(value), '关心主题无效')
            elif key in ('companionElapsedMinutesAtLeast', 'secondsSinceLastGreetingAtLeast'):
                require(type(value) is int and 0 <= value <= 86400, '经过时间条件无效')
            else:
                require(type(value) is bool, '场景开关条件须为布尔值')
    require(total <= 4096, '台词总量不能超过 4096 句')
    return data


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def scene_timing(scene, pack, config):
    """Explain the author's conditions without exposing expression syntax."""
    event, when = scene['trigger'], scene.get('when', {})
    notes = []
    if event == 'app.launch':
        if when.get('firstLaunchOfDay') is False:
            notes.append('当天再次启动时')
            if 'secondsSinceLastGreetingAtLeast' in when:
                seconds = when['secondsSinceLastGreetingAtLeast']
                duration = f'{math.ceil(seconds/60)} 分钟' if seconds >= 60 else f'{seconds} 秒'
                notes.append(f'距离上次问候至少 {duration}')
        else:
            notes.append('当天首次启动时问候')
    elif event == 'schedule.work-start':
        notes.append(f"工作日 {config.start_time} 跨过上班时间时" if config.start_time else '尚未设置上班时间，此场景暂不触发')
    elif event == 'schedule.meal':
        meal = when.get('meal')
        clock = config.meal_times.get(meal) or pack.get('scheduleDefaults', {}).get(meal)
        window = when.get('relativeWindowMinutes')
        if clock and window:
            start = clock_minutes(clock)
            clocks = [f'{((start + offset) % 1440) // 60:02}:{(start + offset) % 60:02}' for offset in window]
            notes.append(f'{clocks[0]}–{clocks[1]} 内择机展示')
            if scene.get('maxPerDay') == 1:
                notes.append('同一餐最多一次')
    elif event == 'companion.care':
        minutes = max(50, when.get('companionElapsedMinutesAtLeast', 0))
        notes.append(f'有效陪伴累计 {minutes} 分钟后可选；每轮只随机选一句关心')
    elif event == 'companion.idle':
        notes.append('有效陪伴约 30–60 分钟随机展示一次')
    else:
        notes.append({'pointer.click': '单击桌宠时', 'gesture.stroke': '抚摸桌宠时',
                      'drag.end': '拖动结束时', 'focus.started': '开始专注时',
                      'focus.finished': '专注计时正常结束时',
                      'schedule.soon': '工作日下班前 10 分钟',
                      'schedule.offwork': '工作日到达设定下班时间时'}.get(event, '满足场景条件时'))
    if 'localTime' in when:
        notes.append('适用时段 ' + '–'.join(when['localTime']))
    if 'weekday' in when:
        notes.append('仅周' + '、'.join('一二三四五六日'[day] for day in when['weekday']))
    if when.get('workingDay') is False:
        notes.append('仅用户设置的休息日')
    elif when.get('workingDay') is True:
        notes.append('仅用户设置的工作日')
    return '；'.join(notes) + '。'


class DialogueLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.packs = {}
        self.errors = []
        for path in sorted(self.root.glob('*.petdialogue')):
            try:
                require(not path.is_symlink(), '台词包不能为符号链接')
                data = read_pack(path)
                previous = self.packs.get(data['id'])
                if not previous or self.version(data) > self.version(previous):
                    self.packs[data['id']] = data
            except (DialogueError, OSError) as error:
                self.errors.append(str(error))

    @staticmethod
    def version(data):
        return tuple(map(int, data['version'].split('.')))

    def install(self, path, character_id=None):
        data = read_pack(path)
        require(character_id is None or data['characterId'] == character_id, '此台词包属于其他角色，请先选择对应角色')
        previous = self.packs.get(data['id'])
        if previous:
            require(previous['characterId'] == data['characterId'], '同一台词包标识不能更换角色')
            require(self.version(data) >= self.version(previous), '已安装更新的台词包版本')
            require(self.version(data) != self.version(previous) or data == previous, '同一版本已有不同内容，请提高版本号')
        target = self.root / f"{data['id']}-{data['version']}.petdialogue"
        require(not target.is_symlink(), '台词包安装目标无效')
        atomic_json(target, data)
        self.packs[data['id']] = data
        return data

    def seed(self, directory):
        for path in sorted(Path(directory).glob('*.petdialogue')):
            try:
                data = read_pack(path)
                if data['id'] not in self.packs:
                    self.install(path)
            except (DialogueError, OSError) as error:
                self.errors.append(str(error))

    def entries(self, character_id):
        return sorted((pack for pack in self.packs.values() if pack['characterId'] == character_id),
                      key=lambda pack: pack['displayName'])

    def selected(self, character_id, choices):
        if character_id in choices:
            pack = self.packs.get(choices[character_id])
            return pack if pack and pack['characterId'] == character_id else None
        return next(iter(self.entries(character_id)), None)


class DialogueRuntime:
    def __init__(self, library, state_path=None, rng=None):
        self.library, self.state_path = library, Path(state_path) if state_path else None
        self.rng = rng or random.Random()
        self.state = {'roles': {}}
        self.error = ''
        if self.state_path and self.state_path.exists():
            try:
                require(self.state_path.stat().st_size <= 4 * MAX_BYTES, '台词历史过大')
                state = json.loads(self.state_path.read_text(encoding='utf-8'))
                require(isinstance(state, dict) and isinstance(state.get('roles'), dict), '台词历史无效')
                # History is local, but must not break a working desktop if corrupted.
                for value in state['roles'].values():
                    require(isinstance(value, dict) and isinstance(value.get('days', {}), dict)
                            and isinstance(value.get('last', {}), dict), '台词历史无效')
                    require(isinstance(value.get('greetingDate', ''), str), '台词历史日期无效')
                    numbers = [value.get('lastGreetingAt', 0), *value.get('last', {}).values()]
                    require(all(type(n) in (int, float) and math.isfinite(n) and abs(n) < 10**12 for n in numbers), '台词历史时间无效')
                    require(all(isinstance(day, dict) and all(type(n) is int and 0 <= n <= 10000 for n in day.values())
                                for day in value.get('days', {}).values()), '台词历史次数无效')
                self.state = state
            except (OSError, ValueError, RecursionError):
                self.error = '台词历史读取失败，本次重新计时。原文件保留。'
                self.state_path = None
        self.role = ''
        self.pack = None
        self.text = ''
        self.active_event = ''
        self.until = self.priority = 0
        self.elapsed = 0.0
        self.previous = self.previous_mono = None
        self.schedule_key = None
        self.bags, self.recent = {}, deque(maxlen=12)
        self.recent_groups = deque(maxlen=2)
        self.last_auto = -math.inf
        self.last_care = 0.0
        self.next_idle = 1800.0
        self.launch_pending = True
        self.launch_mono = None

    def save(self):
        if self.state_path:
            try:
                atomic_json(self.state_path, self.state)
            except OSError:
                self.error = '台词历史暂时无法保存。'

    def switch(self, character_id, choices):
        pack = self.library.selected(character_id, choices)
        if self.role == character_id and self.pack is pack:
            return
        self.role, self.pack = character_id, pack
        self.clear()
        self.bags.clear()
        self.recent.clear()
        self.recent_groups.clear()
        self.elapsed, self.last_care = 0.0, 0.0
        self.next_idle = self.rng.uniform(1800, 3600)
        # Switching content must not replay the application's launch greeting.

    def clear(self):
        self.text, self.until, self.priority = '', 0, 0
        self.active_event = ''

    def role_state(self):
        value = self.state['roles'].setdefault(self.role, {})
        value.setdefault('days', {})
        value.setdefault('last', {})
        return value

    def matching(self, scene, event, now, config, facts):
        if scene['trigger'] != event:
            return False
        history = self.role_state()
        first = history.get('greetingDate') != now.date().isoformat()
        values = {'firstLaunchOfDay': first, 'workingDay': now.weekday() in config.weekdays or config.override_date == now.date().isoformat(),
                  'focusActive': False, 'workScheduleKnown': bool(config.start_time if event == 'schedule.work-start' else config.off_time), **facts}
        when = scene.get('when', {})
        for key, value in when.items():
            if key == 'localTime':
                start, end = map(clock_minutes, value)
                clock = now.hour * 60 + now.minute
                if not (start <= clock < end if start < end else clock >= start or clock < end):
                    return False
            elif key == 'weekday':
                if now.weekday() not in value:
                    return False
            elif key == 'companionElapsedMinutesAtLeast':
                if self.elapsed < value * 60:
                    return False
            elif key == 'secondsSinceLastGreetingAtLeast':
                if now.timestamp() - history.get('lastGreetingAt', 0) < value:
                    return False
            elif key == 'relativeWindowMinutes':
                meal = when['meal']
                clock = config.meal_times.get(meal) or self.pack.get('scheduleDefaults', {}).get(meal)
                if not clock:
                    return False
                offset = (now.hour * 60 + now.minute + now.second / 60) - clock_minutes(clock)
                if not value[0] <= offset < value[1]:
                    return False
            elif key in ('meal', 'careTopic'):
                if key in facts and facts[key] != value:
                    return False
            elif key not in values or values[key] != value:
                return False
        return True

    def draw(self, scene):
        key = scene['id']
        bag = self.bags.get(key)
        if not bag:
            bag = list(scene['lines'])
            self.rng.shuffle(bag)
            self.bags[key] = bag
        limit = min(12, len(scene['lines']) // 2)
        excluded = list(self.recent)[-limit:] if limit else []
        chosen = next((i for i, line in enumerate(bag) if line not in excluded), None)
        if chosen is None:
            chosen = next((i for i, line in enumerate(bag) if not self.recent or line != self.recent[-1]), 0)
        result = bag.pop(chosen)
        self.recent.append(result)
        return result

    def emit(self, event, now, mono, config, *, available=True, facts=None, focus=False):
        if not self.pack or not config.dialogue_enabled or not available or config.bubble_mode == 'hidden' or config.paused:
            return False
        if focus and event != 'focus.started':
            return False
        history = self.role_state()
        date = now.date().isoformat()
        # Keep two local dates, including a future date after a clock rollback.
        history['days'] = {key: val for key, val in history.get('days', {}).items() if key >= (now.date() - timedelta(days=1)).isoformat()}
        day = history['days'].setdefault(date, {})
        automatic = event in ('companion.idle', 'companion.care', 'schedule.meal', 'schedule.work-start')
        if automatic and mono - self.last_auto < 600:
            return False
        candidates = []
        for scene in self.pack['scenes']:
            if not self.matching(scene, event, now, config, {'focusActive': focus, **(facts or {})}):
                continue
            key = self.pack['id'] + ':' + scene['id']
            group_key = self.pack['id'] + ':' + scene.get('occurrenceGroup', scene['id'])
            if day.get(group_key, 0) >= scene.get('maxPerDay', 100):
                continue
            if now.timestamp() - history['last'].get(key, -math.inf) < scene.get('cooldownSeconds', 0):
                continue
            if self.text and mono < self.until and scene.get('priority', 10) <= self.priority:
                continue
            candidates.append(scene)
        self.rng.shuffle(candidates)
        candidates.sort(key=lambda scene: (-scene.get('priority', 10),
                         scene.get('semanticGroup') in self.recent_groups if automatic else False))
        if not candidates:
            return False
        scene = candidates[0]
        self.text = self.draw(scene)
        self.active_event = event
        self.until = mono + self.pack.get('presentation', {}).get('seconds', 8)
        self.priority = scene.get('priority', 10)
        key = self.pack['id'] + ':' + scene['id']
        group_key = self.pack['id'] + ':' + scene.get('occurrenceGroup', scene['id'])
        history['last'][key] = now.timestamp()
        day[group_key] = day.get(group_key, 0) + 1
        self.recent_groups.append(scene.get('semanticGroup', scene['id']))
        if event == 'app.launch':
            history['greetingDate'], history['lastGreetingAt'] = date, now.timestamp()
            self.launch_pending = False
        if automatic or event == 'app.launch':
            self.last_auto = mono
        if event in ('companion.care', 'focus.finished'):
            self.last_care = self.elapsed
        self.save()
        return True

    def tick(self, now, mono, config, *, available=True, focus=False, automatic=True, displayable=True):
        if self.launch_mono is None:
            self.launch_mono = mono
        previous, previous_mono = self.previous, self.previous_mono
        self.previous, self.previous_mono = now, mono
        signature = (config.start_time, tuple(config.weekdays), config.override_date)
        same_schedule, self.schedule_key = signature == self.schedule_key, signature
        if self.text and mono >= self.until:
            self.clear()
        gap = mono - previous_mono if previous_mono is not None else 0
        if gap > 10 or gap < 0:
            self.elapsed = self.last_care = 0
            self.next_idle = self.rng.uniform(1800, 3600)
        if not available or not config.dialogue_enabled or config.paused or config.bubble_mode == 'hidden':
            self.clear()
            # A menu or full-screen window suppresses display, not the already
            # earned companion time. Repeated short interruptions must not
            # postpone a 50-minute reminder forever. Explicit pause and long
            # suspend gaps still reset a session.
            if config.paused:
                self.elapsed = self.last_care = 0
            return
        if focus:
            if getattr(self, 'active_event', '') != 'focus.started':
                self.clear()
            return
        if 0 < gap <= 10:
            self.elapsed += gap
        if not self.pack:
            return
        if not automatic or not displayable:
            return
        if self.launch_pending and mono - self.launch_mono <= 60:
            self.emit('app.launch', now, mono, config)
        if previous and same_schedule and config.start_time and 0 <= gap <= 10 and previous.date() == now.date():
            target = now.replace(hour=int(config.start_time[:2]), minute=int(config.start_time[3:]), second=0, microsecond=0)
            if previous < target <= now:
                self.emit('schedule.work-start', now, mono, config, facts={'observedCrossing': True})
        if self.emit('schedule.meal', now, mono, config):
            return
        if config.dialogue_care_enabled and self.elapsed - self.last_care >= 3000 and self.emit('companion.care', now, mono, config):
            return
        if self.elapsed >= self.next_idle:
            if self.emit('companion.idle', now, mono, config):
                self.next_idle = self.elapsed + self.rng.uniform(1800, 3600)
