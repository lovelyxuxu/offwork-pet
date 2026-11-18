"""Private one-to-one relay. Run exactly one worker (presence is in memory)."""
import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from .store import DomainError, Store
from .device_info import validate_device_info
from .admin import install_admin_routes
from .hosting import Hosting
from .http_body import read_json
from .rally import Rally
from .visits import Visits, install_routes


@dataclass
class Session:
    socket: WebSocket
    last: float
    paused: bool = False
    visible: bool = False
    ready: bool = False
    protocol: int = 1
    capabilities: dict | None = None
    visits: bool = False
    allow_visits: bool = False
    ip: str = ''
    connected: float = 0.0


def validate_capabilities(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DomainError('format', '角色能力格式不正确')
    for key, limit in (('packageId', 96), ('packageVersion', 40), ('displayName', 64), ('revision', 64), ('digest', 64)):
        if not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= limit:
            raise DomainError('format', '角色能力字段不正确')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value['packageId']) or not re.fullmatch(r'[0-9a-f]{64}', value['digest']):
        raise DomainError('format', '角色能力标识不正确')
    interactions = value.get('interactions')
    if not isinstance(interactions, list) or len(interactions) > 64:
        raise DomainError('format', '互动清单超限')
    seen, cleaned = set(), []
    for item in interactions:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', item['id'])
                or item['id'] in seen or not isinstance(item.get('label'), str) or not 1 <= len(item['label']) <= 64
                or type(item.get('order', 0)) not in (int, float) or not -1000 <= item.get('order', 0) <= 1000):
            raise DomainError('format', '互动入口格式不正确')
        seen.add(item['id'])
        cleaned.append({'id': item['id'], 'label': item['label'], 'order': item.get('order', 0)})
    return {**{key: value[key] for key in ('packageId', 'packageVersion', 'displayName', 'revision', 'digest')}, 'interactions': cleaned}


def client_ip(socket):
    peer = socket.client.host if socket.client else ''
    if peer in ('127.0.0.1', '::1'):
        real = socket.headers.get('x-real-ip', '').strip()
        if real:
            return real
        forwarded = socket.headers.get('x-forwarded-for', '').split(',')[0].strip()
        if forwarded:
            return forwarded
    return peer


