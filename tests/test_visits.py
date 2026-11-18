from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import hashlib
import os
import time
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from PySide6.QtWidgets import QApplication

from offwork_pet.app import Pet
from offwork_pet.core import Config, load_config, save_config
from offwork_pet.packages import PackageError, PackageLibrary, load_package
from offwork_pet.visit_assets import VisitorCache, export_package
from offwork_pet.package_limits import MAX_PACKAGE_ARCHIVE
from offwork_pet.visits import VisitorWindow
from offwork_pet.visitor_portal import PortalTransition
from server.app import create_app
from pack_fixtures import create_pack


class VisitAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive, source = create_pack(self.root, 'creator.robot', initial='charging', action='transform', interaction='light_show')
        self.package = load_package(source)
        self.visit = {'capabilities': self.package.snapshot('load-one'),
            'size': self.archive.stat().st_size, 'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest()}

    def tearDown(self):
        self.temp.cleanup()

    def test_transfer_validates_then_reuses_cache_without_installing_personal_character(self):
        personal = PackageLibrary(self.root/'personal')
        cache = VisitorCache(self.root/'visitors')
        self.assertIsNone(cache.find(self.visit['capabilities']))
        guest = cache.install(self.archive, self.visit)
        self.assertEqual(guest.digest, self.package.digest)
        self.assertEqual(personal.entries(), [])
        reloaded = {**self.visit['capabilities'], 'revision': 'load-two'}
        self.assertEqual(cache.find(reloaded).digest, guest.digest)

    def test_wrong_archive_or_content_does_not_leave_a_cached_role(self):
        cache = VisitorCache(self.root/'visitors')
        with self.assertRaises(PackageError):
            cache.install(self.archive, {**self.visit, 'sha256': '0'*64})
        wrong = {**self.visit, 'capabilities': {**self.visit['capabilities'], 'packageId': 'impostor.cat'}}
        with self.assertRaises(PackageError):
            cache.install(self.archive, wrong)
        self.assertIsNone(cache.find(self.visit['capabilities']))

    def test_export_preserves_the_package_content_identity(self):
        exported = self.root/'export.petpack'
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('archive must be streamed')):
            descriptor = export_package(self.package, exported)
            cache = VisitorCache(self.root/'visitors')
            result = cache.install(exported, {**descriptor, 'capabilities': self.package.snapshot('new-load')})
        self.assertEqual(descriptor['sha256'], hashlib.sha256(exported.read_bytes()).hexdigest())
        self.assertEqual(result.digest, self.package.digest)

    def test_prune_respects_budget_reservation_and_keeps_current_role(self):
        cache = VisitorCache(self.root/'visitors', budget=35)
        slots = [cache.root / f'{index:064x}' for index in range(4)]
        for index, slot in enumerate(slots):
            slot.mkdir()
            (slot/'asset').write_bytes(b'x' * 10)
            os.utime(slot, (index+1, index+1))
        cache.prune(keep=slots[0], reserve=10)
        self.assertEqual({path for path in slots if path.exists()}, {slots[0], slots[3]})

    def test_prune_limits_role_count_even_when_under_byte_budget(self):
        cache = VisitorCache(self.root/'visitors')
        slots = [cache.root / f'{index:064x}' for index in range(4)]
        for index, slot in enumerate(slots):
            slot.mkdir()
            os.utime(slot, (index+1, index+1))
        cache.prune()
        self.assertEqual({path for path in slots if path.exists()}, set(slots[1:]))

    def test_prune_reports_insufficient_space_without_removing_current_role(self):
        cache = VisitorCache(self.root/'visitors', budget=1)
        slot = cache.root / ('a' * 64)
        slot.mkdir()
        (slot/'asset').write_bytes(b'xx')
        with self.assertRaises(PackageError):
            cache.prune(keep=slot)
        self.assertTrue(slot.exists())

    def test_same_id_and_version_on_each_desktop_do_not_conflict(self):
        personal = PackageLibrary(self.root/'personal')
        original = personal.install(self.archive)
        other_root = self.root/'other'
        other_root.mkdir()
        archive, _ = create_pack(other_root, 'creator.robot', action='different_animation', interaction='other_reaction')
        other = PackageLibrary(self.root/'other-library').install(archive)
        visit = {'capabilities': other.snapshot('other'), 'size': archive.stat().st_size,
                 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
        guest = VisitorCache(self.root/'visitors').install(archive, visit)
        self.assertNotEqual(guest.digest, original.digest)
        self.assertEqual(personal.load(original.id).digest, original.digest)


class PortalTimingTests(unittest.TestCase):
    def test_door_is_open_before_the_visitor_becomes_visible(self):
        now = [0.0]
        transition = PortalTransition(clock=lambda: now[0])
        now[0] = .35
        frame = transition.sample()
        self.assertGreater(frame.door_alpha, 0)
        self.assertGreater(frame.openness, 0)
        self.assertEqual(frame.reveal, 0)
        now[0] = 1
        self.assertGreater(transition.sample().reveal, 0)
        self.assertLess(transition.sample().reveal, 1)

    def test_arrival_and_departure_finish_even_if_a_frame_is_delayed(self):
        now = [0.0]
        arrival = PortalTransition(clock=lambda: now[0])
        departure = PortalTransition(leaving=True, clock=lambda: now[0])
        now[0] = 20
        self.assertTrue(arrival.sample().done)
        self.assertEqual(arrival.sample().reveal, 1)
        self.assertEqual(arrival.sample().door_alpha, 0)
        self.assertEqual(departure.sample().reveal, 0)


class VisitServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.archive, source = create_pack(self.temp.name, 'creator.robot', interaction='light_show')
        self.caps = load_package(source).snapshot('load-one')
        self.payload = self.archive.read_bytes()
        self.context = TestClient(create_app(':memory:'))
        self.client = self.context.__enter__()
        self.a, self.b, self.c = [self.client.post('/v2/devices').json() for _ in range(3)]

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temp.cleanup()

    def headers(self, identity):
        return {'Authorization': 'Bearer '+identity['token']}

    def socket(self, identity):
        return self.client.websocket_connect('/v2/connect', headers=self.headers(identity))

    def receive(self, socket, kind, rid=None):
        for _ in range(100):
            result = socket.receive_json()
            if result['type'] == kind and (rid is None or result.get('request_id') == rid):
                return result
        self.fail('missing '+kind)

    def command(self, socket, kind, **data):
        rid = uuid.uuid4().hex
        socket.send_json({'type': kind, 'request_id': rid, **data})
        return self.receive(socket, 'result', rid)

    def pair(self, a, b, peer_support=True):
        self.profile(a)
        self.profile(b, visitSupport=peer_support)
        code = self.command(a, 'invite')['data']['code']
        return self.command(b, 'redeem', code=code)['data']['relation']['id']

    def profile(self, socket, **data):
        return self.command(socket, 'profile', **{'capabilities': self.caps, 'paused': False,
                            'visible': True, 'visitSupport': True, 'allowVisits': True, **data})

    def start(self, a, relation):
        return self.command(a, 'visit_start', relation_id=relation, capabilityRevision='load-one',
                            sha256=hashlib.sha256(self.payload).hexdigest(), size=len(self.payload))

    def active(self, a, b):
        relation = self.pair(a, b)
        started = self.start(a, relation)
        self.assertTrue(started['ok'], started)
        vid = started['data']['id']
        uploaded = self.client.put('/v2/visits/'+vid+'/package', content=self.payload, headers=self.headers(self.a))
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(self.receive(b, 'visit_offer')['visit']['capabilities'], self.caps)
        self.assertTrue(self.command(b, 'visit_ready', visit_id=vid, accepted=True)['ok'])
        self.receive(a, 'visit_active')
        return relation, vid

    def test_private_package_transfer_and_terminal_receipts(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            _, vid = self.active(a, b)
            url = '/v2/visits/'+vid+'/package'
            self.assertEqual(self.client.get(url).status_code, 401)
            self.assertEqual(self.client.get(url, headers=self.headers(self.c)).status_code, 404)
            self.assertEqual(self.client.get(url, headers=self.headers(self.a)).status_code, 403)
            self.assertEqual(self.client.get(url, headers=self.headers(self.b)).content, self.payload)
            rid, aid = uuid.uuid4().hex, uuid.uuid4().hex
            a.send_json({'type': 'visit_action', 'request_id': rid, 'visit_id': vid, 'interactionId': 'light_show', 'action_id': aid})
            self.assertEqual(self.receive(b, 'visit_action')['interactionId'], 'light_show')
            for status in ('queued', 'started', 'completed'):
                b.send_json({'type': 'visit_receipt', 'visit_id': vid, 'action_id': aid, 'status': status})
                if status == 'queued':
                    self.assertEqual(self.receive(a, 'result', rid)['data']['status'], 'queued')
                self.assertEqual(self.receive(a, 'visit_action_status')['status'], status)
            self.assertEqual(self.client.app.state.relay.visits.items[vid]['actions'], {})
            self.command(b, 'visit_end', visit_id=vid)
            self.assertEqual(self.receive(a, 'visit_end')['reason'], 'dismissed')
            self.assertEqual(self.client.get(url, headers=self.headers(self.b)).status_code, 404)

    def test_default_hd_character_exports_transfers_and_installs_above_old_limit(self):
        library = PackageLibrary(Path(self.temp.name)/'hd-library')
        package = library.install(Path(__file__).resolve().parents[1]/
                                  'offwork_pet/assets/starter-packs/gugagaga.petpack')
        archive = Path(self.temp.name)/'hd-visit.petpack'
        descriptor = export_package(package, archive)
        self.assertGreater(descriptor['size'], 32*1024**2)
        self.assertLessEqual(descriptor['size'], MAX_PACKAGE_ARCHIVE)
        self.payload = archive.read_bytes()
        self.caps = package.snapshot('load-one')
        with self.socket(self.a) as a, self.socket(self.b) as b:
            _, vid = self.active(a, b)
            response = self.client.get('/v2/visits/'+vid+'/package', headers=self.headers(self.b))
            self.assertEqual(response.status_code, 200)
            downloaded = Path(self.temp.name)/'received.petpack'
            downloaded.write_bytes(response.content)
            cached = VisitorCache(Path(self.temp.name)/'hd-cache').install(downloaded,
                                {**descriptor, 'capabilities': self.caps})
            self.assertEqual(cached.digest, package.digest)

    def test_old_client_and_disabled_reception_never_silently_fall_back(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            relation = self.pair(a, b, peer_support=False)
            self.assertEqual(self.start(a, relation)['code'], 'upgrade')
            self.profile(b, allowVisits=False)
            self.assertEqual(self.start(a, relation)['code'], 'paused')

    def test_corrupt_upload_is_rejected_and_session_cleaned(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            relation = self.pair(a, b)
            vid = self.start(a, relation)['data']['id']
            result = self.client.put('/v2/visits/'+vid+'/package', content=b'wrong', headers=self.headers(self.a))
            self.assertEqual(result.status_code, 400)
            self.assertFalse(self.client.app.state.relay.visits.items)

    def test_profile_change_and_pause_end_the_independent_session(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            _, vid = self.active(a, b)
            self.assertTrue(self.profile(b, paused=True)['ok'])
            self.assertFalse(self.client.app.state.relay.visits.items)
            self.receive(a, 'visit_end')
            self.assertFalse(Path(self.client.app.state.relay.visits.storage.name, vid+'.petpack').exists())

    def test_switching_source_and_unlinking_cancel_visits(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            relation, _ = self.active(a, b)
            self.profile(a, capabilities={**self.caps, 'revision': 'load-two'})
            self.assertEqual(self.receive(b, 'visit_end')['reason'], 'character_changed')
            self.assertFalse(self.client.app.state.relay.visits.items)
            self.profile(a)
            vid = self.start(a, relation)['data']['id']
            self.command(b, 'unlink', relation_id=relation)
            self.assertFalse(self.client.app.state.relay.visits.items)

    def test_duplicate_visit_and_peer_action_impersonation_are_rejected(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            relation, vid = self.active(a, b)
            self.assertEqual(self.start(b, relation)['code'], 'busy')
            result = self.command(b, 'visit_action', visit_id=vid, interactionId='light_show', action_id='impersonate')
            self.assertFalse(result['ok'])
            result = self.command(a, 'visit_action', visit_id=vid, interactionId='not-in-package', action_id='unknown')
            self.assertEqual(result['code'], 'unsupported')

    def test_disconnect_ends_the_visit(self):
        with self.socket(self.a) as a:
            with self.socket(self.b) as b:
                self.active(a, b)
            self.assertEqual(self.receive(a, 'visit_end')['reason'], 'offline')
            self.assertFalse(self.client.app.state.relay.visits.items)

    def test_queued_action_expiry_reaches_sender_as_a_terminal_result(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            _, vid = self.active(a, b)
            rid = uuid.uuid4().hex
            a.send_json({'type': 'visit_action', 'request_id': rid, 'visit_id': vid,
                         'interactionId': 'light_show', 'action_id': 'queued-action'})
            self.receive(b, 'visit_action')
            b.send_json({'type': 'visit_receipt', 'visit_id': vid, 'action_id': 'queued-action', 'status': 'queued'})
            self.assertTrue(self.receive(a, 'result', rid)['ok'])
            self.receive(a, 'visit_action_status')
            b.send_json({'type': 'visit_receipt', 'visit_id': vid, 'action_id': 'queued-action', 'status': 'expired'})
            self.assertEqual(self.receive(a, 'visit_action_status')['status'], 'expired')
            self.assertFalse(self.client.app.state.relay.visits.items[vid]['actions'])

    def test_visit_timeout_and_unconfirmed_action_timeout_are_explicit(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            _, vid = self.active(a, b)
            rid = uuid.uuid4().hex
            a.send_json({'type': 'visit_action', 'request_id': rid, 'visit_id': vid,
                         'interactionId': 'light_show', 'action_id': 'unconfirmed'})
            self.receive(b, 'visit_action')
            visits = self.client.app.state.relay.visits
            visits.items[vid]['actions']['unconfirmed']['deadline'] = 0
            self.client.portal.call(visits.sweep)
            self.assertEqual(self.receive(a, 'result', rid)['code'], 'expired')
            self.assertEqual(self.receive(a, 'visit_action_status')['status'], 'expired')
            visits.items[vid]['expires_at'] = 0
            self.client.portal.call(visits.sweep)
            self.assertEqual(self.receive(a, 'visit_end')['reason'], 'expired')
            self.assertEqual(self.receive(b, 'visit_end')['reason'], 'expired')
            self.assertFalse(visits.items)

    def test_only_the_source_can_upload_and_descriptors_are_bounded(self):
        with self.socket(self.a) as a, self.socket(self.b) as b:
            relation = self.pair(a, b)
            invalid = self.command(a, 'visit_start', relation_id=relation, capabilityRevision='load-one',
                                   sha256='a'*64, size=MAX_PACKAGE_ARCHIVE+1)
            self.assertEqual(invalid['code'], 'format')
            vid = self.start(a, relation)['data']['id']
            url = '/v2/visits/'+vid+'/package'
            self.assertEqual(self.client.put(url, content=self.payload).status_code, 401)
            self.assertEqual(self.client.put(url, content=self.payload, headers=self.headers(self.b)).status_code, 403)
            self.assertEqual(self.client.put(url, content=self.payload, headers=self.headers(self.c)).status_code, 404)


class VisitGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        archive, _ = create_pack(root, 'host.cat', action='roll', interaction='rub')
        self.library = PackageLibrary(root/'personal')
        self.library.install(archive)
        _, source = create_pack(root, 'guest.robot', initial='charging', action='transform', interaction='light_show')
        self.package = load_package(source)
        self.pet = Pet(Config(character='host.cat', autonomous_enabled=False), persist=False,
                       package_directory=self.library.root, seed_starters=False)
        self.pet.animation.stop(); self.pet.tick_timer.stop()
        self.pet.buddy.ready = True
        self.pet.buddy.relation = {'id': 'paired', 'partner_id': 'peer', 'visitSupport': True, 'allowVisits': True}
        self.pet.buddy.status = 'available'
        self.pet.buddy.identity = SimpleNamespace(record={})
        self.pet.visits.incoming = {'id': 'visitor-one', 'relation_id': 'paired', 'sender': 'peer',
            'capabilities': self.package.snapshot('guest-load'), 'stage': 'active', 'expires_at': time.time()+180}
        self.pet.visits.guest = VisitorWindow(self.pet.visits, self.package)
        self.pet.visits.guest.show()

    def tearDown(self):
        self.pet.visits.shutdown()
        self.pet.buddy.stop()
        self.pet.tray.hide(); self.pet.hide(); self.pet.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_remote_visitor_does_not_change_or_move_host_and_ignores_local_interaction_switch(self):
        runtime = self.pet.runtime
        runtime.entry('menu', 'menu', {'source': 'local'})
        before = self.pet.pos(), runtime.current, runtime.context.copy(), runtime.revision
        self.pet.config.interactive = False
        with patch.object(self.pet.buddy, 'send') as send:
            self.pet.visits.handle_message({'type': 'visit_action', 'visit_id': 'visitor-one',
                'action_id': 'arbitrary-remote', 'interactionId': 'light_show', 'expires_at': time.time()+5})
            self.assertEqual(self.pet.visits.guest.runtime.current, 'transform')
            self.assertEqual(send.call_args.args[0]['status'], 'started')
        self.assertEqual((self.pet.pos(), runtime.current, runtime.context, runtime.revision), before)
        self.assertEqual([i['id'] for i in self.pet.library.entries()], ['host.cat'])

    def test_switch_and_late_asset_callback_cannot_resurrect_guest(self):
        generation = self.pet.visits.generation
        guest = self.pet.visits.guest
        self.pet.select_package('host.cat')
        self.assertIsNone(self.pet.visits.guest)
        self.assertTrue(guest.runtime.stopped)
        self.assertEqual(guest.player.cache.bytes, 0)
        self.pet.visits.finished_job('lookup', generation, self.package, None)
        self.assertIsNone(self.pet.visits.guest)

    def test_hide_and_send_home_cleanup_only_visitor(self):
        runtime = self.pet.runtime
        guest = self.pet.visits.guest
        self.pet.visits.dismiss()
        self.assertIsNone(self.pet.visits.guest)
        self.assertTrue(guest.runtime.stopped)
        self.assertIs(self.pet.runtime, runtime)
        self.assertFalse(runtime.stopped)

    def test_visit_preference_persists(self):
        path = Path(self.temp.name)/'settings.json'
        save_config(Config(allow_visits=False), path)
        config, error = load_config(path)
        self.assertFalse(config.allow_visits)
        self.assertFalse(error)

    def test_end_aborts_transfers_and_stops_timer_immediately(self):
        from unittest.mock import Mock
        reply = Mock()
        self.pet.visits.transfers[reply] = (None, None)
        self.pet.visits.timer.start()
        self.pet.visits.dismiss()
        reply.abort.assert_called_once()
        self.assertFalse(self.pet.visits.timer.isActive())
        self.pet.visits.transfers.clear()

    def test_duplicate_remote_request_does_not_play_twice_or_queue_again(self):
        event = {'type': 'visit_action', 'visit_id': 'visitor-one', 'action_id': 'same-request',
                 'interactionId': 'light_show', 'expires_at': time.time()+5}
        with patch.object(self.pet.buddy, 'send') as send:
            self.pet.visits.handle_message(event)
            self.pet.visits.handle_message(event)
            self.pet.visits.tick()
            self.assertEqual(send.call_count, 1)
            self.assertFalse(self.pet.visits.guest.runtime.queue)

    def test_runtime_queue_expiry_is_sent_after_initial_queue_receipt(self):
        runtime = self.pet.visits.guest.runtime
        now = [0.0]
        runtime.clock = lambda: now[0]
        runtime.last_tick = 0
        runtime.entry('interactions', 'light_show', {'source': 'guest-local'})
        now[0] = .11
        event = {'type': 'visit_action', 'visit_id': 'visitor-one', 'action_id': 'queued-request',
                 'interactionId': 'light_show', 'expires_at': time.time()+5}
        with patch('offwork_pet.visits.time.monotonic', side_effect=lambda: now[0]), patch.object(self.pet.buddy, 'send') as send:
            self.pet.visits.handle_message(event)
            self.assertEqual(send.call_args.args[0]['status'], 'queued')
            now[0] = 1.12
            self.pet.visits.tick()
            self.assertEqual([call.args[0]['status'] for call in send.call_args_list], ['queued', 'expired'])

    def test_state_only_interaction_finishes_without_requiring_an_animation(self):
        runtime = self.pet.visits.guest.runtime
        runtime.data['interactions'][0]['invoke'] = {'type': 'state', 'id': 'charging'}
        with patch.object(self.pet.buddy, 'send') as send:
            self.pet.visits.handle_message({'type': 'visit_action', 'visit_id': 'visitor-one',
                'action_id': 'state-request', 'interactionId': 'light_show', 'expires_at': time.time()+5})
            self.assertEqual([call.args[0]['status'] for call in send.call_args_list], ['started', 'completed'])
            self.assertIsNone(runtime.current)

    def test_arrival_defers_network_readiness_and_user_actions_until_doorway_finishes(self):
        self.pet.visits.guest.dispose()
        self.pet.visits.guest = None
        self.pet.visits.incoming['stage'] = 'loading'
        host = self.pet.runtime
        before = host.revision, host.current, self.pet.pos()
        now = [0.0]
        with patch.object(self.pet.buddy, 'request') as request:
            self.pet.visits.show_guest(self.package)
            guest = self.pet.visits.guest
            guest.transition_timer.stop()
            guest.portal.clock = lambda: now[0]
            guest.portal.started = 0
            self.assertTrue(guest.isVisible())
            self.assertFalse(guest.can_interact)
            self.assertTrue(guest.runtime.suspended)
            request.assert_not_called()
            self.pet.visits.local_action('light_show')
            self.assertIsNone(guest.runtime.current)
            now[0] = 2
            guest.advance_transition()
            self.assertEqual(request.call_args.args[0], 'visit_ready')
            self.pet.visits.handle_message({'type': 'visit_active', 'visit': self.pet.visits.incoming.copy()})
            self.assertFalse(guest.runtime.suspended)
        self.assertEqual((host.revision, host.current, self.pet.pos()), before)

    def test_departure_releases_playback_but_keeps_only_a_temporary_snapshot(self):
        guest = self.pet.visits.guest
        self.pet.visits.dismiss()
        self.assertIn(guest, self.pet.visits.retiring)
        self.assertTrue(guest.runtime.stopped)
        self.assertEqual(guest.player.cache.bytes, 0)
        self.assertIsNotNone(guest.departure_image)
        self.assertFalse(guest.can_interact)
        self.assertFalse(self.pet.visits.timer.isActive())
        guest.portal.started -= 2
        guest.advance_transition()
        self.assertTrue(guest.disposing)
        self.assertFalse(guest.transition_timer.isActive())
        self.assertFalse(self.pet.visits.retiring)

    def test_pause_or_disconnect_cancels_departure_immediately(self):
        guest = self.pet.visits.guest
        self.pet.visits.dismiss()
        self.assertTrue(guest.transition_timer.isActive())
        self.pet.visits.stop(notify=False)
        self.assertTrue(guest.disposing)
        self.assertFalse(guest.transition_timer.isActive())
        self.assertFalse(self.pet.visits.retiring)

    def test_stale_arrival_callback_cannot_mark_a_cancelled_visit_ready(self):
        self.pet.visits.guest.dispose()
        self.pet.visits.guest = None
        self.pet.visits.incoming['stage'] = 'loading'
        with patch.object(self.pet.buddy, 'request') as request:
            self.pet.visits.show_guest(self.package)
            guest = self.pet.visits.guest
            arrived = guest.arrival_callback
            self.pet.visits.stop(notify=False)
            arrived()
            request.assert_not_called()
            self.assertFalse(guest.transition_timer.isActive())
