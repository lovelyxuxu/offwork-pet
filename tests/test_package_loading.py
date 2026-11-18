from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from offwork_pet.app import Pet
from offwork_pet.core import Config
from offwork_pet.packages import PackageLibrary, PackageError
from offwork_pet.package_loading import prepare_character
from pack_fixtures import create_pack


class CharacterLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        library = PackageLibrary(root/'library')
        for name in ('old.cat', 'new.robot'):
            archive, _ = create_pack(root, name)
            library.install(archive)
        self.pet = Pet(Config(character='old.cat', network_enabled=False, reminders=False),
                       persist=False, package_directory=library.root, seed_starters=False,
                       enable_alert_hotkeys=False)

    def tearDown(self):
        self.pet.character_loader.shutdown()
        self.pet.animation.stop()
        self.pet.tick_timer.stop()
        self.pet.save_timer.stop()
        self.pet.buddy.stop()
        self.pet.visits.shutdown()
        self.pet.alerts.shutdown()
        self.pet.desktop_observer.close()
        self.pet.tray.hide()
        self.pet.hide()
        self.pet.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def until(self, condition):
        deadline = monotonic()+5
        while not condition() and monotonic() < deadline:
            self.app.processEvents()
            sleep(.005)
        self.assertTrue(condition(), 'background character task did not finish')

    def test_decode_keeps_gui_responsive_and_old_pet_until_atomic_switch(self):
        entered, release = Event(), Event()
        def slow(root):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test decoder was not released')
            return prepare_character(root)
        callbacks, pulses = [], []
        timer = QTimer()
        timer.timeout.connect(lambda: pulses.append(1))
        timer.start(10)
        try:
            with patch('offwork_pet.package_loading.prepare_character', side_effect=slow), \
                    patch.object(self.pet.library, 'load', side_effect=AssertionError('GUI revalidated content')):
                self.pet.character_loader.request('new.robot', callbacks.append)
                self.until(lambda: entered.is_set() and len(pulses) >= 3)
                self.assertEqual(self.pet.runtime.package.id, 'old.cat')
                release.set()
                self.until(lambda: bool(callbacks))
                self.assertEqual(callbacks, [None])
                self.assertEqual(self.pet.runtime.package.id, 'new.robot')
                self.assertGreater(self.pet.player.cache.bytes, 0)
        finally:
            release.set()
            timer.stop()

    def test_only_latest_selection_is_applied(self):
        entered, release = Event(), Event()
        def slow(root):
            entered.set()
            release.wait(5)
            return prepare_character(root)
        old_result, latest_result = [], []
        try:
            with patch('offwork_pet.package_loading.prepare_character', side_effect=slow):
                self.pet.character_loader.request('new.robot', old_result.append)
                self.until(entered.is_set)
                self.pet.character_loader.request('old.cat', latest_result.append)
                release.set()
                self.until(lambda: bool(latest_result))
            self.assertFalse(old_result)
            self.assertEqual(self.pet.runtime.package.id, 'old.cat')
        finally:
            release.set()

    def test_failed_load_retains_working_pet(self):
        results = []
        with patch('offwork_pet.package_loading.prepare_character', side_effect=PackageError('broken content')):
            self.pet.character_loader.request('new.robot', results.append)
            self.until(lambda: bool(results))
        self.assertIsInstance(results[0], PackageError)
        self.assertEqual(self.pet.runtime.package.id, 'old.cat')

    def test_removal_while_loading_does_not_resurrect_character(self):
        entered, release = Event(), Event()
        def slow(root):
            result = prepare_character(root)
            entered.set()
            release.wait(5)
            return result
        results = []
        try:
            with patch('offwork_pet.package_loading.prepare_character', side_effect=slow):
                self.pet.character_loader.request('new.robot', results.append)
                self.until(entered.is_set)
                self.pet.library.remove('new.robot')
                release.set()
                self.until(lambda: bool(results))
            self.assertIsInstance(results[0], PackageError)
            self.assertEqual(self.pet.runtime.package.id, 'old.cat')
        finally:
            release.set()
