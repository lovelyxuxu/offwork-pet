"""Rally groups: membership persists; alert signals are ephemeral and never stored."""
import asyncio
import re
import time

from .store import DomainError

LEVELS = ('safe', 'warning', 'danger')


class Rally:
    def __init__(self, relay):
        self.relay = relay
        self.seen = {}

    def state(self, device):
        rally = self.relay.store.rally(device)
        if rally:
            members = []
            for member in rally['members']:
                session = self.relay.sessions.get(member['device_id'])
                name = member['name'] or (session.capabilities.get('displayName') if session and session.capabilities else '') or '搭子'
                members.append({'name': name, 'status': self.relay.available(member['device_id']),
                                'self': member['device_id'] == device})
            rally = {'id': rally['id'], 'code': rally['code'], 'name': rally['name'],
                     'owner_self': rally['owner'] == device, 'members': members, 'member_limit': rally['member_limit']}
        return {'rally': rally, 'rally_limits': {'max_members': self.relay.store.rally_max_members}}

    async def push(self, device):
        await self.relay.send(device, {'type': 'rally_state', **self.state(device), 'server_time': time.time()})

    async def broadcast_presence(self):
        for device in list(self.relay.sessions):
            if self.relay.store.rally(device):
                await self.push(device)

    def sweep(self):
        now = time.monotonic()
        self.seen = {key: expiry for key, expiry in self.seen.items() if expiry > now}

    async def command(self, device, msg):
        relay = self.relay
        kind, rid = msg['type'], msg.get('request_id', '')
        store = relay.store
        if kind == 'rally_create':
            relay.limit(('rally_create', device), 5, 600)
            name = msg.get('name')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24:
                raise DomainError('format', '集会名称需要 1–24 字')
            member_name = msg.get('member_name', '')
            if not isinstance(member_name, str) or len(member_name.strip()) > 24:
                raise DomainError('format', '集会名字最多 24 字')
            store.rally_create(device, name.strip(), member_name.strip(), member_limit=msg.get('member_limit'))
            await relay.reply(device, rid, self.state(device))
        elif kind == 'rally_join':
            relay.limit(('rally_join', device), 20, 600)
            code = msg.get('code')
            if not isinstance(code, str):
                raise DomainError('format', '集会码格式不正确')
            name = msg.get('name', '')
            if not isinstance(name, str) or len(name.strip()) > 24:
                raise DomainError('format', '集会名字最多 24 字')
            rally = store.rally_join(device, code, name.strip())
            await relay.reply(device, rid, self.state(device))
            for member in rally['members']:
                if member['device_id'] != device:
                    await self.push(member['device_id'])
        elif kind == 'rally_leave':
            current = store.rally(device)
            store.rally_leave(device)
            await relay.reply(device, rid, {'rally': None})
            if current:
                for member in current['members']:
                    if member['device_id'] != device:
                        await self.push(member['device_id'])
        elif kind == 'rally_name':
            name = msg.get('name')
            if not isinstance(name, str) or len(name.strip()) > 24:
                raise DomainError('format', '集会名字最多 24 字')
            current = store.rally(device)
            if not current:
                raise DomainError('unjoined', '先加入集会，再修改名字')
            store.rally_name(device, name.strip())
            await relay.reply(device, rid, self.state(device))
            for member in current['members']:
                if member['device_id'] != device:
                    await self.push(member['device_id'])
        elif kind == 'rally_limit':
            current = store.rally_set_limit(device, msg.get('member_limit'))
            await relay.reply(device, rid, self.state(device))
            for member in current['members']:
                if member['device_id'] != device:
                    await self.push(member['device_id'])
        elif kind == 'rally_alert':
            await self.alert(device, msg)
        elif kind == 'rally_chat':
            await self.chat(device, msg)
        else:
            raise DomainError('format', '不支持的集会操作')

    async def chat(self, device, msg):
        """Forward live text; retain history only under this instance's explicit policy."""
        relay = self.relay
        rally = relay.store.rally(device)
        if not rally:
            raise DomainError('unjoined', '请先加入集会')
        text, identifier = msg.get('text'), msg.get('message_id')
        if (not isinstance(text, str) or not 1 <= len(text.strip()) <= 200
                or any(ord(char) < 32 or ord(char) == 127 for char in text)
                or not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identifier)):
            raise DomainError('format', '即时消息需要 1–200 字的单行文字和有效标识')
        key = ('chat', rally['id'], device, identifier)
        if key in self.seen:
            raise DomainError('duplicate', '消息已转发，不重复发送')
        relay.limit(('rally_chat', device), 6, 10)
        self.seen[key] = time.monotonic() + 30  # IDs only, never text.
        session = relay.sessions[device]
        source = next(member['name'] for member in rally['members'] if member['device_id'] == device)
        source = source or (session.capabilities or {}).get('displayName') or '搭子'
        now = time.time()
        relay.hosting.save_chat(rally['id'], device, source, text.strip())
        event = {'type': 'rally_chat', 'rally_id': rally['id'], 'message_id': identifier,
                 'from': source, 'text': text.strip(), 'server_time': now, 'expires_at': now + 5}
        targets = [member['device_id'] for member in rally['members']
                   if member['device_id'] != device and (target := relay.sessions.get(member['device_id']))
                   and target.ready and target.protocol == 2]
        results = await asyncio.gather(*(relay.send(target, event) for target in targets))
        await relay.reply(device, msg['request_id'], {'forwarded': sum(results)})

    async def alert(self, device, msg):
        relay = self.relay
        rally = relay.store.rally(device)
        if not rally:
            raise DomainError('unjoined', '先加入集会，再广播警报')
        level = msg.get('level')
        if level not in LEVELS:
            raise DomainError('format', '未知警报级别')
        label, color = msg.get('label'), msg.get('color')
        if label is not None or color is not None:
            if (not isinstance(label, str) or not 1 <= len(label.strip()) <= 24
                    or any(ord(char) < 32 or ord(char) == 127 for char in label)
                    or not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color)):
                raise DomainError('format', '自定义警报需要 1–24 字的名称和有效颜色')
        alert_id = msg.get('alert_id')
        if not isinstance(alert_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', alert_id):
            raise DomainError('format', '警报标识不正确')
        relay.limit(('rally_alert', device), 60, 10)
        session = relay.sessions.get(device)
        source = next((member['name'] for member in rally['members'] if member['device_id'] == device), '')
        source = source or (session.capabilities.get('displayName') if session and session.capabilities else '') or '搭子'
        event = {'type': 'rally_alert', 'rally_id': rally['id'], 'alert_id': alert_id, 'level': level,
                 'from': source, 'expires_at': time.time() + 6, 'server_time': time.time()}
        if label is not None:
            event.update(label=label.strip(), color=color.upper())
        delivered = 0
        for member in rally['members']:
            target = member['device_id']
            target_session = relay.sessions.get(target)
            if target != device and target_session and target_session.ready and await relay.send(target, event):
                delivered += 1
        await relay.reply(device, msg['request_id'], {'delivered': delivered, 'members': len(rally['members']) - 1})
