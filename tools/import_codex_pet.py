"""Convert a local Codex pet archive into a standard Offwork Pet character pack.

Only this adapter knows the source format's fixed row layout. The product runtime
continues to use explicit animations and declarative rules.
"""
import argparse
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from offwork_pet.packages import PackageError, PackageLibrary, identifier

ROWS = [('rest', '静静陪伴', 200), ('run_right', '向右跑两步', 155),
        ('run_left', '向左跑两步', 155), ('wave', '挥挥手', 280),
        ('jump', '开心跳一下', 240), ('downcast', '低落一下', 240),
        ('waiting', '耐心等待', 300), ('thinking', '认真思考', 280),
        ('look_around', '歪头看看', 260)]


def recipe_behavior(atlas, recipe, autonomy=False):
    """Apply a creator's explicit row/meaning mapping outside the product runtime."""
    def frames(row, count, duration):
        if type(row) is not int or not 0 <= row < atlas.height // 208 or not 1 <= count <= 8:
            raise PackageError('适配配置的行或帧数不正确')
        result = []
        for col in range(count):
            if not atlas.crop((col*192, row*208, (col+1)*192, (row+1)*208)).getchannel('A').getbbox():
                raise PackageError('适配配置引用了空白帧')
            result.append({'file': 'assets/spritesheet.webp', 'rect': [col*192, row*208, 192, 208],
                           'durationMs': duration})
        return result

    base = recipe['base']
    base_frames = frames(base['row'], base['count'], base['durationMs'])
    base_frames[0]['durationMs'] = base['firstHoldMs']
    base_frames[-1]['durationMs'] = base['lastHoldMs']
    animations = {'base': {'loop': True, 'frames': base_frames}}
    actions, menu, interactions = {}, [], []
    for index, item in enumerate(recipe['actions']):
        key = identifier(item['id'], 'action.id')
        if key in animations:
            raise PackageError('适配配置存在重复动作')
        repeat = item.get('repeat', recipe.get('actionRepeat', 1))
        if type(repeat) is not int or not 1 <= repeat <= 8:
            raise PackageError('适配配置的动作重复次数必须为 1–8')
        source_frames = frames(item['row'], item['count'], item['durationMs'])
        action_frames = [dict(frame) for _ in range(repeat) for frame in source_frames]
        action_frames[0]['durationMs'] += 120
        action_frames[-1]['durationMs'] += 240
        action_frames.append({**base_frames[0], 'durationMs': 320})
        animations[key] = {'loop': False, 'frames': action_frames}
        actions[key] = {'animation': key, 'priority': 10, 'interruptible': True,
                        'onBusy': 'enqueue', 'queueTtlMs': recipe.get('queueTtlMs', 3500), 'cooldownMs': 1000,
                        'after': {'type': 'return'}}
        if autonomy and item.get('motion'):
            actions[key]['motion'] = {**item['motion'], 'startMs': 120,
                'endMs': sum(frame['durationMs'] for frame in action_frames[:-1])-240}
        invoke = {'type': 'action', 'id': key}
        menu.append({'id': key, 'label': item['label'], 'order': index, 'invoke': invoke})
        if item.get('remoteLabel'):
            interactions.append({'id': key, 'label': item['remoteLabel'], 'order': index, 'invoke': invoke})
    touch = recipe['touchAction']
    if touch not in actions:
        raise PackageError('抚摸动作不存在')
    rules = [{'id': 'click_company', 'event': 'pointer.click', 'then': {'type': 'action', 'id': touch}},
             {'id': 'stroke_crown', 'event': 'gesture.stroke', 'region': 'crown', 'then': {'type': 'action', 'id': touch}},
             {'id': 'hold_crown', 'event': 'pointer.hold', 'region': 'crown', 'then': {'type': 'action', 'id': touch}}]
    behavior = {'initialState': 'company', 'recoveryState': 'company', 'maxQueuedActions': 1,
            'animations': animations, 'actions': actions, 'states': {'company': {'animation': 'base'}},
            'regions': {'crown': {'shape': 'ellipse', 'rect': recipe['headRegion']}},
            'rules': rules, 'menu': menu, 'interactions': interactions}
    if autonomy:
        behavior['autonomy'] = {'states': ['company'], 'foot': [96, 198], 'activities': [
            {'action': item['id'], 'weight': item['autonomousWeight']} for item in recipe['actions']
            if item.get('autonomousWeight')]}
    return behavior


