import hashlib
import asyncio
import io
import json
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from offwork_pet import workshop
from offwork_pet.packages import PackageLibrary, PackageError
from server.app import create_app
from server.hosting import Hosting
from server.http_body import read_json
from server.manage import import_sqlite
from server.resources import Catalog, publish
from server.store import Store
from tools.selfhost_backup import backup, validate_archive, validate_backup, write_manifest
from pack_fixtures import create_pack


class PolicyOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.policy = Hosting(self.store)

    def tearDown(self):
        self.store.close()

    def test_policy_reads_do_not_query_database(self):
        queries = []
        self.store.db.set_trace_callback(queries.append)
        for _ in range(100):
            self.policy.public()
            self.policy.get('chat_days')
        self.assertEqual(queries, [])

    def test_persisted_policy_ignores_invalid_seed_environment(self):
        before = self.policy.public()
        with patch.dict(os.environ, {'OFFWORK_CHAT_DAYS': 'invalid', 'OFFWORK_REGISTRATION': 'invalid'}):
            self.assertEqual(Hosting(self.store).public(), before)

    def test_incomplete_migration_history_is_rejected_without_changing_records(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'broken.db'
            store = Store(path)
            store.register()
            store.db.execute('DELETE FROM schema_migrations WHERE version=1')
            store.close()
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                Store(path)

    def test_pruning_runs_once_per_minute_instead_of_every_tick(self):
        queries = []
        self.store.db.set_trace_callback(queries.append)
        with patch('server.hosting.time.monotonic', return_value=100):
            for _ in range(100):
                self.policy.prune()
        self.assertEqual(sum(query.startswith('DELETE FROM chat_messages') for query in queries), 1)
        with patch('server.hosting.time.monotonic', return_value=161):
            self.policy.prune()
        self.assertEqual(sum(query.startswith('DELETE FROM chat_messages') for query in queries), 2)

    def test_failed_settings_audit_rolls_back_database_and_cache(self):
        before = self.policy.public()
        with patch.object(self.policy, 'audit', side_effect=RuntimeError('disk failure')):
            with self.assertRaises(RuntimeError):
                self.policy.update_settings({'name': 'changed', 'registration': 'closed', 'chat_days': 0})
        self.assertEqual(self.policy.public(), before)
        self.assertEqual(Hosting(self.store).public(), before)
        self.assertFalse(self.store.db.in_transaction)

    def test_failed_invite_audit_does_not_leave_an_unaudited_code(self):
        with patch.dict(os.environ, {'OFFWORK_ADMIN_USER': 'test', 'OFFWORK_ADMIN_PASSWORD': 'test'}):
            with TestClient(create_app(':memory:'), raise_server_exceptions=False) as client:
                client.post('/admin/login', json={'username': 'test', 'password': 'test'})
                with patch.object(client.app.state.relay.hosting, 'audit', side_effect=RuntimeError('disk failure')):
                    self.assertEqual(client.post('/admin/api/access-invites', json={}).status_code, 500)
                self.assertEqual(client.app.state.relay.store.db.execute('SELECT COUNT(*) FROM access_invites').fetchone()[0], 0)

    def test_upgrade_from_v2_preserves_devices_and_installs_cleanup_indexes(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'legacy.db'
            old = Store(path)
            identity = old.register()
            for index in ('chat_created', 'access_expires', 'audit_created'):
                old.db.execute(f'DROP INDEX {index}')
            old.db.execute('DELETE FROM schema_migrations WHERE version=3')
            old.close()
            upgraded = Store(path)
            try:
                self.assertEqual(upgraded.authenticate(identity['token']), identity['device_id'])
                indexes = {row[0] for row in upgraded.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
                self.assertTrue({'chat_created', 'access_expires', 'audit_created'} <= indexes)
            finally:
                upgraded.close()

    def test_legacy_import_batches_multiple_chunks_without_losing_rows(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'legacy.db'
            old = Store(path)
            old.db.execute('BEGIN')
            for _ in range(1201):
                old.register()
            old.db.execute('COMMIT')
            old.close()
            import_sqlite(path, self.store)
            self.assertEqual(self.store.admin_counts()['devices'], 1201)


class RequestLimitTests(unittest.TestCase):
    def test_streaming_body_stops_at_limit(self):
        consumed = []
        class Request:
            async def stream(self):
                for chunk in (b'1234', b'5678', b'not consumed'):
                    consumed.append(chunk)
                    yield chunk
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(read_json(Request(), limit=6))
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(len(consumed), 2)

    def test_control_endpoints_reject_large_bodies(self):
        with patch.dict(os.environ, {'OFFWORK_ADMIN_USER': 'test', 'OFFWORK_ADMIN_PASSWORD': 'test'}):
            with TestClient(create_app(':memory:')) as client:
                raw = b' ' * 16385
                self.assertEqual(client.post('/v2/devices', content=raw).status_code, 413)
                self.assertEqual(client.post('/admin/login', content=raw).status_code, 413)
                client.post('/admin/login', json={'username': 'test', 'password': 'test'})
                self.assertEqual(client.post('/admin/api/hosting', content=raw).status_code, 413)

    def test_deep_json_returns_a_client_error(self):
        with TestClient(create_app(':memory:')) as client:
            self.assertEqual(client.post('/v2/devices', content=b'['*2000+b']'*2000).status_code, 400)


class StreamTests(unittest.TestCase):
    def response(self, payload):
        response = io.BytesIO(payload)
        response.url = workshop.ORIGIN+'/download'
        return response

    def test_download_hashes_multiple_chunks_and_releases_response(self):
        payload = b'x' * 200000
        response = self.response(payload)
        with TemporaryDirectory() as temp, patch.object(workshop, 'urlopen', return_value=response):
            target = Path(temp)/'download'
            count, digest = workshop.download('/download', target, len(payload))
            self.assertEqual(count, len(payload))
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertEqual(target.read_bytes(), payload)
        self.assertTrue(response.closed)

    def test_cancel_or_overflow_removes_partial_download(self):
        for cancel in (True, False):
            cancelled = [False]
            with TemporaryDirectory() as temp, patch.object(workshop, 'urlopen', return_value=self.response(b'x'*200000)):
                target = Path(temp)/'download'
                with self.assertRaises(PackageError):
                    workshop.download('/download', target, 300000 if cancel else 100000,
                                      lambda: cancelled[0], lambda _: cancelled.__setitem__(0, cancel))
                self.assertFalse(target.exists())


class ResourceTests(unittest.TestCase):
    def test_oversized_snapshot_is_rejected_before_package_validation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)/'resources'
            archive = Path(temp)/'large.petpack'
            archive.write_bytes(b'x'*9)
            with patch.dict(os.environ, {'OFFWORK_RESOURCE_DIR': str(root)}), \
                    patch('server.resources.MAX_PACKAGE_ARCHIVE', 8), \
                    patch.object(PackageLibrary, 'install') as install:
                with self.assertRaises(ValueError):
                    publish(archive)
                install.assert_not_called()
                self.assertEqual(list(root.iterdir()), [])

    def test_deep_metadata_does_not_break_catalog(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {'OFFWORK_RESOURCE_DIR': temp}):
            child = Path(temp)/'deep'
            child.mkdir()
            (child/'metadata.json').write_text('['*2000+']'*2000)
            self.assertEqual(json.loads(Catalog().read()), {'items': []})

    def test_publication_validates_the_copied_snapshot(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {'OFFWORK_RESOURCE_DIR': str(Path(temp)/'resources')}):
            archive, _ = create_pack(temp)
            original = archive.read_bytes()
            install = PackageLibrary.install
            def mutate_after_validation(library, source):
                package = install(library, source)
                archive.write_bytes(b'changed source')
                return package
            with patch.object(PackageLibrary, 'install', mutate_after_validation):
                row = publish(archive)
            published = Path(temp)/'resources'/row['id']/'character.petpack'
            self.assertEqual(published.read_bytes(), original)
            self.assertEqual(row['sha256'], hashlib.sha256(original).hexdigest())
            self.assertEqual(PackageLibrary(Path(temp)/'verify').install(published).id, row['id'])

    def test_catalog_caches_reads_and_skips_corrupt_metadata(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {'OFFWORK_RESOURCE_DIR': temp}):
            good, bad = Path(temp)/'good', Path(temp)/'bad'
            good.mkdir()
            bad.mkdir()
            (good/'metadata.json').write_text(json.dumps({'id': 'good'}))
            (bad/'metadata.json').write_text('broken')
            catalog = Catalog()
            self.assertEqual(json.loads(catalog.read())['items'], [{'id': 'good'}])
            with patch.object(Path, 'open', side_effect=AssertionError('cache miss')):
                self.assertEqual(json.loads(catalog.read())['items'], [{'id': 'good'}])
            added = Path(temp)/'added'
            added.mkdir()
            (added/'metadata.json').write_text(json.dumps({'id': 'added'}))
            self.assertEqual(len(json.loads(catalog.read())['items']), 2)


class BackupTests(unittest.TestCase):
    def test_archive_requires_resources_root(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'empty.tar.gz'
            with tarfile.open(path, 'w:gz'):
                pass
            with self.assertRaises(ValueError):
                validate_archive(path)

    def make_files(self, root):
        (root/'database.dump').write_bytes(b'PGDMP test fixture')
        (root/'instance.env').write_text('private=true')
        with tarfile.open(root/'resources.tar.gz', 'w:gz') as archive:
            info = tarfile.TarInfo('resources')
            info.type = tarfile.DIRTYPE
            archive.addfile(info)

    def test_manifest_detects_damage_before_restore(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_files(root)
            write_manifest(root)
            validate_backup(root)
            (root/'database.dump').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                validate_backup(root)

    def test_archive_rejects_traversal_and_links(self):
        for name, kind in (('../escape', tarfile.REGTYPE), ('resources/link', tarfile.SYMTYPE),
                           ('/absolute', tarfile.REGTYPE), ('resources/fifo', tarfile.FIFOTYPE), ('resources', tarfile.REGTYPE)):
            with self.subTest(name=name), TemporaryDirectory() as temp:
                path = Path(temp)/'bad.tar.gz'
                with tarfile.open(path, 'w:gz') as archive:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    archive.addfile(info)
                with self.assertRaises(ValueError):
                    validate_archive(path)

    def test_backup_preserves_stopped_state_and_recovers_running_service_on_failure(self):
        for running in (True, False):
            with self.subTest(running=running), TemporaryDirectory() as temp:
                root = Path(temp)
                calls = []
                def run(*parts, **kwargs):
                    calls.append(parts)
                    if parts[0] == 'exec':
                        raise RuntimeError('dump failed')
                with self.assertRaises(RuntimeError):
                    backup(root/'backup', root, run, running)
                self.assertEqual(('start', 'app') in calls, running)
                self.assertEqual(('stop', 'app') in calls, running)
                self.assertFalse((root/'backup'/'COMPLETE').exists())
