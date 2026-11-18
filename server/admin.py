"""Admin console: env-gated credentials, signed cookie, monitoring and management."""
import hmac
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .store import DomainError
from .http_body import read_json

COOKIE = 'admin_session'
MAX_AGE = 7 * 24 * 3600
logger = logging.getLogger('offwork.admin')


def device_metadata(row):
    return {'device_id': row['id'], 'admin_note': row['admin_note'],
            'device_info': json.loads(row['device_info']),
            'info_reported_at': row['info_reported_at']}


def build_rallies(relay):
    rows = []
    for rally in relay.store.all_rallies():
        names = [{'name': m['name'] or '搭子', 'online': relay.available(m['device_id']) != 'offline',
                  'client_type': relay.store.device(m['device_id'])['client_type']}
                 for m in rally['members']]
        rows.append({'code': rally['code'], 'name': rally['name'], 'members': len(names),
                     'online': sum(m['online'] for m in names), 'limit': rally['member_limit'],
                     'max_members': relay.store.rally_max_members,
                     'created': rally['created'], 'member_names': names})
    return rows


def build_devices(relay):
    store = relay.store
    membership = {}
    for rally in store.all_rallies():
        for member in rally['members']:
            membership[member['device_id']] = (rally, member)
    now = time.time()
    devices = []
    for row in store.admin_devices():
        device = row['id']
        session = relay.sessions.get(device)
        pair = membership.get(device)
        member_name = pair[1]['name'] if pair else ''
        capabilities = (session.capabilities if session else None) or {}
        devices.append({'device': device[:8],
                        **device_metadata(row),
                        'name': member_name or capabilities.get('displayName', ''),
                        'character': row['character'],
                        'client_type': row['client_type'],
                        'status': relay.available(device),
                        'protocol': session.protocol if session else None,
                        'ip': session.ip if session else '',
                        'online_seconds': max(0, int(now - session.connected)) if session else 0,
                        'rally': pair[0]['name'] if pair else None,
                        'paired': bool(row['relation_id'])})
    devices.sort(key=lambda item: item['device'])
    return devices


def same_origin(request):
    origin = request.headers.get('origin') or request.headers.get('referer')
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    return parsed.netloc == request.url.netloc and parsed.scheme == request.url.scheme


def build_overview(relay):
    store = relay.store
    rallies = store.all_rallies()
    membership = {}
    for rally in rallies:
        for member in rally['members']:
            membership[member['device_id']] = rally
    now = time.time()
    online = []
    for device, session in relay.sessions.items():
        rally = membership.get(device)
        member_name = ''
        if rally:
            member_name = next((m['name'] for m in rally['members'] if m['device_id'] == device), '')
        capabilities = session.capabilities or {}
        online.append({'device': device[:8],
                       **device_metadata(store.device(device)),
                       'name': member_name or capabilities.get('displayName', ''),
                       'character': store.device(device)['character'],
                       'client_type': store.device(device)['client_type'],
                       'status': relay.available(device),
                       'protocol': session.protocol,
                       'ip': session.ip,
                       'online_seconds': max(0, int(now - session.connected)),
                       'rally': rally['name'] if rally else None,
                       'paired': bool(store.relation(device))})
    online.sort(key=lambda item: item['device'])
    return {'server_time': now, 'totals': {**store.admin_counts(), 'online': len(online)},
            'online': online, 'rallies': build_rallies(relay)}


