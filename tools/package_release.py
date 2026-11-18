"""Build a versioned desktop ZIP with user notes and no project docs or identities."""
import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
version = re.search(r'__version__ = "([^"]+)"', (ROOT / 'offwork_pet/__init__.py').read_text(encoding='utf-8'))[1]
tag = f'v{version}-CharacterPacks'
folder = ROOT / 'dist' / tag / 'OffworkPet'
archive_path = ROOT / 'dist' / f'OffworkPet-{tag}.zip'


def release_notes():
    return f'''下班搭子 v{version} · 自托管预览

打开 OffworkPet.exe 启动。默认角色为“小芽”，可在角色管理中导入 .petpack。
在“搭子 → 服务器”填写自己的服务器地址或 http://106.54.27.8。已有联网的旧版会沿用原服务。
源码和自托管部署说明：https://github.com/lovelyxuxu/offwork-pet
Qt / PySide6 等运行库许可见包内第三方许可文件。
'''


def copy_licenses():
    # Conda installs keep upstream notices next to each cached package.
    cache = Path(sys.prefix) / 'pkgs'
    destination = folder / '第三方许可'
    for package in ('pyside6', 'qtbase', 'qtdeclarative', 'qtimageformats', 'shiboken6'):
        for source in sorted(cache.glob(package + '-*')) if cache.exists() else []:
            licenses = source / 'info' / 'licenses'
            if licenses.is_dir():
                shutil.copytree(licenses, destination / source.name, dirs_exist_ok=True)
    from importlib.metadata import distribution
    for package in ('PySide6_Essentials', 'PySide6_Addons', 'shiboken6'):
        metadata = distribution(package)
        for entry in metadata.files or []:
            if 'licenses' in (part.lower() for part in entry.parts) or entry.name.upper().startswith('LICENSE'):
                source = Path(metadata.locate_file(entry))
                if source.is_file():
                    target = destination / package / Path(*entry.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    pillow = distribution('Pillow')
    for entry in pillow.files or []:
        if entry.name == 'LICENSE' and 'licenses' in entry.parts:
            source = Path(pillow.locate_file(entry))
            target = destination / 'Pillow'
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target / 'LICENSE')
    crypto = distribution('cryptography')
    for entry in crypto.files or []:
        if entry.name.startswith('LICENSE') and 'licenses' in entry.parts:
            source = Path(crypto.locate_file(entry))
            target = destination / 'cryptography' / entry.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def validate_assets():
    from tempfile import TemporaryDirectory
    sys.path.insert(0, str(ROOT))
    from offwork_pet.packages import PackageLibrary
    from offwork_pet.dialogue import read_pack
    for dialogue in (ROOT / 'offwork_pet/assets/dialogue-packs').glob('*.petdialogue'):
        read_pack(dialogue)
    assets = sorted((ROOT / 'offwork_pet/assets/starter-packs').glob('*.petpack'))
    if not assets:
        raise RuntimeError('No starter character packages')
    with TemporaryDirectory() as temporary:
        library = PackageLibrary(temporary)
        for archive in assets:
            library.install(archive)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip-only', action='store_true')
    parser.add_argument('--sign-key', type=Path)
    parser.add_argument('--base-archive', type=Path, action='append', default=None,
        help='Full release ZIP to generate an incremental update from (repeatable).')
    args = parser.parse_args()
    if not args.zip_only and importlib.util.find_spec('numpy') is not None:
        raise RuntimeError('Use a clean release venv without NumPy; see requirements-release.txt.')
    executable = folder / 'OffworkPet.exe'
    if not args.zip_only and executable.exists():
        try:
            with executable.open('r+b'):
                pass
        except PermissionError as error:
            raise RuntimeError('Target release is running or locked. Increase the version or close that release before rebuilding.') from error
    validate_assets()
    if not args.zip_only:
        subprocess.run([
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onefile',
            '--name', 'OffworkPetUpdater', '--paths', str(ROOT),
            '--distpath', str(ROOT / 'build' / tag / 'helper'),
            '--workpath', str(ROOT / 'build' / tag / 'helper-work'),
            '--specpath', str(ROOT / 'work' / 'package' / tag),
            str(ROOT / 'tools/run_updater.py'),
        ], cwd=ROOT, check=True)
        subprocess.run([
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
            '--name', 'OffworkPet', '--distpath', str(folder.parent),
            '--workpath', str(ROOT / 'build' / tag), '--specpath', str(ROOT / 'work' / 'package' / tag),
            '--add-data', str(ROOT / 'offwork_pet/assets/starter-packs') + ';offwork_pet/assets/starter-packs',
            '--add-data', str(ROOT / 'offwork_pet/assets/ui') + ';offwork_pet/assets/ui',
            '--add-data', str(ROOT / 'offwork_pet/assets/dialogue-packs') + ';offwork_pet/assets/dialogue-packs',
            '--additional-hooks-dir', str(ROOT / 'tools/pyinstaller_hooks'),
            '--exclude-module', 'PySide6.QtWebEngineCore',
            '--exclude-module', 'PySide6.QtWebEngineWidgets',
            '--exclude-module', 'numpy', '--exclude-module', 'scipy',
            '--exclude-module', 'PySide6.QtMultimedia',
            '--exclude-module', 'PySide6.QtMultimediaWidgets',
            str(ROOT / 'main.py'),
        ], cwd=ROOT, check=True)
        shutil.copy2(ROOT / 'build' / tag / 'helper/OffworkPetUpdater.exe', folder / 'OffworkPetUpdater.exe')
    if not (folder / 'OffworkPet.exe').is_file():
        raise RuntimeError('Packaged executable not found')
    # The product UI is Chinese; retain Chinese and English Qt messages.
    translations = folder / '_internal/PySide6/translations'
    for translation in translations.glob('*.qm'):
        if not translation.stem.endswith(('_zh_CN', '_en')):
            translation.unlink()
    (folder / '版本说明.txt').write_text(release_notes(), encoding='utf-8-sig')
    for notice in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / notice, folder / notice)
    copy_licenses()
    managed = sorted(path.relative_to(folder).as_posix() for path in folder.rglob('*')
        if path.is_file() and path.name != 'release-info.json')
    (folder / 'release-info.json').write_text(json.dumps({'appId': 'offwork-pet', 'version': version,
        'files': managed + ['release-info.json']}, ensure_ascii=False, indent=2), encoding='utf-8')
    files = sorted(path for path in folder.rglob('*') if path.is_file())
    for path in files:
        relative = path.relative_to(folder)
        if path.name.lower().startswith('mkl') or any(part in ('numpy', 'scipy') for part in relative.parts):
            raise RuntimeError('Unexpected scientific computing dependency in desktop release: ' + str(relative))
        if path.name.lower() in ('qt6quick.dll', 'qt6qml.dll', 'qt6pdf.dll'):
            raise RuntimeError('Unexpected unused Qt module: ' + str(relative))
        if any(part.lower() in ('docs', 'tests', '__pycache__', '.git') for part in relative.parts):
            raise RuntimeError(f'Unexpected docs in release: {relative}')
        if path.name.lower() in ('config.json', 'server-access.md', '.env') or path.suffix.lower() in ('.clixml', '.sqlite3', '.blend', '.glb', '.key', '.pem', '.pfx', '.p12', '.log', '.bak', '.tmp', '.pdb') or path.name.lower().startswith('identity'):
            raise RuntimeError(f'Unexpected private or source file in release: {relative}')
    with ZipFile(archive_path, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, path.relative_to(folder.parent))
    with ZipFile(archive_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f'Corrupt ZIP entry: {bad}')
    print(f'READY {archive_path}', flush=True)
    print(f'ZIP {archive_path.stat().st_size / 1048576:.1f} MB; extracted {sum(path.stat().st_size for path in files) / 1048576:.1f} MB', flush=True)
    components = defaultdict(int)
    for path in files:
        relative = path.relative_to(folder)
        group = '/'.join(relative.parts[:2]) if relative.parts[0] == '_internal' else relative.parts[0]
        components[group] += path.stat().st_size
    with archive_path.open('rb') as stream:
        archive_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (ROOT / 'dist' / f'release-audit-v{version}.json').write_text(json.dumps({
        'version': version, 'zipBytes': archive_path.stat().st_size,
        'installedBytes': sum(path.stat().st_size for path in files), 'sha256': archive_digest,
        'fileCount': len(files), 'components': dict(sorted(components.items(), key=lambda entry: -entry[1]))}, indent=2), encoding='utf-8')
    if args.sign_key:
        base_arguments = [argument for base in args.base_archive or [] for argument in ('--base-archive', str(base))]
        subprocess.run([sys.executable, str(ROOT / 'tools/release_manifest.py'), '--key', str(args.sign_key),
            '--archive', str(archive_path), '--output', str(ROOT / 'dist/latest.json'), *base_arguments], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
