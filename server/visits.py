"""Temporary, paired visitor sessions and bounded private package transfer."""
import hashlib
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .store import DomainError
from offwork_pet.package_limits import MAX_PACKAGE_ARCHIVE

MAX_ARCHIVE = MAX_PACKAGE_ARCHIVE
VISIT_SECONDS = 180
TERMINAL = {'completed', 'cancelled', 'expired', 'error', 'busy', 'cooldown', 'condition', 'unsupported', 'paused'}


class Visits:
    def __init__(self, relay):
        self.relay = relay
        self.items = {}
        self.storage = TemporaryDirectory(prefix='offwork-visits-')

    def public(self, item):
        return {key: item[key] for key in ('id', 'relation_id', 'sender', 'target', 'capabilities',
                'sha256', 'size', 'stage', 'expires_at')}

    def get(self, device, visit_id):
        item = self.items.get(visit_id) if isinstance(visit_id, str) else None
        relation = self.relay.store.relation(device)
        if (not item or device not in (item['sender'], item['target']) or not relation
                or relation['id'] != item['relation_id'] or time.time() >= item['expires_at']):
            raise DomainError('stale', '串门已经结束')
        return item

    async def end(self, item, reason='left'):
        if self.items.pop(item['id'], None) is None:
            return
        for action_id, action in list(item['actions'].items()):
            if not action['replied']:
                await self.relay.reply(item['sender'], action['request_id'],
                                       error=DomainError('cancelled', '串门已结束，动作已取消'))
            await self.relay.send(item['sender'], {'type': 'visit_action_status', 'visit_id': item['id'],
                'action_id': action_id, 'status': 'cancelled'})
        for device in (item['sender'], item['target']):
            await self.relay.send(device, {'type': 'visit_end', 'visit_id': item['id'], 'reason': reason})
        try:
            Path(item['path']).unlink(missing_ok=True)
        except PermissionError:
            pass  # A streaming response/upload may still hold the temporary file.

    async def clear(self, device, reason='unavailable', incoming_only=False):
        for item in list(self.items.values()):
            if item['target'] == device or not incoming_only and item['sender'] == device:
                await self.end(item, reason)

    async def command(self, device, msg):
        relay = self.relay
        kind, rid = msg['type'], msg.get('request_id', '')
        if kind == 'visit_start':
            relation = relay.store.relation(device)
            session = relay.sessions[device]
            if not relation or relation['id'] != msg.get('relation_id'):
                raise DomainError('unlinked', '请先连接搭子')
            target = relation['partner_id']
            peer = relay.sessions.get(target)
            if not session.visits or not peer or not peer.visits:
                raise DomainError('upgrade', '请双方升级到支持串门的版本')
            if relay.available(device) != 'available' or relay.available(target) != 'available' or not peer.allow_visits:
                raise DomainError('paused', '当前暂不接待串门')
            caps = session.capabilities
            if not caps or msg.get('capabilityRevision') != caps['revision']:
                raise DomainError('stale', '自己的角色已变化，请重新串门')
            sha, size = msg.get('sha256'), msg.get('size')
            if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha) or type(size) is not int or not 1 <= size <= MAX_ARCHIVE:
                raise DomainError('format', '串门角色包大小或摘要不正确')
            if len(self.items) >= 16 or any(i['relation_id'] == relation['id'] for i in self.items.values()):
                raise DomainError('busy', '已经在串门，请先结束当前串门')
            relay.limit(('visits', device), 6, 60)
            vid = uuid.uuid4().hex
            item = {'id': vid, 'relation_id': relation['id'], 'sender': device, 'target': target,
                'capabilities': caps, 'sha256': sha, 'size': size, 'stage': 'preparing',
                'expires_at': time.time()+60, 'path': str(Path(self.storage.name)/(vid+'.petpack')),
                'actions': {}, 'seen': set(), 'uploading': False}
            self.items[vid] = item
            await relay.reply(device, rid, self.public(item))
            return
        item = self.get(device, msg.get('visit_id'))
        if kind == 'visit_end':
            await self.end(item, 'dismissed' if device == item['target'] else 'left')
            await relay.reply(device, rid)
        elif kind == 'visit_ready':
            if device != item['target'] or item['stage'] != 'loading':
                raise DomainError('stale', '串门状态已变化')
            if msg.get('accepted') is not True:
                await self.end(item, 'package_error')
            else:
                item['stage'], item['expires_at'] = 'active', time.time()+VISIT_SECONDS
                for who in (item['sender'], item['target']):
                    await relay.send(who, {'type': 'visit_active', 'visit': self.public(item)})
            await relay.reply(device, rid)
        elif kind == 'visit_action':
            if device != item['sender'] or item['stage'] != 'active':
                raise DomainError('stale', '等搭子接待后再选择动作')
            if relay.available(item['target']) != 'available':
                raise DomainError('paused', '对方暂不接待串门')
            aid, interaction = msg.get('action_id'), msg.get('interactionId')
            if not isinstance(aid, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', aid):
                raise DomainError('format', '动作编号不正确')
            if not any(e['id'] == interaction for e in item['capabilities']['interactions']):
                raise DomainError('unsupported', '来访角色没有这个互动')
            if aid in item['seen'] or len(item['actions']) >= 16 or len(item['seen']) >= 256:
                raise DomainError('busy', '动作重复或过多，请稍后再试')
            relay.limit(('visit-actions', device), 4, 1)
            item['seen'].add(aid)
            item['actions'][aid] = {'request_id': rid, 'replied': False, 'stage': 'pending',
                                    'deadline': time.monotonic()+7}
            await relay.send(item['target'], {'type': 'visit_action', 'visit_id': item['id'],
                'action_id': aid, 'interactionId': interaction, 'expires_at': time.time()+5})
        elif kind == 'visit_receipt':
            if device != item['target']:
                raise DomainError('format', '不能确认对方桌面的动作')
            aid, status = msg.get('action_id'), msg.get('status')
            action = item['actions'].get(aid) if isinstance(aid, str) else None
            if not action or status not in TERMINAL | {'queued', 'started'}:
                return
            if action['stage'] == status:
                return
            if action['stage'] == 'started' and status == 'queued':
                return
            action['stage'] = status
            if not action['replied']:
                accepted = status in ('queued', 'started', 'completed')
                await relay.reply(item['sender'], action['request_id'], {'status': status} if accepted else None,
                    None if accepted else DomainError(status, '访客暂时无法播放：'+status))
                action['replied'] = True
            await relay.send(item['sender'], {'type': 'visit_action_status', 'visit_id': item['id'],
                                             'action_id': aid, 'status': status})
            if status in TERMINAL:
                item['actions'].pop(aid, None)
            else:
                action['deadline'] = min(time.monotonic()+120, time.monotonic()+max(0, item['expires_at']-time.time()))

    async def sweep(self):
        for item in list(self.items.values()):
            if time.time() >= item['expires_at']:
                await self.end(item, 'expired')
                continue
            for aid, action in list(item['actions'].items()):
                if time.monotonic() >= action['deadline']:
                    if not action['replied']:
                        await self.relay.reply(item['sender'], action['request_id'], error=DomainError('expired', '动作确认已超时'))
                    await self.relay.send(item['sender'], {'type': 'visit_action_status', 'visit_id': item['id'],
                                                          'action_id': aid, 'status': 'expired'})
                    item['actions'].pop(aid, None)


def install_routes(app, relay):
    visits = relay.visits

    def authorize(request, vid, role):
        auth = request.headers.get('authorization', '')
        device = relay.store.authenticate(auth[7:]) if auth.startswith('Bearer ') else None
        if not device:
            raise HTTPException(401, '设备身份失效')
        try:
            item = visits.get(device, vid)
        except DomainError:
            raise HTTPException(404, '串门已结束')
        if item[role] != device:
            raise HTTPException(403, '角色包仅供本次搭子串门使用')
        return item

    @app.put('/v2/visits/{vid}/package')
    async def upload(vid: str, request: Request):
        item = authorize(request, vid, 'sender')
        if item['stage'] != 'preparing' or item['uploading']:
            raise HTTPException(409, '角色包已在传输')
        item['uploading'] = True
        size, digest = 0, hashlib.sha256()
        try:
            with open(item['path'], 'wb') as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > item['size'] or size > MAX_ARCHIVE or visits.items.get(vid) is not item:
                        raise HTTPException(413, '角色包超限或串门已结束')
                    digest.update(chunk)
                    output.write(chunk)
            authorize(request, vid, 'sender')
            if size != item['size'] or digest.hexdigest() != item['sha256']:
                raise HTTPException(400, '角色包校验失败')
            item['stage'] = 'loading'
            await relay.send(item['target'], {'type': 'visit_offer', 'visit': visits.public(item)})
            return {'ok': True}
        except BaseException:
            await visits.end(item, 'package_error')
            raise
        finally:
            item['uploading'] = False
            if vid not in visits.items:
                Path(item['path']).unlink(missing_ok=True)

    @app.get('/v2/visits/{vid}/package')
    async def download(vid: str, request: Request):
        item = authorize(request, vid, 'target')
        if item['stage'] not in ('loading', 'active'):
            raise HTTPException(409, '角色包尚未准备好')
        def cleanup():
            if vid not in visits.items:
                try:
                    Path(item['path']).unlink(missing_ok=True)
                except PermissionError:
                    pass
        return FileResponse(item['path'], media_type='application/octet-stream',
            headers={'Cache-Control': 'no-store'}, background=BackgroundTask(cleanup))
