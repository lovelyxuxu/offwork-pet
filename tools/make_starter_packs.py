"""One-time conversion of the existing artwork into ordinary, removable packages.

Legacy character drawing is used only by this build tool, never by the package runtime.
"""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from offwork_pet.characters import draw_character


def build(character, output):
    is_lulu = character == 'lulu'
    state = 'standing' if is_lulu else 'flower_garden'
    # These names are authored content, not requirements on other packages.
    animations = {}
    actions = {}
    initial = 'soft_breath' if is_lulu else 'gentle_blink'
    response = 'lean_into_touch' if is_lulu else 'happy_wave'
    drag = 'sway_in_air' if is_lulu else 'swing_dress'
    clips = [(initial, '', 40, 10, False), (response, 'pet', 20, 16, False),
             (drag, 'drag', 16, 12, True), ('cheek_reply', 'receive_pinch', 16, 16, False),
             ('surprised_reply', 'receive_bonk', 16, 16, False)]
    with TemporaryDirectory() as temp:
        folder = Path(temp)
        (folder / 'assets').mkdir()
        for name, legacy, count, fps, loop in clips:
            atlas = QImage(220*8, 190*((count+7)//8), QImage.Format.Format_ARGB32)
            atlas.fill(Qt.GlobalColor.transparent)
            atlas_painter = QPainter(atlas)
            for index in range(count):
                image = QImage(280, 280, QImage.Format.Format_ARGB32)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                progress = index/max(1, count-1)
                draw_character(painter, character, phase=index*20/fps, gaze=QPointF(),
                    happy=legacy == 'pet', dragging=legacy == 'drag',
                    action=legacy if legacy not in ('pet', 'drag') else '', progress=progress,
                    reaction_progress=progress if legacy == 'pet' else None)
                painter.end()
                cropped = image.copy(QRect(40, 90, 220, 190))
                atlas_painter.drawImage((index % 8)*220, (index//8)*190, cropped)
                if name == initial and index == 0:
                    assert cropped.save(str(folder / 'thumbnail.png'))
            atlas_painter.end()
            file = f'assets/{name}.png'
            assert atlas.save(str(folder / file), 'PNG')
            animations[name] = {'loop': name == initial or loop, 'frames': [
                {'file': file, 'rect': [(i % 8)*220, (i//8)*190, 220, 190], 'durationMs': round(1000/fps * (1.3 if name == initial else 1.4))}
                for i in range(count)]}
            if name != initial:
                actions[name] = {'animation': name, 'priority': 30 if name == drag else 20,
                    'interruptible': True, 'onBusy': 'replace', 'cooldownMs': 650,
                    'after': {'type': 'return'}}
                if loop:
                    actions[name]['maxDurationMs'] = 1500
        manifest = {'formatVersion': 1, 'id': character, 'version': '1.2.0',
            'displayName': '噜噜（3D 动画）' if is_lulu else '噜妹（二维）',
            'author': 'Offwork Pet / Spencer0131' if is_lulu else 'Offwork Pet',
            'minEngineVersion': '1.0.0', 'requires': ['render.frames/v1', 'input.pointer/v1', 'input.stroke/v1', 'behavior.autonomy/v1'],
            'thumbnail': 'thumbnail.png', 'canvas': {'width': 220, 'height': 190, 'anchor': [100, 174]},
            'behavior': 'behavior.json', 'credits': [{'author': 'Spencer0131' if is_lulu else 'Offwork Pet',
                'source': 'https://sketchfab.com/3d-models/a5747f74f0034470a2029a85f6e9c11d' if is_lulu else 'docs/形象来源.md',
                'license': 'CC-BY-4.0' if is_lulu else '同人适配，参见项目 THIRD_PARTY_NOTICES.md',
                'changes': '预渲染动画导出为标准角色包' if is_lulu else '将既有 Qt 二维绘制导出为标准帧图集'}]}
        behavior = {'initialState': state, 'recoveryState': state,
            'autonomy': {'states': [state], 'foot': [100, 174], 'activities': [{'action': response, 'weight': 1}]},
            'animations': animations, 'states': {state: {'animation': initial}}, 'actions': actions,
            'regions': {'crown': {'shape': 'ellipse', 'rect': [30, 0, 135, 125]}},
            'rules': [{'id': 'click_response', 'event': 'pointer.click', 'then': {'type': 'action', 'id': response}},
                      {'id': 'stroke_response', 'event': 'gesture.stroke', 'region': 'crown', 'then': {'type': 'action', 'id': response}},
                      {'id': 'drag_response', 'event': 'drag.start', 'then': {'type': 'action', 'id': drag}}],
            'menu': [{'id': 'greet', 'label': '开心挥手', 'invoke': {'type': 'action', 'id': response}}],
            'interactions': [{'id': 'pinch', 'label': '捏捏脸', 'invoke': {'type': 'action', 'id': 'cheek_reply'}},
                             {'id': 'bonk', 'label': '轻捶一下', 'invoke': {'type': 'action', 'id': 'surprised_reply'}}]}
        for filename, value in [('manifest.json', manifest), ('behavior.json', behavior)]:
            (folder / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        output.mkdir(parents=True, exist_ok=True)
        target = output / f'{character}.petpack'
        with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
            for file in sorted(folder.rglob('*')):
                if file.is_file():
                    archive.write(file, file.relative_to(folder).as_posix())
        print(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'offwork_pet/assets/starter-packs')
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    for character in ('lulu', 'lumei'):
        build(character, args.output)
