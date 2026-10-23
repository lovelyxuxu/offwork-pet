import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from PIL import Image


def create_pack(folder, identifier='test.cat', version='1.0.0', initial='napping', action='stretch',
                interaction='wake_up', mutate=None, static=False):
    folder = Path(folder)
    source = folder / (identifier + '-source')
    source.mkdir(exist_ok=True)
    Image.new('RGBA', (32, 32), (230, 160, 70, 255)).save(source / 'body.png')
    manifest = {'formatVersion': 1, 'id': identifier, 'version': version, 'displayName': identifier,
        'author': 'Test creator', 'requires': ['render.frames/v1', 'input.pointer/v1'],
        'thumbnail': 'body.png', 'canvas': {'width': 32, 'height': 32, 'anchor': [16, 32]},
        'behavior': 'behavior.json'}
    behavior = {'initialState': initial, 'recoveryState': initial,
        'animations': {'breath': {'loop': True, 'frames': [{'file': 'body.png', 'durationMs': 200}]},
                       'movement': {'loop': False, 'frames': [{'file': 'body.png', 'durationMs': 300}]}},
        'states': {initial: {'animation': 'breath'}},
        'actions': {action: {'animation': 'movement', 'priority': 1, 'interruptible': True,
                    'onBusy': 'enqueue', 'queueTtlMs': 1000, 'cooldownMs': 100,
                    'after': {'type': 'return'}}},
        'regions': {'ear': {'shape': 'rect', 'rect': [0, 0, 32, 32]}},
        'rules': [{'id': 'clicked_ear', 'event': 'pointer.click', 'region': 'ear',
                    'then': {'type': 'action', 'id': action}}],
        'menu': [{'id': 'menu', 'label': 'Stretch', 'invoke': {'type': 'action', 'id': action}}],
        'interactions': [{'id': interaction, 'label': 'Wake up', 'invoke': {'type': 'action', 'id': action}}]}
    if static:
        behavior.update(actions={}, rules=[], menu=[], interactions=[])
    if mutate:
        mutate(manifest, behavior)
    for name, data in [('manifest.json', manifest), ('behavior.json', behavior)]:
        (source / name).write_text(json.dumps(data), encoding='utf-8')
    archive = folder / (identifier + '-' + version + '.petpack')
    with ZipFile(archive, 'w', ZIP_DEFLATED) as zipped:
        for path in source.iterdir():
            zipped.write(path, path.name)
    return archive, source