class Relay:
    def __init__(self, store):
        self.store = store
        self.sessions = {}
        self.limits = defaultdict(deque)
        self.pending = {}
        self.seen = {}
        self.trend = deque(maxlen=2880)
        self._trend_last = 0.0
        self.visits = Visits(self)
        self.rally = Rally(self)

    def limit(self, key, maximum, seconds):
        now = time.monotonic()
        entries = self.limits[key]
        while entries and entries[0] <= now - seconds:
            entries.popleft()
        if len(entries) >= maximum:
            raise DomainError('rate', '操作太快了，请稍后再试')
        entries.append(now)

    def available(self, device):
        session = self.sessions.get(device)
        if not session or not session.ready or time.monotonic() - session.last > 30:
            return 'offline'
        return 'paused' if session.paused or not session.visible else 'available'

    def sample_trend(self):
        now = time.time()
        if now - self._trend_last < 30:
            return
        self._trend_last = now
        online = sum(1 for device in self.sessions if self.available(device) != 'offline')
        self.trend.append({'t': now, 'online': online})

    async def kick(self, device):
        session = self.sessions.get(device)
        if not session:
            return False
        with suppress(Exception):
            await session.socket.close(code=4002)
        if self.sessions.get(device) is session:
            self.sessions.pop(device, None)
            await self.visits.clear(device, 'offline')
            await self.broadcast()
        return True

    def state(self, device):
        relation = self.store.relation(device)
        if relation:
            partner = self.sessions.get(relation['partner_id'])
            relation['capabilities'] = partner.capabilities if partner and self.available(relation['partner_id']) != 'offline' else None
            relation['visitSupport'] = bool(partner and partner.visits)
            relation['allowVisits'] = bool(partner and partner.allow_visits)
        return {'relation': relation, 'partner_status': self.available(relation['partner_id']) if relation else 'offline',
                'ended_id': self.store.device(device)['ended_id'], 'server_time': time.time()}

    async def send(self, device, data):
        session = self.sessions.get(device)
        if not session:
            return False
        try:
            await asyncio.wait_for(session.socket.send_json(data), 2)
            return True
        except (OSError, RuntimeError, asyncio.TimeoutError, WebSocketDisconnect):
            return False

    async def reply(self, device, request_id, data=None, error=None):
        await self.send(device, {'type': 'result', 'request_id': request_id, 'ok': not error,
                                 'data': data or {}, 'code': error.code if error else '',
                                 'message': error.message if error else ''})

    async def broadcast(self):
        for device in list(self.sessions):
            await self.send(device, {'type': 'state', **self.state(device)})
        await self.rally.broadcast_presence()

    async def clear_relation_actions(self, relation_id):
        for key, action in list(self.pending.items()):
            if key[0] == relation_id:
                self.pending.pop(key, None)
                await self.reply(action['sender'], action['request_id'], error=DomainError('unlinked', '搭子连接已解除'))

    async def command(self, device, msg):
        request_id = msg.get('request_id', '')
        kind = msg.get('type')
        session = self.sessions[device]
        if self.store.device(device)['disabled']:
            raise DomainError('access', '设备已被管理员禁用')
        session.last = time.monotonic()
        self.limit(('messages', device), 60, 10)
        if kind == 'heartbeat':
            await self.send(device, {'type': 'heartbeat', 'server_time': time.time()})
            await self.send(device, {'type': 'state', **self.state(device)})
            return
        if kind == 'profile':
            info = validate_device_info(msg['device_info']) if 'device_info' in msg else None
            if 'client_type' in msg:
                self.store.validate_client_type(msg['client_type'])
            if type(msg.get('paused')) is not bool or type(msg.get('visible')) is not bool:
                raise DomainError('format', '接收状态格式不正确')
            if session.protocol == 2:
                if type(msg.get('visitSupport', False)) is not bool or type(msg.get('allowVisits', False)) is not bool:
                    raise DomainError('format', '串门偏好格式不正确')
                capabilities = validate_capabilities(msg.get('capabilities'))
                character = capabilities['packageId'] if capabilities else ''
                previous = session.capabilities
                if previous and capabilities and previous['revision'] == capabilities['revision'] and previous != capabilities:
                    raise DomainError('format', '角色能力变化时需要新的修订标识')
                if (previous or {}).get('revision') != (capabilities or {}).get('revision'):
                    await self.visits.clear(device, 'character_changed')
                    for key, pending in list(self.pending.items()):
                        if pending['target'] == device:
                            self.pending.pop(key, None)
                            await self.reply(pending['sender'], pending['request_id'], error=DomainError('stale', '对方角色已切换，请刷新互动'))
                session.capabilities = capabilities
                session.visits = msg.get('visitSupport', False)
                session.allow_visits = msg.get('allowVisits', False)
            else:
                character = msg.get('character')
                if character not in ('lulu', 'lumei'):
                    raise DomainError('character', '旧版本不支持该角色，请升级')
            self.store.character(device, character)
            if info is not None:
                self.store.update_device_info(device, info)
            if 'client_type' in msg:
                self.store.client_type(device, msg['client_type'])
            session.paused, session.visible, session.ready = msg['paused'], msg['visible'], True
            if session.paused or not session.visible or not session.visits:
                await self.visits.clear(device)
            elif not session.allow_visits:
                await self.visits.clear(device, incoming_only=True)
            await self.reply(device, request_id, self.state(device))
            await self.broadcast()
        elif kind == 'invite':
            self.limit(('invite', device), 5, 60)
            await self.reply(device, request_id, self.store.invite(device))
        elif kind == 'revoke':
            self.store.revoke(device)
            await self.reply(device, request_id)
        elif kind == 'redeem':
            self.limit(('redeem', device), 10, 60)
            if not isinstance(msg.get('code'), str):
                raise DomainError('format', '邀请码格式不正确')
            self.store.redeem(device, msg['code'])
            await self.reply(device, request_id, self.state(device))
            await self.broadcast()
        elif kind == 'unlink':
            expected = msg.get('relation_id')
            if not isinstance(expected, str) or len(expected) > 64:
                raise DomainError('format', '连接标识不正确')
            partner = self.store.unlink(device, expected)
            if partner:
                await self.visits.clear(device, 'unlinked')
                await self.clear_relation_actions(expected)
            await self.reply(device, request_id, self.state(device))
            await self.broadcast()
        elif kind == 'action':
            await self.action(device, msg)
        elif kind == 'ack':
            await self.ack(device, msg)
        elif kind in ('visit_start', 'visit_end', 'visit_ready', 'visit_action', 'visit_receipt') and session.protocol == 2:
            await self.visits.command(device, msg)
        elif kind in ('rally_create', 'rally_join', 'rally_leave', 'rally_name', 'rally_alert', 'rally_limit', 'rally_chat') and session.protocol == 2:
            await self.rally.command(device, msg)
        else:
            raise DomainError('format', '不支持的操作')

    async def action(self, device, msg):
        relation = self.store.relation(device)
        if not relation or relation['id'] != msg.get('relation_id'):
            raise DomainError('unlinked', '搭子连接已变化，请等待同步')
        if not self.sessions[device].ready:
            raise DomainError('connecting', '正在同步连接，请稍候')
        protocol = self.sessions[device].protocol
        kind, action_id, count = msg.get('action'), msg.get('action_id'), msg.get('count', 1)
        if not isinstance(action_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', action_id):
            raise DomainError('format', '动作格式不正确')
        if protocol == 1 and kind not in ('pinch', 'bonk'):
            raise DomainError('format', '旧版本不支持该互动')
        if protocol == 1 and (type(count) is not int or not 1 <= count <= 5):
            raise DomainError('format', '一次最多合并五个动作')
        key = (relation['id'], action_id)
        if key in self.seen:
            raise DomainError('duplicate', '该动作已处理，不会重复播放')
        self.limit(('actions', device), 4, 1)
        target = relation['partner_id']
        status = self.available(target)
        if status != 'available':
            raise DomainError(status, '对方暂不接收互动' if status == 'paused' else '对方暂不可达')
        target_session = self.sessions[target]
        if target_session.protocol != protocol:
            raise DomainError('upgrade', '请双方升级到同一互动协议版本')
        if protocol == 2:
            capabilities = target_session.capabilities
            interaction = msg.get('interactionId')
            if not capabilities or msg.get('capabilityRevision') != capabilities['revision']:
                raise DomainError('stale', '对方角色已变化，请刷新互动')
            if not isinstance(interaction, str) or not any(item['id'] == interaction for item in capabilities['interactions']):
                raise DomainError('unsupported', '对方当前角色不支持该互动')
            event = {'type': 'action', 'interactionId': interaction, 'capabilityRevision': capabilities['revision'],
                     'action_id': action_id, 'relation_id': relation['id'], 'expires_at': time.time()+5}
        else:
            event = {'type': 'action', 'action': kind, 'action_id': action_id, 'relation_id': relation['id'],
                     'count': count, 'expires_at': time.time() + 5}
        self.seen[key] = time.monotonic() + 30
        self.pending[key] = {'sender': device, 'target': target, 'request_id': msg['request_id'],
                             'expires': time.monotonic() + 5}
        if not await self.send(target, event):
            self.pending.pop(key, None)
            raise DomainError('offline', '对方暂不可达')

    async def ack(self, device, msg):
        relation_id, action_id = msg.get('relation_id'), msg.get('action_id')
        if not isinstance(relation_id, str) or not isinstance(action_id, str):
            raise DomainError('format', '动作回执格式不正确')
        key = (relation_id, action_id)
        action = self.pending.get(key)
        if not action or action['target'] != device:
            return
        relation = self.store.relation(device)
        if not relation or relation['id'] != relation_id:
            return
        self.pending.pop(key, None)
        if time.monotonic() > action['expires']:
            await self.reply(action['sender'], action['request_id'], error=DomainError('unknown', '未确认送达'))
        elif msg.get('accepted') is True and self.available(device) == 'available':
            await self.reply(action['sender'], action['request_id'], {'accepted': True, 'action_id': action_id,
                'status': 'queued' if msg.get('status') == 'queued' else 'started'})
        else:
            await self.reply(action['sender'], action['request_id'], error=DomainError('declined', '对方当前无法播放互动'))

    async def maintenance(self):
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            for device, session in list(self.sessions.items()):
                if now - session.last > 30:
                    with suppress(Exception):
                        await session.socket.close(code=4000)
                    if self.sessions.get(device) is session:
                        self.sessions.pop(device, None)
                        await self.visits.clear(device, 'offline')
                    await self.broadcast()
            await self.visits.sweep()
            self.rally.sweep()
            self.sample_trend()
            self.hosting.prune()
            for key, action in list(self.pending.items()):
                if now >= action['expires']:
                    self.pending.pop(key, None)
                    await self.reply(action['sender'], action['request_id'], error=DomainError('unknown', '未确认送达'))
            self.seen = {key: expiry for key, expiry in self.seen.items() if expiry > now}
            for key, entries in list(self.limits.items()):
                if not entries or now - entries[-1] > 3600:
                    self.limits.pop(key, None)


def create_app(db_path=None):
    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(app.state.relay.maintenance())
        try:
            yield
        finally:
            task.cancel()
            try:
                with suppress(asyncio.CancelledError):
                    await task
            finally:
                try:
                    app.state.relay.visits.storage.cleanup()
                finally:
                    app.state.relay.store.close()

    app = FastAPI(title='下班搭子 Relay', lifespan=lifespan, docs_url=None, redoc_url=None)
    store = Store(db_path or os.environ.get('OFFWORK_DATABASE_URL') or os.environ.get('OFFWORK_DB', str(Path('data') / 'offwork.sqlite3')),
                  rally_max_members=int(os.environ.get('OFFWORK_RALLY_MAX_MEMBERS', '100')))
    relay = app.state.relay = Relay(store)
    try:
        relay.hosting = Hosting(store)
    except BaseException:
        try:
            relay.visits.storage.cleanup()
        finally:
            store.close()
        raise
    install_routes(app, relay)
    install_admin_routes(app, relay)
    from .resources import install as install_resources
    install_resources(app)

    @app.get('/health')
    async def health():
        return {'ok': True, 'protocol': 2, 'supported_protocols': [1, 2],
                'server': relay.hosting.public(),
                'features': ['visits/v1', 'rally/v1', 'rally-signals/v1', 'rally-settings/v1', 'rally-chat/v1']}

    @app.get('/api/server/info')
    async def info():
        return {**relay.hosting.public(), 'protocols': [2], 'features': (await health())['features']}

    @app.post('/v1/devices')
    @app.post('/v2/devices')
    async def register(request: Request):
        try:
            relay.limit(('register', request.client.host), 20, 3600)
        except DomainError as error:
            raise HTTPException(429, error.message)
        try:
            data = await read_json(request, allow_empty=True)
            if not isinstance(data, dict):
                raise HTTPException(400, '请求格式不正确')
            return relay.hosting.register(request.headers.get('x-offwork-client-type', 'desktop'), data.get('access_code'))
        except ValueError:
            raise HTTPException(400, '请求格式不正确') from None
        except DomainError as error:
            raise HTTPException(403 if error.code == 'access' else 400, error.message)

    async def connect(socket: WebSocket, protocol=1):
        authorization = socket.headers.get('authorization', '')
        token = authorization[7:] if authorization.startswith('Bearer ') else ''
        device = store.authenticate(token) if token else None
        if not device:
            await socket.accept()
            await socket.close(code=4401)
            return
        await socket.accept()
        previous = relay.sessions.get(device)
        session = Session(socket, time.monotonic(), protocol=protocol, ip=client_ip(socket), connected=time.time())
        relay.sessions[device] = session
        if previous:
            await relay.visits.clear(device, 'reconnected')
            with suppress(Exception):
                await previous.socket.close(code=4001)
        try:
            await relay.send(device, {'type': 'state', **relay.state(device)})
            await relay.rally.push(device)
            while relay.sessions.get(device) is session:
                raw = await socket.receive_text()
                if relay.sessions.get(device) is not session:
                    break
                if len(raw.encode('utf-8')) > (32768 if protocol == 2 else 8192):
                    await socket.close(code=1009)
                    break
                request_id = ''
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict) or not isinstance(msg.get('request_id', ''), str) or len(msg.get('request_id', '')) > 64:
                        raise DomainError('format', '请求格式不正确')
                    request_id = msg.get('request_id', '')
                    if msg.get('type') not in ('heartbeat', 'ack', 'visit_receipt') and not request_id:
                        raise DomainError('format', '缺少请求标识')
                    await relay.command(device, msg)
                except (ValueError, TypeError, RecursionError, DomainError) as error:
                    error = error if isinstance(error, DomainError) else DomainError('format', '请求格式不正确')
                    await relay.reply(device, request_id, error=error)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if relay.sessions.get(device) is session:
                relay.sessions.pop(device, None)
                await relay.visits.clear(device, 'offline')
                await relay.broadcast()

    @app.websocket('/v1/connect')
    async def connect_v1(socket: WebSocket):
        await connect(socket, 1)

    @app.websocket('/v2/connect')
    async def connect_v2(socket: WebSocket):
        await connect(socket, 2)

    return app
