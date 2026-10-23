"""Apply an author's explicit schedule/ball recipe to a local character package."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from offwork_pet.packages import PackageLibrary


def add_features(archive, recipe, bundle=None):
    archive = Path(archive)
    with TemporaryDirectory() as temp:
        library = PackageLibrary(Path(temp)/'validation')
        old = library.install(archive)
        manifest, behavior = deepcopy(old.manifest), deepcopy(old.behavior)
        manifest['version'] = recipe['version']
        required = set(manifest.get('requires', []))
        required.add('schedule.events/v1')
        for event, definition in recipe['schedule'].items():
            key = 'ritual_'+event.rsplit('.', 1)[-1]
            source = behavior['actions'][definition['action']]
            action = {**deepcopy(source), 'text': definition['text'], 'priority': 0,
                      'onBusy': 'discard', 'interruptible': True, 'cooldownMs': 1000,
                      'after': {'type': 'return'}}
            action.pop('motion', None)
            behavior['actions'][key] = action
            rules = behavior.setdefault('rules', [])
            rules[:] = [rule for rule in rules if rule['id'] != key]
            rules.append({'id': key, 'event': event, 'then': {'type': 'action', 'id': key}})
        if 'ball' in recipe:
            required.update(('desktop.toys/v1', 'desktop.motion/v1'))
            ball = recipe['ball']
            spec = {'states': ball['states']}
            for field in ('leftAction', 'rightAction', 'touchAction'):
                key = 'ball_'+field
                action = deepcopy(behavior['actions'][ball[field]])
                action.update(onBusy='discard', interruptible=True, cooldownMs=0,
                              priority=0, after={'type': 'return'})
                behavior['actions'][key] = action
                spec[field] = key
            behavior.setdefault('toys', {})['ball'] = spec
        if 'exploration' in recipe:
            required.add('desktop.explore/v1')
            spec = {}
            for field in ('observeAction', 'leftAction', 'rightAction', 'jumpAction', 'restAction'):
                key = 'explore_'+field
                action = deepcopy(behavior['actions'][recipe['exploration'][field]])
                action.update(onBusy='discard', interruptible=True, cooldownMs=0, priority=0, after={'type': 'return'})
                behavior['actions'][key] = action
                spec[field] = key
            spec['jumpDurationMs'] = recipe['exploration'].get('jumpDurationMs', 900)
            behavior['autonomy']['exploration'] = spec
        if 'focus' in recipe:
            required.add('focus.events/v1')
            key = 'focus_complete'
            behavior['actions'][key] = {**deepcopy(behavior['actions'][recipe['focus']['action']]),
                'text': recipe['focus']['text'], 'onBusy': 'discard', 'after': {'type': 'return'}}
            behavior['rules'] = [r for r in behavior['rules'] if r['id'] != key]
            behavior['rules'].append({'id': key, 'event': 'focus.finished', 'then': {'type': 'action', 'id': key}})
        if 'gifts' in recipe:
            required.add('render.gifts/v1')
            for gift in recipe['gifts']:
                key = gift['id']
                behavior['actions'][key] = {**deepcopy(behavior['actions'][gift['action']]),
                    'text': gift['text'], 'gift': gift['display'], 'after': {'type': 'return'}}
                behavior['interactions'] = [item for item in behavior['interactions'] if item['id'] != key]
                behavior['interactions'].append({'id': key, 'label': gift['label'], 'invoke': {'type': 'action', 'id': key}})
        manifest['requires'] = sorted(required)
        note = ' 复用已有动画，增加下班日程反应与可选玩球声明；未生成新姿态。'
        if manifest.get('credits') and note not in manifest['credits'][0].get('changes', ''):
            manifest['credits'][0]['changes'] = manifest['credits'][0].get('changes', '')+note
        if manifest == old.manifest and behavior == old.behavior:
            return False
        staged = Path(temp)/'updated.petpack'
        with ZipFile(archive) as source, ZipFile(staged, 'w', ZIP_DEFLATED) as target:
            for item in source.infolist():
                value = (manifest if item.filename == 'manifest.json' else
                         behavior if item.filename == manifest['behavior'] else None)
                payload = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8') if value is not None else source.read(item)
                target.writestr(item, payload)
        PackageLibrary(Path(temp)/'updated-validation').install(staged)
        if bundle:
            bundle = Path(bundle)
            descriptor = json.loads(bundle.read_text(encoding='utf-8'))
            history = descriptor.setdefault('upgradeFrom', {}).setdefault(old.id, [])
            if old.digest not in history:
                history.append(old.digest)
            bundle.write_text(json.dumps(descriptor, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        replacement = archive.with_suffix('.petpack.tmp')
        replacement.write_bytes(staged.read_bytes())
        replacement.replace(archive)
        return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('recipe', type=Path)
    parser.add_argument('--bundle', type=Path)
    args = parser.parse_args()
    recipe = json.loads(args.recipe.read_text(encoding='utf-8'))
    print('Updated' if add_features(args.archive, recipe, args.bundle) else 'Already current')