def install_admin_routes(app, relay):
    username = os.environ.get('OFFWORK_ADMIN_USER', '')
    password = os.environ.get('OFFWORK_ADMIN_PASSWORD', '')
    enabled = bool(username and password)
    secret = hmac.new(relay.hosting.get('cookie_secret').encode(), (username+'\0'+password).encode(), 'sha256').hexdigest()

    def sign(expiry):
        payload = str(int(expiry))
        return payload + '.' + hmac.new(secret.encode(), payload.encode(), 'sha256').hexdigest()

    def authenticated(request):
        payload, _, signature = request.cookies.get(COOKIE, '').partition('.')
        if not payload:
            return False
        expected = hmac.new(secret.encode(), payload.encode(), 'sha256').hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        try:
            return int(payload) > time.time()
        except ValueError:
            return False

    def guard(request):
        if not enabled:
            raise HTTPException(404)
        if not authenticated(request):
            raise HTTPException(401, '请先登录')

    def mutate_guard(request):
        guard(request)
        if not same_origin(request):
            raise HTTPException(403, '跨源请求被拒绝')

    from .hosting import install
    install(app, relay, guard, mutate_guard)

    @app.post('/admin/login')
    async def login(request: Request):
        if not enabled:
            raise HTTPException(404)
        try:
            relay.limit(('admin_login', request.client.host), 10, 600)
        except DomainError as error:
            raise HTTPException(429, error.message)
        try:
            data = await read_json(request)
        except HTTPException as error:
            if error.status_code != 400:
                raise
            data = {}
        user = data.get('username') if isinstance(data, dict) else None
        word = data.get('password') if isinstance(data, dict) else None
        if not (isinstance(user, str) and isinstance(word, str)
                and hmac.compare_digest(user.encode(), username.encode())
                and hmac.compare_digest(word.encode(), password.encode())):
            raise HTTPException(401, '账号或密码不正确')
        response = JSONResponse({'ok': True})
        response.set_cookie(COOKIE, sign(time.time() + MAX_AGE), max_age=MAX_AGE,
                            httponly=True, secure=request.url.scheme == 'https', samesite='lax', path='/admin')
        return response

    @app.post('/admin/logout')
    async def logout():
        if not enabled:
            raise HTTPException(404)
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, path='/admin')
        return response

    @app.get('/admin/api/overview')
    async def overview(request: Request):
        guard(request)
        return build_overview(relay)

    @app.get('/admin/api/devices')
    async def devices(request: Request):
        guard(request)
        return build_devices(relay)

    @app.get('/admin/api/rallies')
    async def rallies(request: Request):
        guard(request)
        return build_rallies(relay)

    @app.post('/admin/api/devices/{device}/note')
    async def device_note(device: str, request: Request):
        mutate_guard(request)
        if not re.fullmatch(r'[0-9a-f]{32}', device):
            raise HTTPException(404, '设备不存在')
        try:
            data = await read_json(request)
        except HTTPException as error:
            if error.status_code != 400:
                raise
            raise HTTPException(400, '备注格式不正确') from None
        try:
            relay.store.admin_set_device_note(device, data.get('note') if isinstance(data, dict) else None)
        except DomainError as error:
            raise HTTPException(404 if error.code == 'identity' else 400, error.message) from None
        logger.info('admin update device note device=%s', device)
        return {'ok': True}

    @app.get('/admin/api/trend')
    async def trend(request: Request):
        guard(request)
        return {'points': list(relay.trend), 'server_time': time.time()}

    @app.post('/admin/api/devices/{device}/kick')
    async def kick(device: str, request: Request):
        mutate_guard(request)
        if not re.fullmatch(r'[0-9a-f]{8}', device):
            raise HTTPException(404, '设备不存在')
        full = next((d for d in relay.sessions if d.startswith(device)), None)
        if not full:
            raise HTTPException(409, '设备不在线')
        await relay.kick(full)
        logger.info('admin kick device=%s', device)
        return {'ok': True}

    @app.post('/admin/api/rallies/{code}/dissolve')
    async def dissolve(code: str, request: Request):
        mutate_guard(request)
        rally = relay.store.rally_by_code(code)
        if not rally:
            raise HTTPException(404, '集会不存在')
        relay.store.admin_dissolve_rally(code)
        for member in rally['members']:
            await relay.rally.push(member['device_id'])
        logger.info('admin dissolve rally=%s', code)
        return {'ok': True}

    @app.post('/admin/api/rallies/{code}/limit')
    async def set_limit(code: str, request: Request):
        mutate_guard(request)
        try:
            data = await read_json(request)
        except HTTPException as error:
            if error.status_code != 400:
                raise
            data = {}
        value = data.get('limit') if isinstance(data, dict) else None
        if type(value) is not int:
            raise HTTPException(400, '人数上限格式不正确')
        try:
            rally = relay.store.admin_set_rally_limit(code, value)
        except DomainError as error:
            raise HTTPException(404 if error.code == 'unavailable' else 400, error.message)
        for member in rally['members']:
            await relay.rally.push(member['device_id'])
        logger.info('admin set rally=%s limit=%s', code, value)
        return {'ok': True, 'rally': rally}

    web = Path(__file__).parent / 'admin_web'
    index = web / 'index.html'
    if index.exists():
        app.mount('/admin/assets', StaticFiles(directory=str(web / 'assets')), name='admin-assets')

        @app.get('/admin')
        async def admin_page():
            if not enabled:
                raise HTTPException(404)
            return FileResponse(index)

        @app.get('/admin/{rest:path}')
        async def admin_spa(rest: str):
            if not enabled:
                raise HTTPException(404)
            return FileResponse(index)
    else:
        legacy = Path(__file__).parent / 'admin.html'
        page = (legacy if legacy.exists() else Path(__file__).parent / 'hosting.html').read_text(encoding='utf-8')

        @app.get('/admin')
        async def admin_page():
            if not enabled:
                raise HTTPException(404)
            return HTMLResponse(page)
