"""Validated, data-only character packages and atomic local installation."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from tempfile import TemporaryDirectory
import uuid
from zipfile import ZipFile, BadZipFile

from PIL import Image

ENGINE_VERSION = (1, 0, 0)
CAPABILITIES = {'render.frames/v1', 'input.pointer/v1', 'input.stroke/v1',
                'behavior.rules/v1', 'audio.wav/v1', 'input.pointer-look/v1',
                'behavior.autonomy/v1', 'desktop.motion/v1', 'schedule.events/v1', 'desktop.toys/v1',
                'desktop.explore/v1', 'focus.events/v1', 'render.gifts/v1'}
ALLOWED_SUFFIXES = {'.json', '.png', '.webp', '.wav', '.txt', '.md'}
MAX_FILES, MAX_BYTES, MAX_IMAGE_BYTES = 4096, 256 * 1024**2, 32 * 1024**2
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z')
EVENTS = {'pointer.click', 'pointer.hold', 'pointer.enter',
          'pointer.leave', 'drag.start', 'drag.end', 'gesture.stroke',
          'gesture.stroke.start', 'gesture.stroke.end', 'gesture.stroke.cancel', 'timer', 'inactivity',
          'schedule.soon', 'schedule.offwork', 'focus.finished'}


class PackageError(ValueError):
    pass


def require(ok, where, message):
    if not ok:
        raise PackageError(f'{where}: {message}')


def identifier(value, where):
    require(isinstance(value, str) and ID.fullmatch(value), where, '标识须为 1–96 位字母、数字、点、横线或下划线')
    return value


def number(value, where, minimum=0, maximum=1_000_000):
    require(type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum,
            where, f'数值须在 {minimum}–{maximum} 之间')
    return value


def text(value, where, maximum=200, empty=False):
    require(isinstance(value, str) and (empty or bool(value.strip())) and len(value) <= maximum,
            where, f'须为不超过 {maximum} 字的文本')
    return value


def mapping(value, where, maximum=256):
    require(isinstance(value, dict) and len(value) <= maximum, where, '须为数量有限的对象')
    return value


def array(value, where, maximum=256):
    require(isinstance(value, list) and len(value) <= maximum, where, '须为数量有限的列表')
    return value


def boolean(value, where):
    require(type(value) is bool, where, '须为布尔值')


def relative(value):
    text(value, '资源路径', 240)
    path = PurePosixPath(value)
    require('\\' not in value and ':' not in value and not path.is_absolute()
            and all(p not in ('', '.', '..') for p in value.split('/')),
            value, '必须是包内相对路径')
    require(all(not p.endswith((' ', '.')) and p.split('.')[0].upper() not in
                {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                 *(f'LPT{i}' for i in range(1, 10))} for p in path.parts), value, '不支持的文件名')
    return path.as_posix()


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, str(path), f'重复字段 {key}')
            result[key] = value
        return result
    try:
        require(path.stat().st_size <= 2 * 1024**2, str(path), '配置文件过大')
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(PackageError(f'无效数值 {value}')))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise PackageError(f'{path.name}: 无法读取配置 ({error})') from error


@dataclass
class CharacterPackage:
    root: Path
    manifest: dict
    behavior: dict
    digest: str

    @property
    def id(self):
        return self.manifest['id']

    @property
    def name(self):
        return self.manifest['displayName']

    def asset(self, name):
        path = self.root / relative(name)
        require(path.resolve().is_relative_to(self.root.resolve()) and path.is_file()
                and not path.is_symlink(), name, '资源缺失或越过包目录')
        return path

    def snapshot(self, revision):
        return {'packageId': self.id, 'packageVersion': self.manifest['version'],
                'displayName': self.name, 'digest': self.digest, 'revision': revision,
                'interactions': [{'id': item['id'], 'label': item['label'], 'order': item.get('order', 0)}
                                 for item in self.behavior.get('interactions', [])]}


def _load_package(root):
    root = Path(root)
    manifest = mapping(read_json(root / 'manifest.json'), 'manifest')
    require(type(manifest.get('formatVersion')) is int and manifest['formatVersion'] == 1,
            'formatVersion', '不支持的角色包格式')
    identifier(manifest.get('id'), 'id')
    version = text(manifest.get('version'), 'version', 40)
    require(re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', version), 'version', '须使用 x.y.z 版本格式')
    text(manifest.get('displayName'), 'displayName', 64)
    text(manifest.get('author'), 'author', 100)
    minimum = manifest.get('minEngineVersion', '1.0.0')
    require(isinstance(minimum, str) and re.fullmatch(r'\d+\.\d+\.\d+', minimum), 'minEngineVersion', '版本格式错误')
    require(tuple(map(int, minimum.split('.'))) <= ENGINE_VERSION, 'minEngineVersion', '请先升级应用')
    required = array(manifest.get('requires', []), 'requires', 16)
    require(all(isinstance(item, str) and item in CAPABILITIES for item in required), 'requires', '包含不支持的必要能力')
    canvas = mapping(manifest.get('canvas'), 'canvas')
    width = number(canvas.get('width'), 'canvas.width', 1, 4096)
    height = number(canvas.get('height'), 'canvas.height', 1, 4096)
    require(type(width) is int and type(height) is int and width * height * 4 <= MAX_IMAGE_BYTES,
            'canvas', '画布超过解码预算')
    anchor = array(canvas.get('anchor'), 'canvas.anchor', 2)
    require(len(anchor) == 2, 'canvas.anchor', '需要 x,y')
    number(anchor[0], 'canvas.anchor.x', 0, width)
    number(anchor[1], 'canvas.anchor.y', 0, height)
    behavior = mapping(read_json(root / relative(manifest.get('behavior', 'behavior.json'))), 'behavior')
    package = CharacterPackage(root, manifest, behavior, '')
    images = {}
    def image(name):
        path = package.asset(name)
        require(path.suffix.lower() in ('.png', '.webp'), name, '仅支持 PNG/WebP 图片')
        if name not in images:
            try:
                with Image.open(path) as decoded:
                    w, h = decoded.size
                    require(w > 0 and h > 0 and w <= 8192 and h <= 8192 and w*h*4 <= MAX_IMAGE_BYTES,
                            name, '图片尺寸或解码大小超限')
                    require(getattr(decoded, 'n_frames', 1) == 1, name, '动画图片需先转换为显式帧')
                    decoded.load()
                    images[name] = w, h
            except (OSError, Image.DecompressionBombError) as error:
                raise PackageError(f'{name}: 图片损坏或无法解码') from error
        return images[name]
    thumb = image(manifest.get('thumbnail'))
    require(thumb[0] <= 512 and thumb[1] <= 512, 'thumbnail', '缩略图须在 512×512 以内')
    animations = mapping(behavior.get('animations'), 'animations', 128)
    states = mapping(behavior.get('states'), 'states', 128)
    actions = mapping(behavior.get('actions', {}), 'actions', 128)
    regions = mapping(behavior.get('regions', {}), 'regions', 64)
    for group_name, group in [('animations', animations), ('states', states), ('actions', actions), ('regions', regions)]:
        for key, value in group.items():
            identifier(key, group_name)
            mapping(value, f'{group_name}.{key}')
    require(behavior.get('initialState') in states, 'initialState', '状态不存在')
    require(behavior.get('recoveryState') is None or behavior['recoveryState'] in states,
            'recoveryState', '状态不存在')
    limit = behavior.get('maxQueuedActions', 8)
    require(type(limit) is int and 0 <= limit <= 8, 'maxQueuedActions', '排队数量应为 0 到 8 的整数')
    total_frames = 0
    for key, animation in animations.items():
        where = f'animations.{key}'
        boolean(animation.get('loop', False), where + '.loop')
        frames = array(animation.get('frames'), where + '.frames', 2048)
        require(bool(frames), where, '动画没有有效帧')
        total_frames += len(frames)
        for index, frame in enumerate(frames):
            field = f'{where}.frames[{index}]'
            mapping(frame, field)
            size = image(frame.get('file'))
            number(frame.get('durationMs'), field + '.durationMs', 10, 3_600_000)
            if 'rect' in frame:
                rect = array(frame['rect'], field + '.rect', 4)
                require(len(rect) == 4 and all(type(v) is int for v in rect), field, '图集区域需要四个整数')
                x, y, w, h = rect
                require(x >= 0 and y >= 0 and w > 0 and h > 0 and x+w <= size[0] and y+h <= size[1], field, '图集区域越界')
            if 'drawRect' in frame:
                rect = array(frame['drawRect'], field + '.drawRect', 4)
                require(len(rect) == 4, field, '绘制区域需要四个数值')
                for v in rect:
                    number(v, field + '.drawRect', -8192, 8192)
                require(rect[2] > 0 and rect[3] > 0, field, '绘制区域尺寸必须为正')
    require(total_frames <= 8192, 'animations', '总帧数超限')
    if 'pointerLook' in behavior:
        where = 'pointerLook'
        look = mapping(behavior['pointerLook'], where)
        require('input.pointer-look/v1' in required, where, '须声明 input.pointer-look/v1 能力')
        require(set(look) <= {'origin', 'states', 'directions', 'deadZone', 'maxDistance', 'idleTimeoutMs',
                             'smoothingMs', 'switchIntervalMs', 'hysteresisDegrees', 'resumeDelayMs'}, where, '未知字段')
        origin = array(look.get('origin'), where+'.origin', 2)
        require(len(origin) == 2, where, '注视原点须为画布坐标')
        number(origin[0], where+'.origin.x', 0, width)
        number(origin[1], where+'.origin.y', 0, height)
        look_states = array(look.get('states'), where+'.states', 128)
        require(bool(look_states) and all(isinstance(s, str) and s in states for s in look_states), where, '须引用可用状态')
        directions = array(look.get('directions'), where+'.directions', 32)
        require(bool(directions), where, '须提供至少一个方向')
        angles = set()
        for direction in directions:
            mapping(direction, where+'.direction')
            angle = number(direction.get('angle'), where+'.angle', 0, 359.999999)
            require(angle not in angles, where, '方向角度重复')
            angles.add(angle)
            animation = direction.get('animation')
            require(isinstance(animation, str) and animation in animations, where, '引用未知动画')
        for field, low, high in [('deadZone', 0, 1000), ('maxDistance', 1, 8192),
                ('idleTimeoutMs', 100, 60000), ('smoothingMs', 10, 1000),
                ('switchIntervalMs', 40, 1000), ('hysteresisDegrees', 0, 30), ('resumeDelayMs', 0, 3000)]:
            if field in look:
                number(look[field], where+'.'+field, low, high)
        require(look.get('deadZone', 20) < look.get('maxDistance', 1600), where, '注视距离须大于中心死区')
    def region_set(group, where):
        for key, region in mapping(group, where, 64).items():
            identifier(key, where)
            require(region.get('shape') in ('rect', 'ellipse'), where, '区域形状错误')
            rect = array(region.get('rect'), where + '.rect', 4)
            require(len(rect) == 4, where, '区域需要 x,y,width,height')
            x, y, w, h = [number(v, where, 0, 4096) for v in rect]
            require(w > 0 and h > 0 and x+w <= width and y+h <= height, where, '区域越过画布')
            number(region.get('priority', 0), where + '.priority', -1000, 1000)
    region_set(regions, 'regions')
    for key, state in states.items():
        require(state.get('animation') in animations, f'states.{key}.animation', '动画不存在')
        if 'regions' in state:
            region_set(state['regions'], f'states.{key}.regions')
    for key, animation in animations.items():
        if 'regions' in animation:
            region_set(animation['regions'], f'animations.{key}.regions')
    variables = mapping(behavior.get('variables', {}), 'variables', 32)
    for key, var in variables.items():
        identifier(key, 'variables')
        require(var.get('type') in ('boolean', 'number'), f'variables.{key}', '仅支持布尔或数值变量')
        if var['type'] == 'boolean':
            boolean(var.get('default'), f'variables.{key}.default')
        else:
            low = number(var.get('min', 0), f'variables.{key}.min', -1_000_000)
            high = number(var.get('max', 1_000_000), f'variables.{key}.max', low)
            number(var.get('default'), f'variables.{key}.default', low, high)
    def condition(value, where, depth=0):
        mapping(value, where)
        require(depth <= 6, where, '条件嵌套过深')
        require(set(value) <= {'stateIn', 'all', 'any', 'variable', 'op', 'value'}, where, '未知条件')
        if 'stateIn' in value:
            require(all(item in states for item in array(value['stateIn'], where)), where, '引用未知状态')
        for combiner in ('all', 'any'):
            if combiner in value:
                for entry in array(value[combiner], where, 16):
                    condition(entry, where, depth+1)
        if 'variable' in value:
            require(value['variable'] in variables and value.get('op', 'eq') in ('eq', 'ne', 'lt', 'le', 'gt', 'ge'), where, '变量或比较类型错误')
            variable = variables[value['variable']]
            if variable['type'] == 'boolean':
                boolean(value.get('value'), where + '.value')
            else:
                number(value.get('value'), where + '.value', -1_000_000)
    def invoke(value, where, depth=0):
        mapping(value, where)
        require(depth <= 6, where, '操作嵌套过深')
        kind = value.get('type')
        if kind in ('action', 'state'):
            require(value.get('id') in (actions if kind == 'action' else states), where + '.id', '目标不存在')
        elif kind == 'set':
            require(value.get('variable') in variables, where, '变量不存在')
            var = variables[value['variable']]
            if var['type'] == 'boolean':
                boolean(value.get('value'), where)
            else:
                number(value.get('value'), where, var.get('min', 0), var.get('max', 1_000_000))
        elif kind == 'random':
            choices = array(value.get('choices'), where, 16)
            require(bool(choices), where, '随机候选不能为空')
            for choice in choices:
                number(choice.get('weight', 1), where + '.weight', 0.001, 1000)
                invoke(choice.get('invoke'), where, depth+1)
        else:
            raise PackageError(f'{where}: 不支持的操作 {kind}')
    for key, action in actions.items():
        where = f'actions.{key}'
        require(action.get('animation') in animations, where + '.animation', '动画不存在')
        boolean(action.get('interruptible', True), where + '.interruptible')
        require(action.get('onBusy', 'discard') in ('enqueue', 'replace', 'discard'), where, '未知忙碌策略')
        number(action.get('priority', 0), where + '.priority', -1000, 1000)
        number(action.get('cooldownMs', 0), where + '.cooldownMs', 0, 3_600_000)
        number(action.get('queueTtlMs', 3000), where + '.queueTtlMs', 10, 60_000)
        if animations[action['animation']].get('loop'):
            number(action.get('maxDurationMs'), where + '.maxDurationMs', 10, 60_000)
        after = mapping(action.get('after'), where + '.after')
        require(after.get('type') in ('return', 'state'), where + '.after', '需要返回或切换状态')
        if after['type'] == 'state':
            require(after.get('id') in states, where + '.after.id', '状态不存在')
        if 'when' in action:
            condition(action['when'], where + '.when')
        if 'text' in action:
            text(action['text'], where + '.text')
        if 'gift' in action:
            require('render.gifts/v1' in required, where, '须声明 render.gifts/v1')
            gift = mapping(action['gift'], where+'.gift')
            require(set(gift) <= {'symbol', 'label', 'durationMs'}, where+'.gift', '未知字段')
            text(gift.get('symbol'), where+'.gift.symbol', 4)
            text(gift.get('label'), where+'.gift.label', 12)
            number(gift.get('durationMs', 15000), where+'.gift.durationMs', 1000, 30000)
        if 'sound' in action:
            path = package.asset(action['sound'])
            require(path.suffix.lower() == '.wav' and path.stat().st_size <= 5 * 1024**2, where + '.sound', '仅支持不超过 5 MiB 的 WAV')
        if 'motion' in action:
            require('desktop.motion/v1' in required and ('autonomy' in behavior or 'toys' in behavior),
                    where+'.motion', '须声明 desktop.motion/v1 和自主活动或玩具规则')
            motion = mapping(action['motion'], where+'.motion')
            require(set(motion) <= {'direction', 'speed', 'startMs', 'endMs'}, where+'.motion', '未知字段')
            require(type(motion.get('direction')) is int and motion['direction'] in (-1, 1), where+'.motion', '方向须为 -1 或 1')
            number(motion.get('speed'), where+'.motion.speed', 8, 240)
            start = number(motion.get('startMs', 0), where+'.motion.startMs', 0, 60000)
            animation = animations[action['animation']]
            duration = action.get('maxDurationMs') if animation.get('loop') else sum(frame['durationMs'] for frame in animation['frames'])
            end = number(motion.get('endMs'), where+'.motion.endMs', 1, duration)
            require(start < end, where+'.motion', '移动时间段须为正，且不能超过动作时长')
    if 'autonomy' in behavior:
        where = 'autonomy'
        require('behavior.autonomy/v1' in required, where, '须声明 behavior.autonomy/v1')
        autonomy = mapping(behavior['autonomy'], where)
        require(set(autonomy) <= {'states', 'activities', 'foot', 'exploration'}, where, '未知字段')
        allowed_states = array(autonomy.get('states'), where+'.states', 128)
        require(bool(allowed_states) and all(isinstance(s, str) and s in states for s in allowed_states), where, '须引用可用状态')
        foot = array(autonomy.get('foot', anchor), where+'.foot', 2)
        require(len(foot) == 2, where, '落脚点需要 x,y')
        number(foot[0], where+'.foot.x', 0, width)
        number(foot[1], where+'.foot.y', 0, height)
        activities = array(autonomy.get('activities'), where+'.activities', 32)
        require(bool(activities), where, '至少需要一个自主活动')
        seen = set()
        for activity in activities:
            mapping(activity, where+'.activity')
            require(set(activity) <= {'action', 'weight'}, where+'.activity', '未知字段')
            key = activity.get('action')
            require(isinstance(key, str) and key in actions and key not in seen, where, '动作不存在或重复')
            seen.add(key)
            number(activity.get('weight', 1), where+'.weight', .001, 1000)
        if 'exploration' in autonomy:
            require('desktop.explore/v1' in required, 'exploration', '须声明 desktop.explore/v1')
            explore = mapping(autonomy['exploration'], 'exploration')
            require(set(explore) <= {'observeAction', 'leftAction', 'rightAction', 'jumpAction', 'restAction',
                    'maxRise', 'maxDrop', 'maxDistance', 'jumpDurationMs'}, 'exploration', '未知字段')
            for field in ('observeAction', 'leftAction', 'rightAction', 'jumpAction', 'restAction'):
                key = explore.get(field)
                require(isinstance(key, str) and key in actions, 'exploration.'+field, '动作不存在')
                require(actions[key].get('onBusy', 'discard') == 'discard' and actions[key].get('interruptible', True),
                        'exploration.'+field, '须可打断且忙碌时丢弃')
            for field, direction in [('leftAction', -1), ('rightAction', 1)]:
                require(actions[explore[field]].get('motion', {}).get('direction') == direction,
                        'exploration.'+field, '须引用对应方向的位移动作')
            for field, default, low, high in [('maxRise', 220, 0, 400), ('maxDrop', 350, 0, 600),
                    ('maxDistance', 420, 30, 600), ('jumpDurationMs', 900, 300, 3000)]:
                number(explore.get(field, default), 'exploration.'+field, low, high)
            jump = actions[explore['jumpAction']]
            animation = animations[jump['animation']]
            duration = jump.get('maxDurationMs') if animation.get('loop') else sum(f['durationMs'] for f in animation['frames'])
            require(explore.get('jumpDurationMs', 900) <= duration, 'exploration', '跳跃位移不能超过动作时长')
    if 'toys' in behavior:
        toys = mapping(behavior['toys'], 'toys')
        require(set(toys) == {'ball'}, 'toys', '目前支持 ball')
        require('desktop.toys/v1' in required and 'desktop.motion/v1' in required,
                'toys', '须声明 desktop.toys/v1 和 desktop.motion/v1')
        ball = mapping(toys['ball'], 'toys.ball')
        require(set(ball) <= {'states', 'leftAction', 'rightAction', 'touchAction'}, 'toys.ball', '未知字段')
        toy_states = array(ball.get('states'), 'toys.ball.states', 128)
        require(bool(toy_states) and all(isinstance(s, str) and s in states for s in toy_states),
                'toys.ball.states', '须引用可用状态')
        for field, direction in [('leftAction', -1), ('rightAction', 1), ('touchAction', None)]:
            key = ball.get(field)
            require(isinstance(key, str) and key in actions, 'toys.ball.'+field, '动作不存在')
            action = actions[key]
            require(action.get('onBusy', 'discard') == 'discard' and action.get('interruptible', True),
                    'toys.ball.'+field, '玩具动作须可打断且忙碌时丢弃')
            if direction is not None:
                require(action.get('motion', {}).get('direction') == direction,
                        'toys.ball.'+field, '须引用对应方向的位移动作')
    rules = array(behavior.get('rules', []), 'rules', 128)
    ids = set()
    for rule in rules:
        key = identifier(rule.get('id'), 'rules.id')
        require(key not in ids, 'rules', '规则 ID 重复')
        ids.add(key)
        require(rule.get('event') in EVENTS, f'rules.{key}.event', '未知事件')
        if rule['event'].startswith('gesture.stroke'):
            require('input.stroke/v1' in required, key, '需声明 input.stroke/v1')
        if rule['event'].startswith('schedule.'):
            require('schedule.events/v1' in required, key, '需声明 schedule.events/v1')
            require('region' not in rule, key, '日程事件不包含鼠标区域')
        if rule['event'].startswith('focus.'):
            require('focus.events/v1' in required and 'region' not in rule, key, '须声明 focus.events/v1，且不能绑定鼠标区域')
        if 'region' in rule:
            all_regions = set(regions)
            for item in [*states.values(), *animations.values()]:
                all_regions.update(item.get('regions', {}))
            require(rule['region'] in all_regions, key, '区域不存在')
        if rule['event'] in ('timer', 'inactivity'):
            number(rule.get('afterMs'), key + '.afterMs', 50, 3_600_000)
            boolean(rule.get('repeat', False), key + '.repeat')
        number(rule.get('rulePriority', 0), key + '.rulePriority', -1000, 1000)
        condition(rule.get('when', {}), key + '.when')
        invoke(rule.get('then'), key + '.then')
    for group in ('menu', 'interactions'):
        seen = set()
        for item in array(behavior.get(group, []), group, 64):
            identifier(item.get('id'), group + '.id')
            require(item['id'] not in seen, group, '入口 ID 重复')
            seen.add(item['id'])
            text(item.get('label'), group + '.label', 64)
            number(item.get('order', 0), group + '.order', -1000, 1000)
            condition(item.get('when', {}), group + '.when')
            invoke(item.get('invoke'), group + '.invoke')
    entries = sorted(root.rglob('*'))
    require(len(entries) <= MAX_FILES, 'package', '文件数量超限')
    digest = hashlib.sha256()
    total = 0
    for path in entries:
        require(not path.is_symlink(), str(path), '禁止符号链接')
        if path.is_file():
            relative(path.relative_to(root).as_posix())
            require(path.suffix.lower() in ALLOWED_SUFFIXES, path.name, '包内包含不支持的文件类型')
            total += path.stat().st_size
            require(total <= MAX_BYTES, 'package', '解包大小超限')
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b'\0')
            file_digest = hashlib.sha256()
            with path.open('rb') as stream:
                while block := stream.read(1024 * 1024):
                    file_digest.update(block)
            digest.update(file_digest.digest())
    package.digest = digest.hexdigest()
    return package


def load_package(root):
    try:
        return _load_package(root)
    except (KeyError, TypeError, AttributeError, RecursionError) as error:
        raise PackageError(f'角色包字段结构不正确：{error}') from error


class PackageLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / 'catalog.json'
        self.index = read_json(self.index_path) if self.index_path.exists() else {'seeded': False, 'packages': {}}
        require(isinstance(self.index.get('packages'), dict), 'catalog', '角色目录损坏')

    def _save(self):
        temporary = self.index_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.index, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.index_path)

    def entries(self):
        return list(self.index['packages'].values())

    def path(self, identifier):
        item = self.index['packages'].get(identifier)
        require(item is not None, identifier, '角色未安装')
        path = self.root / relative(item['directory'])
        require(path.resolve().is_relative_to(self.root.resolve()) and path.is_dir(), identifier, '角色目录缺失')
        return path

    def load(self, identifier):
        return load_package(self.path(identifier))

    def install(self, archive):
        try:
            with TemporaryDirectory(prefix='.import-', dir=self.root) as temp:
                staging = Path(temp)
                with ZipFile(archive) as zipped:
                    entries = zipped.infolist()
                    require(len(entries) <= MAX_FILES, 'package', '文件数量超限')
                    require(sum(item.file_size for item in entries) <= MAX_BYTES, 'package', '解包大小超限')
                    seen = set()
                    for item in entries:
                        name = relative(item.filename.rstrip('/'))
                        require(name.casefold() not in seen, name, '重复或大小写冲突路径')
                        seen.add(name.casefold())
                        mode = item.external_attr >> 16
                        require(not stat.S_ISLNK(mode) and not item.flag_bits & 1, name, '不支持链接或加密文件')
                        target = staging / name
                        if item.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            require(target.suffix.lower() in ALLOWED_SUFFIXES, name, '包内包含不支持的文件类型')
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with zipped.open(item) as source, target.open('wb') as output:
                                copied = 0
                                while chunk := source.read(1024 * 1024):
                                    copied += len(chunk)
                                    require(copied <= item.file_size and copied <= MAX_BYTES, name, '解包大小不符')
                                    output.write(chunk)
                package = load_package(staging)
                old = self.index['packages'].get(package.id)
                if old and old['version'] == package.manifest['version']:
                    require(old['digest'] == package.digest, package.id, '同一版本内容不同，请修改版本号')
                    return self.load(package.id)
                directory = f'{package.id}/{package.manifest["version"]}-{package.digest[:16]}'
                destination = self.root / directory
                destination.parent.mkdir(parents=True, exist_ok=True)
                moved = False
                try:
                    if not destination.exists():
                        shutil.move(str(staging), str(destination))
                        moved = True
                    self.index['packages'][package.id] = {'id': package.id, 'displayName': package.name,
                        'author': package.manifest['author'], 'version': package.manifest['version'],
                        'digest': package.digest, 'directory': directory, 'thumbnail': package.manifest['thumbnail']}
                    self._save()
                except Exception:
                    if old is None:
                        self.index['packages'].pop(package.id, None)
                    else:
                        self.index['packages'][package.id] = old
                    if moved:
                        shutil.rmtree(destination)
                    raise
                package.root = destination
                return package
        except (BadZipFile, OSError, RuntimeError, RecursionError, TypeError, KeyError) as error:
            raise PackageError(f'无法导入角色包：{error}') from error

    def remove(self, identifier):
        directory = self.path(identifier)
        # Only remove the exact immutable installed version after checking its absolute path.
        require(directory.resolve().is_relative_to(self.root.resolve()), identifier, '目录越界')
        trash = self.root / ('.remove-' + uuid.uuid4().hex)
        directory.rename(trash)
        old = self.index['packages'].pop(identifier)
        try:
            self._save()
        except Exception:
            self.index['packages'][identifier] = old
            trash.rename(directory)
            raise
        shutil.rmtree(trash)
        # Old update versions are inert; remove their package directory as well.
        parent = directory.parent
        if parent.resolve().is_relative_to(self.root.resolve()) and parent != self.root:
            shutil.rmtree(parent, ignore_errors=True)

    def seed(self, directory):
        directory = Path(directory)
        descriptor = directory / 'bundle.json'
        bundle = read_json(descriptor) if descriptor.exists() else {}
        upgrades = mapping(bundle.get('upgradeFrom', {}), 'bundle.upgradeFrom')
        if 'seededPackages' not in self.index:
            # Older libraries only recorded one global boolean. Migration content
            # names belong to the bundle descriptor, never to runtime role logic.
            legacy = bundle.get('legacyPackageIds', [])
            self.index['seededPackages'] = list(legacy) if self.index.get('seeded') else []
        seen = set(self.index['seededPackages'])
        for archive in sorted(directory.glob('*.petpack')):
            with ZipFile(archive) as source:
                info = source.getinfo('manifest.json')
                require(info.file_size <= 2 * 1024**2, archive.name, '配置文件过大')
                package_id = identifier(json.loads(source.read(info).decode('utf-8'))['id'], 'id')
            old = self.index['packages'].get(package_id)
            # Only migrate exact contents previously shipped by this product; preserve custom imports.
            if old and old['digest'] in upgrades.get(package_id, []):
                self.install(archive)
            if package_id in seen:
                continue
            if package_id not in self.index['packages']:
                self.install(archive)
            seen.add(package_id)
            self.index['seededPackages'].append(package_id)
        self.index['seeded'] = True
        self._save()
