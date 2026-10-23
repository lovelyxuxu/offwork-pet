from copy import deepcopy
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from offwork_pet.behavior import BehaviorRuntime
from offwork_pet.packages import PackageError, PackageLibrary
from offwork_pet.pointer_look import PointerLook, angular_distance
from pack_fixtures import create_pack


def specification():
    return {'origin': [16, 16], 'states': ['napping'], 'directions': [
        {'angle': angle, 'animation': 'pose'+str(angle)} for angle in range(0, 360, 90)],
        'deadZone': 5, 'idleTimeoutMs': 3000, 'switchIntervalMs': 100, 'hysteresisDegrees': 5}


class PointerLookTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.look = PointerLook(specification(), clock=lambda: self.now)

    def move(self, angle, duration=.8):
        point = (16+100*math.sin(math.radians(angle)), 16-100*math.cos(math.radians(angle)))
        for step in range(round(duration/.05)):
            self.now += .05
            self.look.update(point)

    def test_creator_defined_directions_and_boundary_hysteresis(self):
        self.move(0)
        self.assertEqual(self.look.animation, 'pose0')
        for angle in (44, 46)*8:
            self.move(angle, .1)
            self.assertEqual(self.look.animation, 'pose0')
        self.move(90)
        self.assertEqual(self.look.animation, 'pose90')
        self.move(180)
        self.assertEqual(self.look.animation, 'pose180')
        self.move(270)
        self.assertEqual(self.look.animation, 'pose270')
        self.move(359)
        self.assertEqual(self.look.animation, 'pose0')
        self.assertEqual(angular_distance(359, 1), 2)

    def test_rest_deadzone_distance_and_resume_delay(self):
        self.move(90)
        self.now += 3.1
        self.look.update(self.look.last_point)
        self.assertIsNone(self.look.animation)
        self.look.update((16, 16))
        self.assertIsNone(self.look.animation)
        self.look.update((10000, 16))
        self.assertIsNone(self.look.animation)
        self.look.update((116, 16), enabled=False)
        self.now += .2
        self.look.update((117, 16))
        self.assertIsNone(self.look.animation)
        self.now += .2
        self.look.update((118, 16))
        self.assertEqual(self.look.animation, 'pose90')

    def test_lumei_all_16_poses_do_not_interrupt_actions_or_queue(self):
        self.check_pack_poses('codex-lumei')

    def test_noah_all_16_poses_do_not_interrupt_actions_or_queue(self):
        self.check_pack_poses('noah-lulu')

    def check_pack_poses(self, package_id):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as folder:
            package = PackageLibrary(folder).install(root/f'offwork_pet/assets/starter-packs/{package_id}.petpack')
            runtime = BehaviorRuntime(package, clock=lambda: self.now)
            for index in range(16):
                angle = math.radians(index*22.5)
                for step in range(15):
                    self.now += .05
                    runtime.look_at((96+200*math.sin(angle), 72-200*math.cos(angle)))
                self.assertEqual(runtime.animation_id(), f'facing_{index:02d}')
                self.assertIsNone(runtime.current)
                self.assertFalse(runtime.queue)
            self.assertEqual(runtime.entry('menu', 'wave'), 'started')
            runtime.look_at((296, 72))
            self.assertEqual(runtime.animation_id(), 'wave')
            self.now += runtime.duration('wave')+.1
            runtime.tick()
            runtime.look_at((297, 72))
            self.assertEqual(runtime.animation_id(), 'rest')
            self.now += .4
            runtime.look_at((298, 72))
            self.assertEqual(runtime.animation_id(), 'facing_04')
            runtime.suspend(True)
            runtime.look_at((-100, 72))
            self.assertEqual(runtime.animation_id(), 'rest')
            runtime.close()
            self.assertIsNone(runtime.pointer_look.animation)

    def test_invalid_pose_declarations_are_rejected(self):
        with TemporaryDirectory() as folder:
            library = PackageLibrary(Path(folder)/'library')
            mutations = [lambda m,b: b['pointerLook'].update(origin=[100,16]),
                         lambda m,b: b['pointerLook'].update(states=['absent']),
                         lambda m,b: b['pointerLook'].update(directions=[]),
                         lambda m,b: b['pointerLook']['directions'][0].update(animation='absent'),
                         lambda m,b: b['pointerLook']['directions'][0].update(angle=float('nan')),
                         lambda m,b: b['pointerLook']['directions'][0].update(angle=90),
                         lambda m,b: b['pointerLook'].update(smoothingMs=0),
                         lambda m,b: b['pointerLook'].update(deadZone=100, maxDistance=10),
                         lambda m,b: m['requires'].remove('input.pointer-look/v1')]
            for index, mutation in enumerate(mutations):
                def configure(manifest, behavior):
                    manifest['requires'].append('input.pointer-look/v1')
                    behavior['pointerLook'] = deepcopy(specification())
                    for direction in behavior['pointerLook']['directions']:
                        behavior['animations'][direction['animation']] = deepcopy(behavior['animations']['breath'])
                    mutation(manifest, behavior)
                with self.subTest(index=index):
                    archive, _ = create_pack(folder, version=f'1.0.{index}', mutate=configure)
                    with self.assertRaises(PackageError):
                        library.install(archive)
