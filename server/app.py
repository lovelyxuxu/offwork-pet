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


@dataclass
class Session:
    socket: WebSocket
    last: float
    paused: bool = False
    visible: bool = False
    ready: bool = False


class Relay:
    def __init__(self, store):
        self.store = store
        self.sessions = {}
        self.limits = defaultdict(deque)
        self.pending = {}
        self.seen = {}

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

    def state(self, device):
        relation = self.store.relation(device)
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

    async def clear_relation_actions(self, relation_id):
        for key, action in list(self.pending.items()):
            if key[0] == relation_id:
                self.pending.pop(key, None)
                await self.reply(action['sender'], action['request_id'], error=DomainError('unlinked', '搭子连接已解除'))

    async def command(self, device, msg):
        request_id = msg.get('request_id', '')
        kind = msg.get('type')
        session = self.sessions[device]
        session.last = time.monotonic()
        self.limit(('messages', device), 60, 10)
        if kind == 'heartbeat':
            await self.send(device, {'type': 'heartbeat', 'server_time': time.time()})
            await self.send(device, {'type': 'state', **self.state(device)})
            return
        if kind == 'profile':
            if type(msg.get('paused')) is not bool or type(msg.get('visible')) is not bool:
                raise DomainError('format', '接收状态格式不正确')
            self.store.character(device, msg.get('character'))
            session.paused, session.visible, session.ready = msg['paused'], msg['visible'], True
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
                await self.clear_relation_actions(expected)
            await self.reply(device, request_id, self.state(device))
            await self.broadcast()
        elif kind == 'action':
            await self.action(device, msg)
        elif kind == 'ack':
            await self.ack(device, msg)
        else:
            raise DomainError('format', '不支持的操作')

    async def action(self, device, msg):
        relation = self.store.relation(device)
        if not relation or relation['id'] != msg.get('relation_id'):
            raise DomainError('unlinked', '搭子连接已变化，请等待同步')
        if not self.sessions[device].ready:
            raise DomainError('connecting', '正在同步连接，请稍候')
        kind, action_id, count = msg.get('action'), msg.get('action_id'), msg.get('count', 1)
        if kind not in ('pinch', 'bonk') or not isinstance(action_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', action_id):
            raise DomainError('format', '动作格式不正确')
        if type(count) is not int or not 1 <= count <= 5:
            raise DomainError('format', '一次最多合并五个动作')
        key = (relation['id'], action_id)
        if key in self.seen:
            raise DomainError('duplicate', '该动作已处理，不会重复播放')
        self.limit(('actions', device), 4, 1)
        target = relation['partner_id']
        status = self.available(target)
        if status != 'available':
            raise DomainError(status, '对方暂不接收互动' if status == 'paused' else '对方暂不可达')
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
            await self.reply(action['sender'], action['request_id'], {'accepted': True, 'action_id': action_id})
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
                    await self.broadcast()
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
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        app.state.relay.store.close()

    app = FastAPI(title='下班搭子 Relay', lifespan=lifespan, docs_url=None, redoc_url=None)
    store = Store(db_path or os.environ.get('OFFWORK_DB', str(Path('data') / 'offwork.sqlite3')))
    relay = app.state.relay = Relay(store)

    @app.get('/health')
    async def health():
        return {'ok': True, 'protocol': 1}

    @app.post('/v1/devices')
    async def register(request: Request):
        try:
            relay.limit(('register', request.client.host), 20, 3600)
        except DomainError as error:
            raise HTTPException(429, error.message)
        return store.register()

    @app.websocket('/v1/connect')
    async def connect(socket: WebSocket):
        authorization = socket.headers.get('authorization', '')
        token = authorization[7:] if authorization.startswith('Bearer ') else ''
        device = store.authenticate(token) if token else None
        if not device:
            await socket.accept()
            await socket.close(code=4401)
            return
        await socket.accept()
        previous = relay.sessions.get(device)
        session = Session(socket, time.monotonic())
        relay.sessions[device] = session
        if previous:
            with suppress(Exception):
                await previous.socket.close(code=4001)
        try:
            await relay.send(device, {'type': 'state', **relay.state(device)})
            while relay.sessions.get(device) is session:
                raw = await socket.receive_text()
                if relay.sessions.get(device) is not session:
                    break
                if len(raw) > 8192:
                    await socket.close(code=1009)
                    break
                request_id = ''
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict) or not isinstance(msg.get('request_id', ''), str) or len(msg.get('request_id', '')) > 64:
                        raise DomainError('format', '请求格式不正确')
                    request_id = msg.get('request_id', '')
                    if msg.get('type') not in ('heartbeat', 'ack') and not request_id:
                        raise DomainError('format', '缺少请求标识')
                    await relay.command(device, msg)
                except (ValueError, TypeError, DomainError) as error:
                    error = error if isinstance(error, DomainError) else DomainError('format', '请求格式不正确')
                    await relay.reply(device, request_id, error=error)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if relay.sessions.get(device) is session:
                relay.sessions.pop(device, None)
                await relay.broadcast()

    return app
