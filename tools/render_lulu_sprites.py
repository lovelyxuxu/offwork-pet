"""Render the approved v5 model to transparent desktop frames using Blender.

blender --background --python tools/render_lulu_sprites.py -- --preview
blender --background --python tools/render_lulu_sprites.py
The .blend and original supplied assets are read without modification.
"""
import argparse
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'offwork_pet' / 'assets' / 'lulu3d'
CLIPS = {'idle': (40, 10), 'pet': (20, 16), 'drag': (16, 12),
         'receive_pinch': (16, 16), 'receive_bonk': (16, 16),
         'send_pinch': (16, 16), 'send_bonk': (16, 16)}
parser = argparse.ArgumentParser()
parser.add_argument('--preview', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])

bpy.ops.wm.open_mainfile(filepath=str(ROOT / 'outputs/lulu-model-v5-reference-face/lulu-reference-face.blend'))
scene = bpy.context.scene
rig = bpy.data.objects['Lulu_sample_rig']
rig.animation_data_clear()
head, body = rig.pose.bones['Head'], rig.pose.bones['Body']
head.rotation_mode = body.rotation_mode = rig.rotation_mode = 'XYZ'
head.rotation_euler = body.rotation_euler = rig.rotation_euler = (0, 0, 0)
head.location = body.location = rig.location = (0, 0, 0)
head.scale = body.scale = rig.scale = (1, 1, 1)
eyes = bpy.data.objects['Reference_round_eyes_v5']
eyes.shape_key_add(name='Basis')
blink = eyes.shape_key_add(name='Blink')
design = json.loads((ROOT / 'outputs/lulu-model-v5-reference-face/design.json').read_text())
for point in blink.data:
    center = min(design['eye_centers'], key=lambda c: abs(point.co.x - c[0]))
    point.co.z = center[1] + (point.co.z - center[1]) * .09
    point.co.y -= .0015

scene.render.engine = 'CYCLES'
scene.cycles.samples = 12
scene.cycles.use_denoising = True
scene.render.use_persistent_data = True
scene.render.resolution_x, scene.render.resolution_y = 320, 400
scene.render.resolution_percentage = 100
scene.render.film_transparent = True
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.image_settings.color_depth = '8'
scene.render.image_settings.compression = 70
# Do not embed local project paths or render details in distributed PNG frames.
scene.render.use_stamp = False
scene.render.use_stamp_note = False
scene.render.use_stamp_filename = False
scene.render.use_stamp_date = False
scene.render.use_stamp_time = False
scene.render.use_stamp_render_time = False
scene.render.use_stamp_frame = False
scene.render.use_stamp_camera = False
scene.render.use_stamp_scene = False
scene.render.use_stamp_memory = False
camera = scene.camera
camera.location = (0, -3, .70)
camera.rotation_euler = (Vector((0, 0, .52)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
camera.data.ortho_scale = 1.30

try:
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.compute_device_type = 'CUDA'
    prefs.get_devices()
    for device in prefs.devices:
        device.use = device.type == 'CUDA'
    if any(device.use for device in prefs.devices):
        scene.cycles.device = 'GPU'
except Exception:
    scene.cycles.device = 'CPU'


def pose(clip, index, count):
    t = index / count
    wave = math.sin(math.tau * t)
    envelope = math.sin(math.pi * index / max(1, count - 1)) ** 2
    head.rotation_euler = (0, 0, 0)
    head.scale = body.scale = rig.scale = (1, 1, 1)
    head.location = body.location = rig.location = (0, 0, 0)
    rig.rotation_euler = (0, 0, 0)
    body.rotation_euler = (0, 0, 0)
    blink.value = 0
    if clip == 'idle':
        body.scale = (1 + .006 * wave, 1 + .006 * wave, 1 + .009 * wave)
        head.rotation_euler.y = math.radians(1.2) * wave
        blink.value = max(0, 1 - abs(index - 29) / 1.5)
    elif clip == 'pet':
        head.rotation_euler.y = math.radians(9) * math.sin(math.tau * t) * envelope
        head.rotation_euler.x = math.radians(-5) * envelope
        body.location.y = .012 * envelope
        blink.value = .7 * envelope
    elif clip == 'drag':
        rig.rotation_euler.y = math.radians(4) * wave
        head.rotation_euler.y = math.radians(-3) * wave
    elif clip == 'receive_pinch':
        head.scale = (1 + .055 * envelope, 1, 1 - .025 * envelope)
        head.rotation_euler.y = math.radians(3) * wave * envelope
        blink.value = .85 * envelope
    elif clip == 'receive_bonk':
        head.rotation_euler.x = math.radians(9) * envelope
        head.rotation_euler.y = math.radians(6) * math.sin(math.tau * 2 * t) * envelope
        body.location.y = -.012 * envelope
        blink.value = envelope
    elif clip == 'send_pinch':
        head.rotation_euler.z = math.radians(-7) * envelope
        head.rotation_euler.x = math.radians(-3) * envelope
        rig.rotation_euler.z = math.radians(-4) * envelope
    elif clip == 'send_bonk':
        head.rotation_euler.x = math.radians(-8) * math.sin(math.tau * t) * envelope
        body.location.y = .015 * envelope
    bpy.context.view_layer.update()


OUT.mkdir(parents=True, exist_ok=True)
for clip, (count, fps) in CLIPS.items():
    if args.preview and clip != 'idle':
        continue
    folder = OUT / clip
    folder.mkdir(exist_ok=True)
    for index in range(1 if args.preview else count):
        target = folder / f'{index:03d}.png'
        if target.exists() and not args.preview:
            continue
        pose(clip, index, count)
        scene.render.filepath = str(target)
        bpy.ops.render.render(write_still=True)
        print(f'SPRITE {clip} {index + 1}/{count}', flush=True)

if not args.preview:
    (OUT / 'manifest.json').write_text(json.dumps({
        'format': 1, 'size': [320, 400], 'draw_rect': [66, 91, 148, 185],
        'clips': {name: {'frames': count, 'fps': fps} for name, (count, fps) in CLIPS.items()},
        'source': 'https://sketchfab.com/3d-models/a5747f74f0034470a2029a85f6e9c11d',
        'author': 'Spencer0131', 'license': 'CC-BY-4.0',
        'changes': 'v5 reference face, colors, optimized surface, head/body animation and transparent rendering',
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print('LULU_SPRITES_COMPLETE', flush=True)