def write_pack(image_bytes, atlas, output, manifest, behavior, thumbnail_rect):
    with TemporaryDirectory() as temp:
        folder = Path(temp) / 'source'
        (folder / 'assets').mkdir(parents=True)
        (folder / 'assets/spritesheet.webp').write_bytes(image_bytes)
        atlas.crop(thumbnail_rect).save(folder / 'thumbnail.png')
        for filename, data in [('manifest.json', manifest), ('behavior.json', behavior)]:
            (folder / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(temp) / 'converted.petpack'
        with ZipFile(staging, 'w', ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(folder).as_posix())
        PackageLibrary(Path(temp) / 'validation').install(staging)
        output.write_bytes(staging.read_bytes())
    print(f"Converted {manifest['id']}: {len(behavior['actions'])} actions -> {output}")


def convert(source, output, package_id, name, author, source_url, recipe=None, pointer_look=False, autonomy=False):
    identifier(package_id, 'id')
    with ZipFile(source) as original:
        # No extraction of arbitrary archive members or execution of source content.
        for filename in ('pet.json', 'spritesheet.webp'):
            info = original.getinfo(filename)
            if info.file_size > 16 * 1024**2:
                raise PackageError('源素材文件过大')
        metadata = json.loads(original.read('pet.json').decode('utf-8-sig'))
        image_bytes = original.read('spritesheet.webp')
    version = metadata.get('spriteVersionNumber', 1)
    if pointer_look and (version != 2 or recipe is not None):
        raise PackageError('鼠标朝向适配需要带 16 个朝向的 v2 标准图集')
    with Image.open(io.BytesIO(image_bytes)) as source_image:
        if source_image.size != (1536, 2288 if version == 2 else 1872):
            raise PackageError('不支持的 Codex 宠物图集尺寸')
        atlas = source_image.convert('RGBA')
    animations, actions, menu, interactions = {}, {}, [], []
    for row, (key, label, duration) in enumerate(ROWS):
        columns = 6 if row == 0 and version == 2 else 8
        frames = []
        for col in range(columns):
            rect = (col*192, row*208, (col+1)*192, (row+1)*208)
            if atlas.crop(rect).getchannel('A').getbbox():
                frames.append({'file': 'assets/spritesheet.webp',
                    'rect': [col*192, row*208, 192, 208], 'durationMs': duration})
        if not frames:
            continue
        if row == 0:
            # Stay in the neutral pose; blink briefly instead of blinking every 1.5 seconds.
            frames[0]['durationMs'] = 3400
            frames[-1]['durationMs'] = 1200
        else:
            frames[0]['durationMs'] += 100
            frames[-1]['durationMs'] += 220
            frames.append({**animations['rest']['frames'][0], 'durationMs': 280})
            if key == 'jump' and len(frames) == 6:
                # A brief takeoff, readable apex and quieter landing, then settle.
                for frame, ms in zip(frames, (220, 180, 300, 220, 360, 280)):
                    frame['durationMs'] = ms
        animations[key] = {'loop': row == 0, 'frames': frames}
        if row:
            actions[key] = {'animation': key, 'priority': 10, 'interruptible': True,
                'onBusy': 'enqueue', 'queueTtlMs': 3000, 'cooldownMs': 800,
                'after': {'type': 'return'}}
            menu.append({'id': key, 'label': label, 'order': row,
                         'invoke': {'type': 'action', 'id': key}})
    if 'rest' not in animations:
        raise PackageError('源格式缺少可用的基础画面')
    # Reuse source eye-closing frames; do not claim to generate a new rubbing animation.
    blink_frames = animations['rest']['frames'][2:5] or animations['rest']['frames']
    touch_frames = [{**frame, 'durationMs': duration} for frame, duration in zip(blink_frames, (180, 520, 220))]
    touch_frames.append({**animations['rest']['frames'][0], 'durationMs': 320})
    animations['close_eyes'] = {'loop': False, 'frames': touch_frames}
    actions['close_eyes'] = {'animation': 'close_eyes', 'priority': 30, 'interruptible': True,
        'onBusy': 'enqueue', 'queueTtlMs': 2500, 'cooldownMs': 1200, 'after': {'type': 'return'}}
    if version == 2:
        # Source row semantics stay in this converter; the runtime uses explicit poses.
        for index in range(16):
            animations[f'facing_{index:02d}'] = {'loop': False, 'frames': [{
                'file': 'assets/spritesheet.webp', 'rect': [(index % 8)*192, (9+index//8)*208, 192, 208],
                'durationMs': 500}]}
        if pointer_look:
            for key, row, label in [('look_right', 9, '向右张望'), ('look_left', 10, '向左张望')]:
                animations[key] = {'loop': False, 'frames': [
                    {**animations['rest']['frames'][0], 'durationMs': 140},
                    *[{'file': 'assets/spritesheet.webp', 'rect': [col*192, row*208, 192, 208],
                       'durationMs': 160} for col in range(8)],
                    {**animations['rest']['frames'][0], 'durationMs': 300}]}
                actions[key] = {'animation': key, 'priority': 10, 'interruptible': True,
                    'onBusy': 'enqueue', 'queueTtlMs': 3000, 'cooldownMs': 1000, 'after': {'type': 'return'}}
                menu.append({'id': key, 'label': label, 'order': row, 'invoke': {'type': 'action', 'id': key}})
    for key, label in [('wave', '打个招呼'), ('jump', '给你打气'), ('thinking', '一起想想'), ('close_eyes', '摸摸头')]:
        if key in actions:
            interactions.append({'id': key, 'label': label, 'invoke': {'type': 'action', 'id': key}})
    rules = [{'id': 'stroke_crown', 'event': 'gesture.stroke', 'region': 'crown',
              'then': {'type': 'action', 'id': 'close_eyes'}},
             {'id': 'hold_crown', 'event': 'pointer.hold', 'region': 'crown',
              'then': {'type': 'action', 'id': 'close_eyes'}}]
    if 'wave' in actions:
        rules.append({'id': 'click_hello', 'event': 'pointer.click', 'then': {'type': 'action', 'id': 'wave'}})
    manifest = {'formatVersion': 1, 'id': package_id, 'version': '1.1.0', 'displayName': name,
        'author': author, 'description': metadata.get('description', ''),
        'requires': ['render.frames/v1', 'input.pointer/v1', 'input.stroke/v1'],
        'thumbnail': 'thumbnail.png', 'canvas': {'width': 192, 'height': 208, 'anchor': [96, 198]},
        'behavior': 'behavior.json', 'credits': [{'author': author, 'source': source_url,
            'license': '原页面与源包未提供具体素材许可证；保留原作者及来源，未声明为项目 MIT 授权。',
            'changes': '原始图集不变；添加显式帧位置、时长、菜单和互动规则，封面取原图首帧。'}]}
    behavior = {'initialState': 'calm_company', 'recoveryState': 'calm_company',
        'animations': animations, 'actions': actions, 'maxQueuedActions': 1,
        'states': {'calm_company': {'animation': 'rest'}},
        'regions': {'crown': {'shape': 'ellipse', 'rect': [32, 4, 128, 100]}},
        'rules': rules, 'menu': menu, 'interactions': interactions}
    if pointer_look:
        manifest['version'] = '1.2.0'
        manifest['requires'].append('input.pointer-look/v1')
        manifest['credits'][0]['changes'] += ' 启用原图的 16 向鼠标跟随和左右张望入口。'
        behavior['pointerLook'] = {'origin': [96, 72], 'states': ['calm_company'],
            'directions': [{'angle': index*22.5, 'animation': f'facing_{index:02d}'} for index in range(16)],
            'deadZone': 20, 'maxDistance': 1600, 'idleTimeoutMs': 3000,
            'smoothingMs': 120, 'switchIntervalMs': 100, 'hysteresisDegrees': 5, 'resumeDelayMs': 350}
    thumbnail_rect = (0, 0, 192, 208)
    if recipe is not None:
        behavior = recipe_behavior(atlas, recipe, autonomy)
        manifest['description'] = recipe.get('description', manifest['description'])
        manifest['credits'][0]['changes'] = '原始图集不变；按实际画面逐行配置动作名称、节奏、菜单和互动；封面取工作画面。'
        row = recipe['base']['row']
        thumbnail_rect = (0, row*208, 192, (row+1)*208)
    elif autonomy:
        activities = [{'action': key, 'weight': weight} for key, weight in
                      [('thinking', 2), ('waiting', 2), ('look_around', 3), ('wave', 1)] if key in actions]
        for source_key, key, direction in [('run_left', 'amble_left', -1), ('run_right', 'amble_right', 1)]:
            if source_key not in animations:
                continue
            # Use only the authored running frames, without the appended neutral pose.
            frames = [{**frame, 'durationMs': 155} for frame in animations[source_key]['frames'][:-1]]
            duration = sum(frame['durationMs'] for frame in frames)
            animations[key] = {'loop': False, 'frames': frames}
            actions[key] = {'animation': key, 'interruptible': True, 'onBusy': 'discard',
                'cooldownMs': 3000, 'after': {'type': 'return'},
                'motion': {'direction': direction, 'speed': 85, 'startMs': 100, 'endMs': duration-100}}
            activities.append({'action': key, 'weight': 2})
        behavior['autonomy'] = {'states': ['calm_company'], 'foot': [96, 198], 'activities': activities}
    if autonomy:
        manifest['version'] = '1.3.0'
        manifest['requires'].append('behavior.autonomy/v1')
        if any(action.get('motion') for action in behavior['actions'].values()):
            manifest['requires'].append('desktop.motion/v1')
        manifest['credits'][0]['changes'] += ' 添加包内自主活动权重、落脚点及行走位移声明，原始素材不变。'
    if recipe is not None and 'version' in recipe:
        manifest['version'] = recipe['version']
    write_pack(image_bytes, atlas, output, manifest, behavior, thumbnail_rect)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--id', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--author', required=True)
    parser.add_argument('--source-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recipe', type=Path, help='JSON row/label/interaction mapping for this source character')
    parser.add_argument('--pointer-look', action='store_true', help='Enable authored v2 directions and look-around menu entries')
    parser.add_argument('--autonomy', action='store_true', help='Declare autonomous activities and authored walking motion')
    args = parser.parse_args()
    recipe = json.loads(args.recipe.read_text(encoding='utf-8')) if args.recipe else None
    convert(args.source, args.output, args.id, args.name, args.author, args.source_url, recipe, args.pointer_look, args.autonomy)
