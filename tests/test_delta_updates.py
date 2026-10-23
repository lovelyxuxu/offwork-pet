import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from offwork_pet.update_delta import stage_delta, verify_delta_stage
from offwork_pet.update_format import canonical, select_delta, UPDATE_ORIGIN, UpdateError, verify_manifest
from offwork_pet.update_install import owned_path, swap_release
from tools.delta_release import build_delta, recent_bases
from tools.publish_release import release_artifacts


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def metadata(folder, version):
    files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*')
        if p.is_file() and p.name != 'release-info.json')
    (folder / 'release-info.json').write_text(json.dumps({'appId': 'offwork-pet',
        'version': version, 'files': files + ['release-info.json']}), encoding='utf-8')


def archive(folder, path):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for file in folder.rglob('*'):
            if file.is_file():
                bundle.write(file, 'OffworkPet/' + file.relative_to(folder).as_posix())


class DeltaUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.install = self.root / 'OffworkPet'
        (self.install / '_internal').mkdir(parents=True)
        (self.install / 'OffworkPet.exe').write_bytes(b'old program')
        (self.install / 'OffworkPetUpdater.exe').write_bytes(b'old helper')
        (self.install / '_internal/runtime.dll').write_bytes(os.urandom(100000))
        (self.install / 'obsolete.txt').write_text('old file')
        metadata(self.install, '0.5.0-preview.11')
        self.base = self.root / 'OffworkPet-v0.5.0-preview.11-CharacterPacks.zip'
        archive(self.install, self.base)
        self.target = self.root / 'new-release'
        shutil.copytree(self.install, self.target)
        (self.target / 'OffworkPet.exe').write_bytes(b'new program')
        (self.target / 'OffworkPetUpdater.exe').write_bytes(b'new helper')
        (self.target / 'obsolete.txt').unlink()
        (self.target / 'added.txt').write_text('new file')
        metadata(self.target, '0.5.0-preview.12')
        self.full = self.root / 'OffworkPet-v0.5.0-preview.12-CharacterPacks.zip'
        archive(self.target, self.full)
        self.delta = build_delta(self.base, self.full, self.root)
        self.patch = self.root / self.delta['url'].rsplit('/', 1)[-1]
        self.release = {'version': '0.5.0-preview.12', 'size': self.full.stat().st_size,
            'sha256': digest(self.full), 'url': UPDATE_ORIGIN + '/offwork-pet/releases/' + self.full.name,
            'notes': '', 'deltas': [self.delta]}
        self.token = uuid.uuid4().hex

    def stage(self):
        return stage_delta(self.patch, self.install, self.token, self.release, self.delta)

    def rewrite_patch(self, transform):
        with zipfile.ZipFile(self.patch) as bundle:
            entries = [(item.filename, bundle.read(item)) for item in bundle.infolist()]
        entries = transform(entries)
        with zipfile.ZipFile(self.patch, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for name, content in entries:
                bundle.writestr(name, content)
        self.delta.update(size=self.patch.stat().st_size, sha256=digest(self.patch))

    def test_patch_omits_runtime_and_reconstructs_exact_target_with_additions_and_deletions(self):
        with zipfile.ZipFile(self.patch) as bundle:
            self.assertNotIn('OffworkPet/_internal/runtime.dll', bundle.namelist())
        self.assertLess(self.delta['size'], self.release['size'] // 10)
        (self.install / 'personal.txt').write_text('keep me')
        stage = self.stage()
        verify_delta_stage(self.patch, stage, self.release, self.delta)
        expected = {p.relative_to(self.target).as_posix(): digest(p) for p in self.target.rglob('*') if p.is_file()}
        actual = {p.relative_to(stage).as_posix(): digest(p) for p in stage.rglob('*') if p.is_file()}
        self.assertEqual(actual, expected)
        self.assertEqual((self.install / 'OffworkPet.exe').read_bytes(), b'old program')
        leftover = swap_release(self.install, stage, self.token, lambda *_: 'child', lambda _: True, lambda _: None)
        self.assertEqual((leftover / 'personal.txt').read_text(), 'keep me')
        self.assertFalse((self.install / 'obsolete.txt').exists())

    def test_changed_or_missing_reused_local_file_rejected_without_touching_install(self):
        runtime = self.install / '_internal/runtime.dll'
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    runtime.unlink()
                else:
                    runtime.write_bytes(b'corrupt local runtime')
                with self.assertRaises(UpdateError):
                    self.stage()
                self.assertFalse(owned_path(self.install, self.token, 'stage').exists())
                self.assertEqual((self.install / 'OffworkPet.exe').read_bytes(), b'old program')

    def test_helper_rejects_stage_tampering_and_unsigned_extra_files(self):
        stage = self.stage()
        (stage / 'OffworkPet.exe').write_bytes(b'tampered executable')
        with self.assertRaises(UpdateError):
            verify_delta_stage(self.patch, stage, self.release, self.delta)
        shutil.copy2(self.target / 'OffworkPet.exe', stage / 'OffworkPet.exe')
        (stage / 'extra.dll').write_bytes(b'extra')
        with self.assertRaises(UpdateError):
            verify_delta_stage(self.patch, stage, self.release, self.delta)

    def test_wrong_base_version_rejected(self):
        metadata(self.install, '0.5.0-preview.10')
        with self.assertRaises(UpdateError):
            self.stage()
        self.assertIsNone(select_delta(self.release, '0.5.0-preview.10'))

    def test_malicious_patch_paths_duplicates_and_inventory_bounds_rejected(self):
        original = self.patch.read_bytes()
        for name in ('OffworkPet/../outside.txt', 'OffworkPet/./odd.txt', 'OffworkPet/CON.txt',
                'OffworkPet/OFFWORKPET.EXE', 'OffworkPet/_internal', 'evil.txt'):
            with self.subTest(name=name):
                self.patch.write_bytes(original)
                self.rewrite_patch(lambda entries: entries + [(name, b'bad')])
                with self.assertRaises(UpdateError):
                    self.stage()
                self.assertFalse(owned_path(self.install, self.token, 'stage').exists())
        self.patch.write_bytes(original)
        def bad_index(entries):
            data = json.loads(entries[0][1])
            data['files']['_internal/runtime.dll']['size'] = 2 ** 40
            entries[0] = ('delta-info.json', json.dumps(data).encode())
            return entries
        self.rewrite_patch(bad_index)
        with self.assertRaises(UpdateError):
            self.stage()

    def test_failed_delta_startup_rolls_back(self):
        stage = self.stage()
        launches = []
        with self.assertRaises(UpdateError):
            swap_release(self.install, stage, self.token, lambda _, upgraded: launches.append(upgraded),
                lambda _: False, lambda _: None)
        self.assertEqual(launches, [True, False])
        self.assertEqual((self.install / 'OffworkPet.exe').read_bytes(), b'old program')

    def test_signed_delta_fields_cannot_be_tampered_and_invalid_descriptors_are_rejected(self):
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes_raw().hex()
        def signed(release):
            return {'release': release, 'signature': base64.b64encode(key.sign(canonical(release))).decode()}
        document = signed(self.release)
        verify_manifest(document, public)
        self.delta['size'] += 1
        with self.assertRaises(UpdateError):
            verify_manifest(document, public)
        for change in ({'url': 'https://evil.example/patch.zip'}, {'fromVersion': self.release['version']},
                {'size': True}, {'sha256': 'bad'}):
            with self.subTest(change=change), self.assertRaises(UpdateError):
                verify_manifest(signed({**self.release, 'deltas': [{**self.delta, **change}]}), public)
        with self.assertRaises(UpdateError):
            verify_manifest(signed({**self.release, 'deltas': [self.delta, self.delta]}), public)

    def test_publisher_requires_every_signed_artifact_and_correct_hash(self):
        artifacts = release_artifacts(self.full, self.release)
        self.assertEqual([path for path, _, _ in artifacts], [self.full, self.patch])
        self.patch.write_bytes(b'corrupt patch')
        with self.assertRaises(ValueError):
            release_artifacts(self.full, self.release)
        self.patch.unlink()
        with self.assertRaises(FileNotFoundError):
            release_artifacts(self.full, self.release)

    def test_automatic_base_selection_uses_older_full_releases_only(self):
        (self.root / 'OffworkPet-v-not-a-version-CharacterPacks.zip').write_bytes(b'unrelated archive')
        self.assertEqual(recent_bases(self.full, self.release['version']), [self.base])
        with self.assertRaises(UpdateError):
            build_delta(self.full, self.base, self.root)
        self.assertIsNone(select_delta({**self.release, 'deltas': [{**self.delta, 'size': self.release['size']}]},
            self.delta['fromVersion']))

    def test_rebuilding_identical_release_produces_identical_patch_bytes(self):
        rebuilt = build_delta(self.base, self.full, self.root / 'rebuilt')
        self.assertEqual(rebuilt, self.delta)
        with zipfile.ZipFile(self.patch) as bundle:
            self.assertEqual(bundle.getinfo('delta-info.json').date_time, (1980, 1, 1, 0, 0, 0))

    def run_worker(self, responses, cancel=False):
        from offwork_pet.update_ui import UpdateWorker
        worker = UpdateWorker({'release': self.release})
        received, failures, messages = [], [], []
        worker.result.connect(received.append)
        worker.failed.connect(failures.append)
        worker.progress.connect(lambda value, message: messages.append(message))
        interrupted = [False]
        def download(request, **kwargs):
            result = responses[request.full_url]
            if cancel:
                interrupted[0] = True
            if isinstance(result, Exception):
                raise result
            return io.BytesIO(result)
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', str(self.install / 'OffworkPet.exe')), \
                patch('offwork_pet.update_ui.__version__', self.delta['fromVersion']), \
                patch('offwork_pet.update_ui.verify_manifest', return_value=self.release), \
                patch('offwork_pet.update_ui.data_dir', return_value=self.root / 'user-data'), \
                patch.object(worker, 'isInterruptionRequested', side_effect=lambda: interrupted[0]), \
                patch('offwork_pet.update_ui.urlopen', side_effect=download) as network:
            worker.run()
        return worker, received, failures, messages, network

    def test_click_update_downloads_only_patch_and_prepares_new_helper(self):
        worker, received, failures, messages, network = self.run_worker({self.delta['url']: self.patch.read_bytes()})
        self.assertEqual(failures, [])
        self.assertEqual(network.call_count, 1)
        job = Path(received[0][1])
        self.assertEqual(json.loads((job / 'plan.json').read_text())['deltaFrom'], self.delta['fromVersion'])
        self.assertEqual((job / 'OffworkPetUpdater.exe').read_bytes(), b'new helper')
        self.assertTrue(any('增量更新' in message for message in messages))
        self.assertTrue(owned_path(self.install, worker.token, 'stage').is_dir())

    def test_missing_patch_download_or_corrupt_local_file_falls_back_to_full(self):
        for mode in ('404', 'corrupt-download', 'corrupt-local'):
            with self.subTest(mode=mode):
                responses = {self.delta['url']: self.patch.read_bytes(), self.release['url']: self.full.read_bytes()}
                if mode == '404':
                    responses[self.delta['url']] = OSError('404')
                elif mode == 'corrupt-download':
                    responses[self.delta['url']] = b'broken patch'
                else:
                    (self.install / '_internal/runtime.dll').write_bytes(b'broken local file')
                worker, received, failures, messages, network = self.run_worker(responses)
                self.assertEqual(failures, [])
                self.assertEqual(network.call_count, 2)
                job = Path(received[0][1])
                self.assertNotIn('deltaFrom', json.loads((job / 'plan.json').read_text()))
                self.assertTrue(any('切换为完整更新' in message for message in messages))
                self.assertEqual((owned_path(self.install, worker.token, 'stage') / '_internal/runtime.dll').read_bytes(),
                    (self.target / '_internal/runtime.dll').read_bytes())

    def test_cancelled_delta_never_starts_full_download_and_cleans_job(self):
        worker, received, failures, _, network = self.run_worker({self.delta['url']: self.patch.read_bytes()}, cancel=True)
        self.assertEqual(failures, ['已取消更新'])
        self.assertEqual(received, [])
        self.assertEqual(network.call_count, 1)
        self.assertFalse((self.root / 'user-data/updates' / worker.token).exists())
        self.assertFalse(owned_path(self.install, worker.token, 'stage').exists())

    def test_helper_runs_delta_plan_and_rechecks_bytes_before_old_process_exit(self):
        from offwork_pet.update_helper import run_plan
        stage = self.stage()
        job = self.root / 'user-data/updates' / self.token
        job.mkdir(parents=True)
        shutil.copy2(self.patch, job / 'release.zip')
        (job / 'plan.json').write_text(json.dumps({'manifest': {'release': self.release},
            'install': str(self.install), 'pid': os.getpid(), 'deltaFrom': self.delta['fromVersion']}))
        class Process:
            pid = 12345
            def poll(self):
                return None
        def launch(*args, **kwargs):
            (job / 'ready.json').write_text(json.dumps({'version': self.release['version']}))
            return Process()
        with patch('offwork_pet.update_helper.data_dir', return_value=self.root / 'user-data'), \
                patch('offwork_pet.update_helper.verify_manifest', return_value=self.release), \
                patch('offwork_pet.update_helper.subprocess.Popen', side_effect=launch), \
                patch('offwork_pet.update_helper.wait_old_process') as wait:
            original = (stage / 'OffworkPet.exe').read_bytes()
            (stage / 'OffworkPet.exe').write_bytes(b'corrupt stage')
            with self.assertRaises(UpdateError):
                run_plan(job / 'plan.json')
            wait.assert_not_called()
            (stage / 'OffworkPet.exe').write_bytes(original)
            run_plan(job / 'plan.json')
            wait.assert_called_once()
        result = json.loads((job / 'result.json').read_text())
        self.assertTrue(result['ok'])
        self.assertEqual((self.install / 'OffworkPet.exe').read_bytes(), b'new program')
        self.assertFalse((job / 'release.zip').exists())
