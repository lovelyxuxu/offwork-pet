"""Instance policy, controlled enrollment and optional server-owned chat history."""
import json
import os
from pathlib import Path
import secrets
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

from .store import DomainError, digest
from .database import transaction
from .http_body import read_json


class Hosting:
    def __init__(self, store):
        self.store, self.db = store, store.db
        stored = {row['key']: json.loads(row['value']) for row in self.db.execute('SELECT key,value FROM server_settings')}
        defaults = {'instance_id': uuid.uuid4().hex, 'name': os.getenv('OFFWORK_SERVER_NAME', '下班搭子'),
                    'registration': stored.get('registration', os.getenv('OFFWORK_REGISTRATION', 'open')),
                    'chat_days': stored['chat_days'] if 'chat_days' in stored else int(os.getenv('OFFWORK_CHAT_DAYS', '0')),
                    'cookie_secret': secrets.token_hex(32)}
        if defaults['registration'] not in ('open', 'invite', 'closed') or not 0 <= defaults['chat_days'] <= 3650:
            raise ValueError('Invalid registration policy or chat retention days')
        with transaction(self.db):
            for key, value in defaults.items():
                self.db.execute('INSERT INTO server_settings VALUES(?,?) ON CONFLICT(key) DO NOTHING',
                                (key, json.dumps(value, ensure_ascii=False)))
        self._settings = {row['key']: json.loads(row['value']) for row in self.db.execute('SELECT key,value FROM server_settings')}
        self._next_prune = 0

    def get(self, key):
        return self._settings[key]

    def public(self):
        return {key: self.get(key) for key in ('instance_id', 'name', 'registration', 'chat_days')}

    def audit(self, action, target=''):
        self.db.execute('INSERT INTO admin_audit VALUES(?,?,?,?)', (uuid.uuid4().hex, action, target, time.time()))

    def register(self, client_type, code):
        policy = self.get('registration')
        if policy == 'closed':
            raise DomainError('access', '服务器已关闭新设备接入')
        with transaction(self.db):
            if policy == 'invite':
                if not isinstance(code, str) or not 1 <= len(code) <= 128:
                    raise DomainError('access', '请输入服务器接入码')
                result = self.db.execute('UPDATE access_invites SET remaining=remaining-1 '
                    'WHERE code_hash=? AND remaining>0 AND expires>?', (digest(code), time.time()))
                if result.rowcount != 1:
                    raise DomainError('access', '接入码无效、过期或已用完')
            identity = self.store.register(client_type)
            return identity

    def update_settings(self, values):
        with transaction(self.db):
            for key, value in values.items():
                self.db.execute('UPDATE server_settings SET value=? WHERE key=?', (json.dumps(value), key))
            self.audit('settings')
            # Retention changes and deletion are one atomic operation.
            self._prune(values['chat_days'])
        self._settings.update(values)
        self._next_prune = time.monotonic()+60

    def save_chat(self, rally, device, name, body):
        days = self.get('chat_days')
        if days:
            self.db.execute('INSERT INTO chat_messages VALUES(?,?,?,?,?,?)',
                            (uuid.uuid4().hex, rally, device, name, body, time.time()))

    def _prune(self, days):
        if days:
            self.db.execute('DELETE FROM chat_messages WHERE created<?', (time.time()-days*86400,))
        else:
            self.db.execute('DELETE FROM chat_messages')
        self.db.execute('DELETE FROM access_invites WHERE expires<?', (time.time(),))

    def prune(self):
        now = time.monotonic()
        if now >= self._next_prune:
            with transaction(self.db):
                self._prune(self.get('chat_days'))
            self._next_prune = now+60


def install(app, relay, guard, mutate_guard):
    hosting = relay.hosting

    async def body(request):
        data = await read_json(request)
        if not isinstance(data, dict):
            raise HTTPException(400, '请求格式不正确')
        return data

    @app.get('/admin/api/hosting')
    async def settings(request: Request):
        guard(request)
        return {**hosting.public(), 'invites': [dict(row) for row in hosting.db.execute(
                    'SELECT id,expires,remaining FROM access_invites ORDER BY expires DESC LIMIT 100')],
                'devices': [dict(row) for row in hosting.db.execute('SELECT id,disabled,client_type FROM devices')],
                'audit': [dict(row) for row in hosting.db.execute('SELECT * FROM admin_audit ORDER BY created DESC LIMIT 50')]}

    @app.post('/admin/api/hosting')
    async def update(request: Request):
        mutate_guard(request)
        data = await body(request)
        if (set(data) != {'name', 'registration', 'chat_days'} or not isinstance(data['name'], str)
                or not 1 <= len(data['name'].strip()) <= 80 or data['registration'] not in ('open', 'invite', 'closed')
                or type(data['chat_days']) is not int or not 0 <= data['chat_days'] <= 3650):
            raise HTTPException(400, '实例设置无效')
        data['name'] = data['name'].strip()
        hosting.update_settings(data)
        return hosting.public()

    @app.post('/admin/api/access-invites')
    async def invite(request: Request):
        mutate_guard(request)
        data = await body(request)
        hours, uses = data.get('hours', 24), data.get('uses', 1)
        if type(hours) is not int or not 1 <= hours <= 720 or type(uses) is not int or not 1 <= uses <= 1000:
            raise HTTPException(400, '有效期须为 1–720 小时，次数须为 1–1000')
        identifier, code = uuid.uuid4().hex, secrets.token_urlsafe(24)
        with transaction(hosting.db):
            hosting.db.execute('INSERT INTO access_invites VALUES(?,?,?,?)',
                               (identifier, digest(code), time.time()+hours*3600, uses))
            hosting.audit('invite.create', identifier)
        return {'id': identifier, 'code': code}

    @app.delete('/admin/api/access-invites/{identifier}')
    async def revoke(identifier: str, request: Request):
        mutate_guard(request)
        with transaction(hosting.db):
            hosting.db.execute('DELETE FROM access_invites WHERE id=?', (identifier,))
            hosting.audit('invite.revoke', identifier)
        return {'ok': True}

    @app.post('/admin/api/hosting/devices/{device}/disabled')
    async def disable(device: str, request: Request):
        mutate_guard(request)
        data = await body(request)
        if type(data.get('disabled')) is not bool:
            raise HTTPException(400, '禁用状态无效')
        with transaction(hosting.db):
            result = hosting.db.execute('UPDATE devices SET disabled=? WHERE id=?', (int(data['disabled']), device))
            if not result.rowcount:
                raise HTTPException(404, '设备不存在')
            hosting.audit('device.disable' if data['disabled'] else 'device.enable', device)
        if data['disabled']:
            await relay.kick(device)
        return {'ok': True}

    @app.get('/v2/chat/history')
    async def history(request: Request):
        auth = request.headers.get('authorization', '')
        device = relay.store.authenticate(auth[7:]) if auth.startswith('Bearer ') else None
        if not device:
            raise HTTPException(401)
        rally = relay.store.rally(device)
        if not rally:
            raise HTTPException(403, '先加入集会')
        days = hosting.get('chat_days')
        rows = hosting.db.execute('SELECT id,name,body,created FROM chat_messages WHERE rally_id=? AND created>=? '
                                  'ORDER BY created DESC,id DESC LIMIT 100', (rally['id'], time.time()-days*86400)) if days else ()
        return {'items': [dict(row) for row in rows], 'chat_days': days}

    @app.get('/admin/hosting')
    async def page(request: Request):
        return FileResponse(Path(__file__).with_name('hosting.html'))
