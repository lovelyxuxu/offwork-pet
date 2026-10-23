"""Exercise the actual frozen updater against an isolated copy on Windows (same-version rehearsal)."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from offwork_pet.update_format import select_delta, verify_manifest
from offwork_pet.update_delta import stage_delta
from offwork_pet.update_install import owned_path, stage_release


def until(predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError('Windows update rehearsal timed out')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--from-archive', type=Path)
    parser.add_argument('--expect-package', action='append', default=[])
    parser.add_argument('--delta-from', help='Rehearse the signed delta for this base version.')
    parser.add_argument('--helper', type=Path, help='Use a separately built helper for local rehearsal.')
    parser.add_argument('--download', action='store_true', help='Use the real update feed and background download worker.')
    args = parser.parse_args()
    document = json.loads(args.manifest.read_text(encoding='utf-8'))
    release = verify_manifest(document)
    delta = select_delta(release, args.delta_from) if args.delta_from else None
    if args.delta_from and (delta is None or args.from_archive is None):
        parser.error('--delta-from requires a matching signed delta and --from-archive')
    download = args.archive.parent / delta['url'].rsplit('/', 1)[-1] if delta else args.archive
    expected_packages = {}
    with zipfile.ZipFile(args.archive) as bundle:
        for name in bundle.namelist():
            if '/starter-packs/' in name and name.endswith('.petpack'):
                with zipfile.ZipFile(io.BytesIO(bundle.read(name))) as pack:
                    manifest = json.loads(pack.read('manifest.json'))
                    expected_packages[manifest['id']] = manifest['version']
    work = ROOT / 'work'
    with TemporaryDirectory(prefix='native-update-', dir=work) as temporary:
        root = Path(temporary).resolve()
        assert root.is_relative_to(work.resolve())
        install = root / 'OffworkPet'
        with zipfile.ZipFile(args.from_archive or args.archive) as bundle:
            bundle.extractall(root)
        token = uuid.uuid4().hex
        user = root / 'user-data'
        user.mkdir()
        config = {'character': 'noah-lulu', 'auto_update_check': False, 'network_enabled': False, 'off_time': '17:15'}
        (user / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        (user / 'personal-sentinel.txt').write_text('keep personal data', encoding='utf-8')
        (install / 'my-notes.txt').write_text('keep user files', encoding='utf-8')
        env = dict(os.environ, OFFWORK_HOME=str(user), QT_QPA_PLATFORM='offscreen')
        child_flags = subprocess.CREATE_NO_WINDOW
        old = subprocess.Popen([str(install / 'OffworkPet.exe')], cwd=install, env=env, creationflags=child_flags)
        helper = None
        new_pid = None
        try:
            until(lambda: (user / 'characters/catalog.json').exists() or old.poll() is not None)
            assert old.poll() is None, 'Old app did not start'
            print('PASS: actual frozen application started in isolated directory.', flush=True)
            if args.download:
                from unittest.mock import patch
                from offwork_pet.update_ui import UpdateWorker, fetch_manifest
                from offwork_pet.update_install import marker
                document = fetch_manifest()
                assert document['release'] == release, 'Live release differs from the requested signed manifest'
                worker = UpdateWorker(document)
                worker.token = token
                received, failures, messages, requested = [], [], [], []
                worker.result.connect(received.append)
                worker.failed.connect(failures.append)
                worker.progress.connect(lambda _, text: messages.append(text))
                from offwork_pet.update_ui import urlopen as real_urlopen
                def track_download(request, **kwargs):
                    requested.append(request.full_url)
                    return real_urlopen(request, **kwargs)
                # Run the identical worker against an isolated frozen install and its version.
                with patch.object(sys, 'frozen', True, create=True), \
                        patch.object(sys, 'executable', str(install / 'OffworkPet.exe')), \
                        patch('offwork_pet.update_ui.__version__', marker(install)['version']), \
                        patch('offwork_pet.update_ui.data_dir', return_value=user), \
                        patch('offwork_pet.update_ui.urlopen', side_effect=track_download):
                    worker.run()
                assert not failures, failures
                assert received and received[0][0] == 'prepared', received
                assert requested == [(delta or release)['url']], requested
                if delta:
                    assert any('下载增量更新' in text for text in messages), messages
                stage = worker.stage
                print('PASS: real background worker downloaded only the expected URL: ' + requested[0], flush=True)
            else:
                stage = (stage_delta(download, install, token, release, delta) if delta
                    else stage_release(download, install, token, release))
            job = user / 'updates' / token
            job.mkdir(parents=True, exist_ok=True)
            if not args.download:
                shutil.copy2(download, job / 'release.zip')
            helper_source = args.helper or ((stage if delta else install) / 'OffworkPetUpdater.exe')
            shutil.copy2(helper_source, job / 'OffworkPetUpdater.exe')
            plan = {'manifest': document, 'install': str(install), 'pid': old.pid}
            if delta:
                plan['deltaFrom'] = delta['fromVersion']
                print(f'PASS: staged complete release from delta ({download.stat().st_size / 1048576:.1f} MB).', flush=True)
            (job / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
            helper = subprocess.Popen([str(job / 'OffworkPetUpdater.exe'), str(job / 'plan.json')], cwd=job, env=env, creationflags=child_flags)
            until(lambda: (job / 'helper-ready').exists() or helper.poll() is not None)
            assert helper.poll() is None, 'Frozen helper failed before acknowledgement'
            old.terminate()
            old.wait(10)
            print('PASS: frozen helper ready; owned old application stopped.', flush=True)
            until(lambda: (job / 'result.json').exists() or helper.poll() is not None)
            result = json.loads((job / 'result.json').read_text(encoding='utf-8'))
            assert result['ok'], result
            new_pid = result['pid']
            helper.wait(15)
            assert helper.returncode == 0
            assert (job / 'ready.json').is_file()
            assert json.loads((user / 'config.json').read_text(encoding='utf-8'))['off_time'] == '17:15'
            if args.from_archive:
                catalog = json.loads((user / 'characters/catalog.json').read_text(encoding='utf-8'))
                for package_id, version in expected_packages.items():
                    assert catalog['packages'][package_id]['version'] == version
                assert json.loads((user / 'config.json').read_text(encoding='utf-8'))['character'] == 'noah-lulu'
                print('PASS: shipped character versions installed/migrated; selected role preserved.', flush=True)
            if args.expect_package:
                catalog = json.loads((user / 'characters/catalog.json').read_text(encoding='utf-8'))
                for package_id in args.expect_package:
                    assert package_id in catalog['packages'], 'New package not seeded: '+package_id
                print('PASS: expected new character packages seeded: '+', '.join(args.expect_package), flush=True)
            assert (user / 'personal-sentinel.txt').read_text() == 'keep personal data'
            backup = owned_path(install, token, 'old')
            assert (backup / 'my-notes.txt').read_text() == 'keep user files'
            assert not (backup / 'OffworkPet.exe').exists()
            assert not (job / 'release.zip').exists()
            print('PASS: real Windows folder replacement + automatic relaunch + readiness acknowledgement + old-file cleanup.', flush=True)
            print('PASS: countdown settings, independent role library, personal data and unknown installation files preserved.', flush=True)
        finally:
            if old.poll() is None:
                old.terminate()
                old.wait(10)
            if new_pid:
                import psutil
                try:
                    new = psutil.Process(new_pid)
                    assert Path(new.exe()).resolve() == (install / 'OffworkPet.exe').resolve()
                    new.terminate()
                    new.wait(10)
                except psutil.NoSuchProcess:
                    pass
            if helper and helper.poll() is None:
                helper.terminate()
                helper.wait(10)


if __name__ == '__main__':
    main()
