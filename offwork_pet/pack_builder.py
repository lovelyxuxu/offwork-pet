"""Create, validate, export and preview data-only character packages."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

from PIL import Image, ImageSequence
from .packages import PackageLibrary, PackageError, load_package, identifier


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def frames_from_image(path, output, prefix):
    identifier(prefix, 'animation')
    frames = []
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as source:
        if (source.width * source.height * 4 > 32 * 1024**2 or getattr(source, 'n_frames', 1) > 512
                or source.width * source.height * 4 * getattr(source, 'n_frames', 1) > 256 * 1024**2):
            raise PackageError('图片尺寸或帧数超出制作工具限制')
        for index, frame in enumerate(ImageSequence.Iterator(source)):
            file = f'{prefix}-{index:03d}.png'
            frame.convert('RGBA').save(output / file)
            frames.append({'file': f'assets/{file}', 'durationMs': max(10, int(frame.info.get('duration', 1000 if getattr(source, 'n_frames', 1) == 1 else 100)))})
        return frames, source.size


def make_template(args):
    identifier(args.id, 'id')
    output = args.output.resolve()
    if output.exists():
        raise PackageError('目标目录已存在，请选择新目录')
    output.mkdir(parents=True)
    try:
        frames, (width, height) = frames_from_image(args.image, output / 'assets', 'rest')
        with Image.open(output / frames[0]['file']) as first:
            thumbnail = first.copy()
            thumbnail.thumbnail((256, 256))
            thumbnail.save(output / 'thumbnail.png')
        manifest = {'formatVersion': 1, 'id': args.id, 'version': '1.0.0', 'displayName': args.name,
            'author': args.author, 'requires': ['render.frames/v1', 'input.pointer/v1'],
            'thumbnail': 'thumbnail.png', 'canvas': {'width': width, 'height': height, 'anchor': [width/2, height]},
            'behavior': 'behavior.json', 'credits': [{'author': args.author, 'source': args.image.name, 'license': args.license}]}
        behavior = {'initialState': 'at_home', 'recoveryState': 'at_home',
            'animations': {'rest': {'loop': True, 'frames': frames}},
            'states': {'at_home': {'animation': 'rest'}}, 'actions': {}, 'regions': {},
            'rules': [], 'menu': [], 'interactions': []}
        write_json(output / 'manifest.json', manifest)
        write_json(output / 'behavior.json', behavior)
        load_package(output)
    except Exception:
        # This directory was just created above and is entirely owned by this command.
        shutil.rmtree(output)
        raise
    print(f'已创建角色源目录：{output}')


def add_action(args):
    package = load_package(args.folder)
    identifier(args.id, 'action')
    if args.id in package.behavior['actions'] or args.id in package.behavior['animations']:
        raise PackageError('动作标识已存在，请使用新的标识')
    # Build in a separate directory so a failed conversion cannot corrupt the source.
    with TemporaryDirectory() as temp:
        working = Path(temp) / 'package'
        shutil.copytree(package.root, working)
        frames, size = frames_from_image(args.image, working / 'assets', args.id)
        canvas = package.manifest['canvas']
        if size != (canvas['width'], canvas['height']):
            raise PackageError('动作画布尺寸须与角色画布一致，请先对齐素材')
        if any((package.root / frame['file']).exists() for frame in frames):
            raise PackageError('新动作文件名与现有素材冲突，请更换动作标识')
        data = package.behavior
        data['animations'][args.id] = {'loop': False, 'frames': frames}
        data['actions'][args.id] = {'animation': args.id, 'priority': 10, 'interruptible': True,
            'onBusy': 'enqueue', 'cooldownMs': 500, 'queueTtlMs': 3000, 'after': {'type': 'return'}}
        entry = {'id': args.id, 'label': args.label, 'invoke': {'type': 'action', 'id': args.id}}
        data.setdefault('menu', []).append(entry)
        if args.remote:
            data.setdefault('interactions', []).append(entry)
        write_json(working / 'behavior.json', data)
        load_package(working)
        for frame in frames:
            shutil.copy2(working / frame['file'], package.root / frame['file'])
        temporary = package.root / 'behavior.json.tmp'
        shutil.copy2(working / 'behavior.json', temporary)
        temporary.replace(package.root / 'behavior.json')
    print('已添加动作；发布更新前请增加 manifest.json 中的 version。')
