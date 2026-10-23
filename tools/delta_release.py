"""Build direct file-level patches between complete desktop release ZIPs."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from offwork_pet.update_delta import MAX_INDEX, release_entries
from offwork_pet.update_format import UPDATE_ORIGIN, UpdateError, version_key


def release_inventory(bundle):
    entries, _ = release_entries(bundle)
    entry = entries.get('release-info.json')
    if entry is None or entry.file_size > MAX_INDEX:
        raise UpdateError('Release metadata is missing or too large')
    metadata = json.loads(bundle.read(entry))
    if metadata.get('appId') != 'offwork-pet' or set(metadata['files']) != set(entries):
        raise UpdateError('Release file inventory does not match ZIP')
    if (not {'OffworkPet.exe', 'OffworkPetUpdater.exe'} <= set(entries)
            or not any(name.startswith('_internal/') for name in entries)):
        raise UpdateError('Release executable or runtime files are missing')
    version_key(metadata.get('version'))
    files = {}
    for name, entry in entries.items():
        with bundle.open(entry) as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        files[name] = {'size': entry.file_size, 'sha256': digest}
    return metadata['version'], files, entries


def build_delta(base_archive, target_archive, output_dir):
    """All target hashes travel inside the signed patch, including reused files."""
    with zipfile.ZipFile(base_archive) as base, zipfile.ZipFile(target_archive) as target:
        old_version, old_files, _ = release_inventory(base)
        version, files, entries = release_inventory(target)
        if version_key(old_version) >= version_key(version):
            raise UpdateError('Delta base version must be older than target')
        changed = sorted(name for name in files if old_files.get(name) != files[name])
        index = json.dumps({'format': 1, 'fromVersion': old_version, 'version': version,
            'files': files}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        if len(index) > MAX_INDEX:
            raise UpdateError('Delta file inventory is too large')
        output = Path(output_dir) / f'OffworkPet-v{old_version}-to-v{version}-delta.zip'
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as patch:
            # Re-signing an identical release must not change its immutable patch bytes.
            index_entry = zipfile.ZipInfo('delta-info.json', (1980, 1, 1, 0, 0, 0))
            index_entry.compress_type = zipfile.ZIP_DEFLATED
            patch.writestr(index_entry, index)
            for name in changed:
                # Preserve ZIP timestamps and attributes for reproducible release artifacts.
                entry = entries[name]
                info = zipfile.ZipInfo('OffworkPet/' + name, entry.date_time)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = entry.external_attr
                with target.open(entry) as source, patch.open(info, 'w') as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'fromVersion': old_version, 'url': UPDATE_ORIGIN + '/offwork-pet/releases/' + output.name,
        'size': output.stat().st_size, 'sha256': digest}


def recent_bases(target_archive, target_version, limit=3):
    """Use retained complete releases so routine packaging also produces small updates."""
    candidates = []
    for path in Path(target_archive).parent.glob('OffworkPet-v*-CharacterPacks.zip'):
        raw_version = path.name.removeprefix('OffworkPet-v').removesuffix('-CharacterPacks.zip')
        try:
            key = version_key(raw_version)
        except UpdateError:
            continue
        if key < version_key(target_version):
            candidates.append((key, path))
    return [path for _, path in sorted(candidates, reverse=True)[:limit]]
