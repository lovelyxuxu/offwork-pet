import base64
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from offwork_pet.update_format import canonical, verify_manifest, version_key, UpdateError, UPDATE_ORIGIN
from offwork_pet.update_install import (stage_release, swap_release, owned_path, remove_owned, relative_file)
from offwork_pet.update_install import clean_job


def fixture(folder, version='1.0.0'):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / '_internal').mkdir(exist_ok=True)
    (folder / 'OffworkPet.exe').write_bytes(b'fake executable ' + version.encode())
    (folder / 'OffworkPetUpdater.exe').write_bytes(b'fake updater')
    (folder / '_internal/runtime.dll').write_bytes(b'fake runtime')
    info = {'appId': 'offwork-pet', 'version': version,
            'files': ['OffworkPet.exe', 'OffworkPetUpdater.exe', '_internal/runtime.dll', 'release-info.json']}
    (folder / 'release-info.json').write_text(json.dumps(info), encoding='utf-8')


class ReleaseFormatTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes_raw().hex()
        self.release = {'version': '0.5.0-preview.6', 'size': 10, 'sha256': 'a' * 64,
            'url': UPDATE_ORIGIN + '/offwork-pet/releases/new.zip', 'notes': 'test'}

    def sign(self, release):
        return {'release': release, 'signature': base64.b64encode(self.key.sign(canonical(release))).decode()}

    def test_signed_manifest_and_tamper(self):
        document = self.sign(self.release.copy())
        self.assertEqual(verify_manifest(document, self.public), self.release)
        document['release']['url'] = UPDATE_ORIGIN + '/offwork-pet/releases/tampered.zip'
        with self.assertRaises(UpdateError):
            verify_manifest(document, self.public)

    def test_untrusted_key_rejected(self):
        key = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
        with self.assertRaises(UpdateError):
            verify_manifest(self.sign(self.release), key)

    def test_signed_wrong_download_origin_or_bounds_rejected(self):
        for change in ({'url': 'http://bad.example/new.zip'}, {'url': UPDATE_ORIGIN + '/offwork-pet/releases/../new.zip'},
                {'size': 1024 ** 4}, {'size': True}, {'sha256': 'bad'}, {'version': 'run code'}):
            with self.subTest(change=change), self.assertRaises(UpdateError):
                verify_manifest(self.sign({**self.release, **change}), self.public)

    def test_versions_compare_previews_and_stable(self):
        values = ['0.5.0-preview.2', '0.5.0-preview.10', '0.5.0', '0.5.1-preview.1', '1.0.0']
        self.assertEqual(sorted(reversed(values), key=version_key), values)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.install = self.root / 'MyPet'
        fixture(self.install)
        self.new = self.root / 'build'
        fixture(self.new, '1.1.0')
        self.token = uuid.uuid4().hex

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self, extra=None):
        archive = self.root / 'update.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            for p in self.new.rglob('*'):
                if p.is_file():
                    bundle.write(p, 'OffworkPet/' + p.relative_to(self.new).as_posix())
            if extra:
                bundle.writestr(extra, 'malicious')
        release = {'version': '1.1.0', 'size': archive.stat().st_size, 'url': UPDATE_ORIGIN + '/offwork-pet/releases/new.zip',
                   'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
        return archive, release

    def stage(self):
        archive, release = self.archive()
        return stage_release(archive, self.install, self.token, release)

    def test_stage_does_not_modify_running_installation(self):
        stage = self.stage()
        self.assertEqual(json.loads((stage / 'release-info.json').read_text())['version'], '1.1.0')
        self.assertEqual(json.loads((self.install / 'release-info.json').read_text())['version'], '1.0.0')

    def test_path_traversal_duplicate_and_wrong_root_rejected(self):
        for name in ('OffworkPet/../../outside.txt', 'C:/outside.exe', 'OffworkPet/OffworkPet.exe',
                     'OffworkPet/CON.txt', 'Other/OffworkPet.exe', 'OffworkPet/bad:stream'):
            with self.subTest(name=name):
                archive, release = self.archive(name)
                with self.assertRaises(UpdateError):
                    stage_release(archive, self.install, self.token, release)
                self.assertFalse(owned_path(self.install, self.token, 'stage').exists())
                self.assertTrue((self.install / 'OffworkPet.exe').exists())

    def test_incomplete_download_rejected_and_stage_removed(self):
        archive, release = self.archive()
        archive.write_bytes(archive.read_bytes()[:-5])
        with self.assertRaises(UpdateError):
            stage_release(archive, self.install, self.token, release)
        self.assertFalse(owned_path(self.install, self.token, 'stage').exists())

    def test_unhealthy_new_process_rolls_back_and_relaunches_old(self):
        stage = self.stage()
        launches, terminated = [], []
        def launch(exe, upgraded):
            launches.append(upgraded)
            return 'child'
        with self.assertRaises(UpdateError):
            swap_release(self.install, stage, self.token, launch, lambda process: False, terminated.append)
        self.assertEqual(launches, [True, False])
        self.assertEqual(terminated, ['child'])
        self.assertEqual(json.loads((self.install / 'release-info.json').read_text())['version'], '1.0.0')

    def test_success_cleans_old_release_and_preserves_user_data(self):
        config = self.root / 'user-data/config.json'
        config.parent.mkdir()
        config.write_text('personal config')
        stage = self.stage()
        leftover = swap_release(self.install, stage, self.token, lambda *_: 'child', lambda _: True, lambda _: None)
        self.assertIsNone(leftover)
        self.assertEqual(config.read_text(), 'personal config')
        self.assertEqual(json.loads((self.install / 'release-info.json').read_text())['version'], '1.1.0')

    def test_unknown_files_are_preserved_in_backup(self):
        (self.install / 'my-notes.txt').write_text('keep me')
        stage = self.stage()
        leftover = swap_release(self.install, stage, self.token, lambda *_: 'child', lambda _: True, lambda _: None)
        self.assertEqual((leftover / 'my-notes.txt').read_text(), 'keep me')

    def test_cleanup_refuses_unrelated_directory(self):
        with self.assertRaises(UpdateError):
            remove_owned(self.root, self.install, self.token, 'stage')
        self.assertTrue(self.install.exists())

    def test_completed_cache_cleanup_is_confined_to_its_job(self):
        root = self.root / 'user-data/updates'
        job = root / self.token
        job.mkdir(parents=True)
        for name in ('OffworkPetUpdater.exe', 'release.zip', 'plan.json', 'helper-ready', 'ready.json', 'result.json'):
            (job / name).write_text('test')
        self.assertTrue(clean_job(job, root))
        self.assertFalse(job.exists())
        with self.assertRaises(UpdateError):
            clean_job(self.install, root)
        self.assertTrue((self.install / 'OffworkPet.exe').exists())

    def test_zip_expansion_budget(self):
        archive, release = self.archive()
        with patch('offwork_pet.update_install.MAX_UNPACKED', 3), self.assertRaises(UpdateError):
            stage_release(archive, self.install, self.token, release)

    def test_background_worker_downloads_and_prepares_without_replacing_app(self):
        import sys
        from offwork_pet.update_ui import UpdateWorker
        archive, release = self.archive()
        received, failures = [], []
        worker = UpdateWorker({'release': release})
        worker.result.connect(received.append)
        worker.failed.connect(failures.append)
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(self.install / 'OffworkPet.exe')), \
                patch('offwork_pet.update_ui.data_dir', return_value=self.root / 'user-data'), \
                patch('offwork_pet.update_ui.verify_manifest', return_value=release), \
                patch('offwork_pet.update_ui.urlopen', return_value=io.BytesIO(archive.read_bytes())):
            worker.run()
        self.assertEqual(failures, [])
        self.assertEqual(received[0][0], 'prepared')
        job = Path(received[0][1])
        self.assertTrue((job / 'plan.json').is_file())
        self.assertTrue((job / 'OffworkPetUpdater.exe').is_file())
        self.assertTrue(owned_path(self.install, worker.token, 'stage').exists())
        self.assertEqual(json.loads((self.install / 'release-info.json').read_text())['version'], '1.0.0')

    def test_download_failure_cleans_cache_and_keeps_app(self):
        import sys
        from offwork_pet.update_ui import UpdateWorker
        archive, release = self.archive()
        failures = []
        worker = UpdateWorker({'release': release})
        worker.failed.connect(failures.append)
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(self.install / 'OffworkPet.exe')), \
                patch('offwork_pet.update_ui.data_dir', return_value=self.root / 'user-data'), \
                patch('offwork_pet.update_ui.verify_manifest', return_value=release), \
                patch('offwork_pet.update_ui.urlopen', return_value=io.BytesIO(b'partial-download')):
            worker.run()
        self.assertTrue(failures)
        self.assertFalse(owned_path(self.install, worker.token, 'stage').exists())
        self.assertFalse((self.root / 'user-data/updates' / worker.token).exists())
        self.assertTrue((self.install / 'OffworkPet.exe').is_file())

    def test_cancelled_download_removes_its_partial_files(self):
        import sys
        from offwork_pet.update_ui import UpdateWorker
        archive, release = self.archive()
        worker = UpdateWorker({'release': release})
        failures = []
        worker.failed.connect(failures.append)
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(self.install / 'OffworkPet.exe')), \
                patch('offwork_pet.update_ui.data_dir', return_value=self.root / 'user-data'), \
                patch('offwork_pet.update_ui.verify_manifest', return_value=release), \
                patch('offwork_pet.update_ui.urlopen', return_value=io.BytesIO(archive.read_bytes())), \
                patch.object(worker, 'cancelled', side_effect=[None, UpdateError('已取消更新')]):
            worker.run()
        self.assertEqual(failures, ['已取消更新'])
        self.assertFalse((self.root / 'user-data/updates' / worker.token).exists())
        self.assertFalse(owned_path(self.install, worker.token, 'stage').exists())
        self.assertTrue((self.install / 'OffworkPet.exe').is_file())

    def test_helper_launch_failure_discards_prepared_files_and_keeps_app(self):
        import sys
        from PySide6.QtWidgets import QApplication, QWidget
        from offwork_pet.core import Config
        from offwork_pet.update_ui import UpdateController
        app = QApplication.instance() or QApplication([])
        parent = QWidget()
        parent.persist, parent.config = False, Config()
        controller = UpdateController(parent)
        stage = self.stage()
        job = self.root / 'user-data/updates' / self.token
        job.mkdir(parents=True)
        (job / 'OffworkPetUpdater.exe').write_bytes(b'helper')
        (job / 'plan.json').write_text('{}')
        with patch.object(sys, 'executable', str(self.install / 'OffworkPet.exe')), \
                patch('offwork_pet.update_ui.data_dir', return_value=self.root / 'user-data'), \
                patch('offwork_pet.update_ui.subprocess.Popen', side_effect=OSError('cannot launch')):
            controller.start_helper(job)
        self.assertFalse(stage.exists())
        self.assertFalse(job.exists())
        self.assertFalse(controller.waiting_helper)
        self.assertIn('旧版继续可用', controller.message)
        self.assertTrue((self.install / 'OffworkPet.exe').is_file())
        parent.deleteLater()
        app.processEvents()
