import io
import hashlib
import json
from pathlib import Path
import random
from time import sleep
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from offwork_pet import workshop
from offwork_pet.behavior import BehaviorRuntime
from offwork_pet.desktop_scene import Rect, Scene, Surface, visible_surfaces
from offwork_pet.exploration import Explorer
from offwork_pet.packages import PackageError, PackageLibrary
from offwork_pet.toys import Ball, BallGame
from offwork_pet.toy_ui import BallWindow
from offwork_pet.app import Pet
from offwork_pet.core import Config
from offwork_pet.package_ui import PackageDialog
from offwork_pet.workshop_ui import WorkshopDialog
from pack_fixtures import create_pack
from test_desktop_life import exploring_pack


def download_entry(archive):
    raw = Path(archive).read_bytes()
    with ZipFile(io.BytesIO(raw)) as zipped:
        manifest = json.loads(zipped.read('manifest.json'))
    return {'id': manifest['id'], 'version': manifest['version'],
        'displayName': manifest['displayName'], 'ownerName': manifest['author'],
        'description': manifest.get('description', ''), 'posterUrl': '',
        'downloadUrl': workshop.ORIGIN+'/api/catalog/characters/'+manifest['id']+'/download',
        'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}, raw


def download_response(raw):
    response = io.BytesIO(raw)
    response.url = workshop.ORIGIN+'/download'
    return response


class BallPhysicsTests(unittest.TestCase):
    def setUp(self):
        self.floor = Surface('desktop', 0, 1400, 1000)
        self.window = Surface('window', 200, 1000, 500)
        self.scene = Scene(Rect(0, 0, 1400, 1000), (self.window, self.floor))

    def test_diagonal_throw_rises_then_lands_on_window(self):
        ball = Ball(300, 16, 1384, velocity=260, y=380, velocity_y=-450)
        ball.advance(.1, scene=self.scene)
        self.assertGreater(ball.x, 300)
        self.assertLess(ball.y, 380)
        for _ in range(120):
            ball.advance(.02, scene=self.scene)
        self.assertEqual(ball.support, self.window)
        self.assertAlmostEqual(ball.y, 484)

    def test_upward_ball_passes_through_ledge_then_catches_it_descending(self):
        ball = Ball(400, 16, 1384, y=700, velocity_y=-1000)
        minimum = ball.y
        for _ in range(150):
            ball.advance(.02, scene=self.scene)
            minimum = min(minimum, ball.y)
        self.assertLess(minimum, 484)
        self.assertEqual(ball.support, self.window)

    def test_crossing_edge_drops_to_desktop_and_closed_support_falls(self):
        ball = Ball(990, 16, 1384, velocity=400, y=484, support=self.window)
        for _ in range(150):
            ball.advance(.02, scene=self.scene)
        self.assertEqual(ball.support, self.floor)
        ball = Ball(500, 16, 1384, y=484, support=self.window)
        empty = Scene(self.scene.bounds, (self.floor,))
        for _ in range(150):
            ball.advance(.02, scene=empty)
        self.assertEqual(ball.support, self.floor)

    def test_moving_window_and_ceiling_and_negative_monitor_origin(self):
        ball = Ball(500, 16, 1384, y=484, support=self.window)
        moved = Surface('window', 300, 1100, 450, 100)
        ball.advance(.02, scene=Scene(self.scene.bounds, (moved, self.floor)))
        self.assertEqual((ball.x, ball.y, ball.support), (600, 434, moved))
        scene = Scene(Rect(-1400, -400, 0, 600), (Surface('desktop', -1400, 0, 600),))
        ball = Ball(-1383, -1384, -16, velocity=-600, y=-383, velocity_y=-900)
        ball.advance(.1, scale=1, scene=scene)
        self.assertGreaterEqual(ball.y, -384)
        self.assertGreaterEqual(ball.x, -1384)
        self.assertGreater(ball.velocity, 0)

    def test_covered_ledge_is_not_a_landing_target(self):
        surfaces = visible_surfaces(self.scene.bounds, [
            ('front', Rect(100, 200, 1100, 700)), ('back', Rect(200, 500, 1000, 900))])
        self.assertFalse(any(s.key == 'back' for s in surfaces))
        ball = Ball(400, 16, 1384, y=300)
        for _ in range(180):
            ball.advance(.02, scene=Scene(self.scene.bounds, surfaces))
        self.assertEqual(ball.support.key, 'desktop')


class ExplorationAndDownloadTests(unittest.TestCase):
    def test_explore_reaches_high_windows_and_respects_authored_limits(self):
        with TemporaryDirectory() as folder:
            archive, _ = create_pack(folder, mutate=exploring_pack)
            package = PackageLibrary(Path(folder)/'library').install(archive)
            runtime = BehaviorRuntime(package)
            explorer = Explorer(runtime, rng=random.Random(2))
            floor = Surface('desktop', 0, 1920, 1080)
            high = Surface('window', 700, 1750, 360)
            scene = Scene(Rect(0, 0, 1920, 1080), (floor, high))
            self.assertEqual(explorer.choose(scene, (300, 816), (140, 264), (280, 280), 1)[0], high)
            explorer.spec['maxRise'] = 220
            choice = explorer.choose(scene, (300, 816), (140, 264), (280, 280), 1)
            self.assertTrue(choice is None or choice[0] == floor)

    def test_character_can_use_high_ledge_without_reserving_empty_bubble_space(self):
        with TemporaryDirectory() as folder:
            archive, _ = create_pack(folder, mutate=exploring_pack)
            package = PackageLibrary(Path(folder)/'library').install(archive)
            explorer = Explorer(BehaviorRuntime(package), rng=random.Random(2))
            ledge = Surface('window', 100, 1600, 180)
            scene = Scene(Rect(0, 0, 1920, 1080), (ledge,))
            choice = explorer.choose(scene, (300, 816), (140, 264), (280, 280), 1)
            self.assertEqual(choice[0], ledge)
            self.assertEqual(choice[2], -84)

    def test_catalog_search_metadata_and_bounded_preview(self):
        buffer = io.BytesIO()
        Image.new('RGBA', (192, 208), '#abc').save(buffer, 'PNG')
        document = {'items': [{'id': f'sample-{i}', 'name': '小角色', 'author': '小猫',
            'format_version': 1, 'version': '1.0.0', 'size': 123, 'sha256': 'a'*64,
            'download_url': f'/api/catalog/characters/sample-{i}/download',
            'thumbnail_url': f'/media/sample-{i}/thumbnail.png'} for i in range(25)]}
        calls = []
        def fake_fetch(url, *args):
            calls.append(url)
            return json.dumps(document).encode() if url == '/api/catalog/characters' else buffer.getvalue()
        with patch.object(workshop, 'fetch', side_effect=fake_fetch):
            entries, pages = workshop.catalog('小猫', 2)
        self.assertEqual(pages, 3)
        self.assertEqual(calls[0], '/api/catalog/characters')
        self.assertEqual(len(entries), 12)
        self.assertEqual(entries[0]['id'], 'sample-12')
        self.assertTrue(entries[0]['preview'])
        self.assertEqual(entries[0]['downloadUrl'], workshop.ORIGIN+'/api/catalog/characters/sample-12/download')

    def test_invalid_urls_cancellation_and_oversized_responses(self):
        for url in ('file:///etc/passwd', 'https://example.com/pet', 'http://codex-pets.net/a', 'https://codex-pets.net@evil.com/a'):
            with self.assertRaises(PackageError):
                workshop.source_url(url)
        with patch.object(workshop, 'urlopen') as opened:
            with self.assertRaises(PackageError):
                workshop.fetch('/a', 20, lambda: True)
            opened.assert_not_called()
        response = io.BytesIO(b'x'*21)
        response.url = workshop.ORIGIN+'/a'
        with patch.object(workshop, 'urlopen', return_value=response):
            with self.assertRaises(PackageError):
                workshop.fetch('/a', 20)

    def test_native_download_preserves_authored_meanings(self):
        entry, raw = download_entry('offwork_pet/assets/starter-packs/monthly-salary-cat.petpack')
        with TemporaryDirectory() as folder, patch.object(workshop, 'urlopen', side_effect=lambda *args, **kwargs: download_response(raw)) as fetch:
            path = workshop.prepare(entry, folder)
            downloaded = PackageLibrary(Path(folder)/'read').install(path)
            source = PackageLibrary(Path(folder)/'source').install(Path('offwork_pet/assets/starter-packs/monthly-salary-cat.petpack'))
            self.assertEqual(downloaded.digest, source.digest)
            fetch.assert_called_once()
            self.assertEqual(fetch.call_args.args[0].full_url, entry['downloadUrl'])

    def test_different_package_ids_are_not_confused(self):
        entry, raw = download_entry('offwork_pet/assets/starter-packs/codex-lumei.petpack')
        entry['id'] = 'lumei'
        with TemporaryDirectory() as folder, patch.object(workshop, 'urlopen', side_effect=lambda *args, **kwargs: download_response(raw)):
            with self.assertRaises(PackageError):
                workshop.prepare(entry, folder)

    def test_download_checks_size_hash_and_version_before_activation(self):
        entry, raw = download_entry('offwork_pet/assets/starter-packs/noah-lulu.petpack')
        for changes in ({'sha256': '0'*64}, {'size': len(raw)+1}, {'version': '999.0.0'}):
            with self.subTest(changes=changes), TemporaryDirectory() as folder:
                with patch.object(workshop, 'urlopen', side_effect=lambda *args, **kwargs: download_response(raw)), self.assertRaises(PackageError):
                    workshop.prepare({**entry, **changes}, folder)


class BallGestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drag_and_release_samples_vertical_velocity(self):
        with TemporaryDirectory() as folder:
            archive, _ = create_pack(folder, mutate=exploring_pack)
            pet = Pet(Config(character='', reminders=False, internal_surfaces=False), persist=False,
                package_directory=Path(folder)/'library', seed_starters=False)
            pet.animation.stop()
            pet.tick_timer.stop()
            pet.import_package(archive)
            scene = Scene(Rect(0, 0, 1400, 1000), (Surface('desktop', 0, 1400, 1000),))
            game = BallGame(pet.runtime, Ball(400, 16, 1384, y=500), (0, 1000), 140)
            game.scene = scene
            window = BallWindow(game, 516, pet)
            def event(kind, point, button, buttons):
                return QMouseEvent(kind, QPointF(22, 21), QPointF(*point), button, buttons, Qt.KeyboardModifier.NoModifier)
            try:
                with patch('offwork_pet.toy_ui.monotonic', return_value=100):
                    window.mousePressEvent(event(QEvent.Type.MouseButtonPress, (400, 500), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
                with patch('offwork_pet.toy_ui.monotonic', return_value=100.1):
                    window.mouseMoveEvent(event(QEvent.Type.MouseMove, (450, 420), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
                    window.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, (450, 420), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
                self.assertEqual((game.ball.x, game.ball.y), (450, 420))
                self.assertGreater(game.ball.velocity, 0)
                self.assertLess(game.ball.velocity_y, 0)
            finally:
                window.deleteLater()
                pet.buddy.stop()
                pet.click_timer.stop()
                pet.save_timer.stop()
                pet.tray.hide()
                pet.hide()
                pet.desktop_observer.close()
                pet.deleteLater()
                self.app.processEvents()


class WorkshopGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.pet = Pet(Config(character='', reminders=False, internal_surfaces=False, server_url=workshop.ORIGIN), persist=False,
            package_directory=Path(self.temp.name)/'library', seed_starters=False)
        self.pet.animation.stop()
        self.pet.tick_timer.stop()
        self.manager = PackageDialog(self.pet)
        self.gallery = WorkshopDialog(self.manager)
        self.entry, self.raw = download_entry('offwork_pet/assets/starter-packs/monthly-salary-cat.petpack')

    def tearDown(self):
        self.pet.character_loader.shutdown()
        if self.gallery.worker:
            self.gallery.worker.shutdown()
            self.app.processEvents()
        self.gallery.close()
        self.gallery.deleteLater()
        self.manager.deleteLater()
        self.pet.buddy.stop()
        self.pet.click_timer.stop()
        self.pet.save_timer.stop()
        self.pet.tray.hide()
        self.pet.hide()
        self.pet.desktop_observer.close()
        self.pet.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_worker(self):
        for _ in range(400):
            self.app.processEvents()
            if self.gallery.worker is None:
                return
            sleep(.01)
        self.fail('workshop task did not finish')

    def test_gallery_search_details_background_install_and_use_existing(self):
        with patch.object(workshop, 'catalog', return_value=([self.entry], 2)) as catalog:
            self.gallery.query.setText('猫')
            self.gallery.search()
            self.wait_worker()
        self.assertEqual(catalog.call_args.args[:2], ('猫', 1))
        self.assertEqual(self.gallery.list.count(), 1)
        self.assertTrue(self.gallery.next.isEnabled())
        self.assertFalse(self.gallery.previous.isEnabled())
        errors = []
        def install():
            try:
                self.gallery.download_button.click()
                self.wait_worker()
                self.assertEqual(self.pet.config.character, 'monthly-salary-cat')
                self.assertIn('已安装并启用', self.gallery.download_status.text())
            except Exception as error:
                errors.append(error)
            finally:
                self.gallery.detail_dialog.reject()
        QTimer.singleShot(0, install)
        with patch.object(workshop, 'urlopen', side_effect=lambda *args, **kwargs: download_response(self.raw)):
            self.gallery.details()
        self.assertFalse(errors, errors)
        with patch.object(workshop, 'prepare') as prepare:
            def use_existing():
                self.gallery.download_button.click()
                self.gallery.detail_dialog.reject()
            QTimer.singleShot(0, use_existing)
            self.gallery.details()
            prepare.assert_not_called()

    def test_failed_search_keeps_current_character_and_recovers_controls(self):
        with patch.object(workshop, 'catalog', side_effect=OSError('offline')):
            self.gallery.search()
            self.wait_worker()
        self.assertIn('offline', self.gallery.message.text())
        self.assertTrue(self.gallery.search_button.isEnabled())
        self.assertFalse(self.pet.config.character)

    def test_server_switch_discards_in_flight_gallery_results(self):
        from threading import Event
        entered, release = Event(), Event()
        def slow(*args, **kwargs):
            entered.set()
            release.wait(2)
            return [self.entry], 1
        try:
            with patch.object(workshop, 'catalog', side_effect=slow):
                self.gallery.search()
                for _ in range(100):
                    self.app.processEvents()
                    if entered.is_set():
                        break
                    sleep(.005)
                self.assertTrue(entered.is_set())
                self.pet.buddy.change_server('https://other.example.com')
                release.set()
                self.wait_worker()
                self.assertEqual(self.gallery.list.count(), 0)
                self.assertFalse(self.pet.config.character)
        finally:
            release.set()

    def test_close_popup_cancels_download_without_installing(self):
        self.gallery.loaded(([self.entry], 1))
        def slow_prepare(entry, folder, cancelled, progress, *, origin=None):
            for _ in range(100):
                if cancelled():
                    raise PackageError('已取消')
                sleep(.01)
            raise RuntimeError('cancellation was not received')
        def start_and_close():
            self.gallery.download_button.click()
            self.gallery.detail_dialog.reject()
        with patch.object(workshop, 'prepare', side_effect=slow_prepare), patch.object(self.pet, 'import_package') as install:
            QTimer.singleShot(0, start_and_close)
            self.gallery.details()
            self.wait_worker()
            install.assert_not_called()
        self.assertFalse(self.pet.config.character)
        self.assertIsNone(self.gallery.worker)
