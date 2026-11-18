"""Asynchronous Qt companion connection. No sockets are opened until enabled."""
import json
import time
import uuid

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from .credentials import IdentityStore, normalize_endpoint
from .device_info import collect_device_info

SERVICE_ENDPOINT = 'http://106.54.27.8'


class BuddyClient(QObject):
    changed = Signal()
    notice = Signal(str)
    incoming = Signal(dict)
    visit_event = Signal(dict)
    rally_event = Signal(dict)
    chat_event = Signal(dict)

    def __init__(self, config, parent=None, persist=True, *, endpoint=None):
        super().__init__(parent)
        self.config, self.persist = config, persist
        self.endpoint = normalize_endpoint(endpoint or config.server_url) if (endpoint or config.server_url) else ''
        self.access_code = ''
        self.capabilities_provider = lambda: None
        self.identity = None
        self.socket = None
        self.http = QNetworkAccessManager(self)
        self.registration = None
        self.preflight = None
        self.service_checked = False
        self.service_features = set()
        self.server_info = {}
        self.enabled = False
        self.connected = False
        self.ready = False
        self.visible = True
        self.relation = None
        self.status = 'offline'
        self.connection_text = '联网未开启'
        self.invitation = None
        self.rally = None
        self.rally_max_members = 12
        self.callbacks = {}
        self.last_message = 0.0
        self.clock_offset = 0.0
        self.retry_seconds = 1
        self.received = {}
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(10000)
        self.heartbeat.timeout.connect(self.pulse)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self.connect_service)
        self.connect_timeout = QTimer(self)
        self.connect_timeout.setSingleShot(True)
        self.connect_timeout.timeout.connect(self.abort_connect)
        self.deadlines = QTimer(self)
        self.deadlines.setInterval(500)
        self.deadlines.timeout.connect(self.expire_requests)
        self.deadlines.start()

    def update_config(self, config):
        self.config = config
        if self.ready:
            self.profile()

    def change_server(self, endpoint, access_code=''):
        normalized = normalize_endpoint(endpoint) if endpoint.strip() else ''
        self.stop()
        self.identity = None
        self.relation = None
        self.received.clear()
        self.callbacks.clear()
        self.clock_offset = 0
        self.retry_seconds = 1
        self.service_checked = False
        self.service_features.clear()
        self.server_info.clear()
        self.endpoint = self.config.server_url = normalized
        self.config.network_enabled = False
        self.access_code = access_code.strip()
        self.changed.emit()

    def start(self):
        if self.enabled:
            return
        if not self.endpoint:
            self.connection_text = '请先配置自己的服务器地址'
            self.config.network_enabled = False
            self.changed.emit()
            return
        try:
            self.identity = IdentityStore(self.endpoint)
            cached = self.identity.record.get('relation_cache')
            self.relation = cached if isinstance(cached, dict) and cached.get('id') == self.identity.record.get('relation_id') else None
        except (ValueError, OSError, UnicodeError) as error:
            self.connection_text = f'无法读取连接身份：{error}'
            self.changed.emit()
            return
        if self.identity.reset:
            self.notice.emit('本机身份文件损坏，已自动重置，请重新邀请搭子')
        self.enabled = True
        self.service_checked = False
        self.connect_service()

    def stop(self):
        self.enabled = False
        self.retry.stop()
        self.connect_timeout.stop()
        self.heartbeat.stop()
        self.invitation = None
        if self.registration:
            old_reply, self.registration = self.registration, None
            old_reply.abort()
        if self.preflight:
            old_reply, self.preflight = self.preflight, None
            old_reply.abort()
        self.connected = self.ready = False
        self.status = 'offline'
        self.connection_text = '联网未开启'
        self.rally = None
        if self.socket:
            old, self.socket = self.socket, None
            old.abort()
            old.deleteLater()
        self.fail_requests()
        self.changed.emit()

    def connect_service(self):
        if not self.enabled or self.registration or self.preflight or self.socket:
            return
        self.connection_text = '正在连接…'
        self.changed.emit()
        if not self.service_checked:
            request = QNetworkRequest(QUrl(self.endpoint + '/health'))
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
            request.setTransferTimeout(10000)
            reply = self.preflight = self.http.get(request)
            reply.finished.connect(lambda: self.checked_service(reply))
            return
        if not self.identity.token:
            request = QNetworkRequest(QUrl(self.identity.endpoint + '/v2/devices'))
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/json')
            request.setTransferTimeout(10000)
            self.registration = self.http.post(request, json.dumps({'access_code': self.access_code}).encode())
            reply = self.registration
            reply.finished.connect(lambda: self.registered(reply))
            return
        socket = self.socket = QWebSocket('', parent=self)
        socket.connected.connect(lambda: self.on_connected(socket))
        socket.disconnected.connect(lambda: self.on_disconnected(socket))
        socket.textMessageReceived.connect(lambda raw: self.on_message(socket, raw))
        socket.errorOccurred.connect(lambda _: self.socket_error(socket))
        endpoint = self.identity.endpoint.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
        request = QNetworkRequest(QUrl(endpoint + '/v2/connect'))
        request.setRawHeader(b'Authorization', ('Bearer ' + self.identity.token).encode())
        socket.open(request)
        self.connect_timeout.start(12000)

    def block_service(self, message='搭子服务正在升级，暂时无法联网。请稍后再试。'):
        self.config.network_enabled = False
        self.stop()
        self.connection_text = message
        self.changed.emit()

    def checked_service(self, reply):
        if reply is not self.preflight:
            reply.deleteLater()
            return
        self.preflight = None
        try:
            if not self.enabled:
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                if status in (404, 405):
                    self.block_service()
                    return
                self.schedule_retry('暂时连不上搭子服务，稍后自动重试')
                return
            try:
                data = json.loads(bytes(reply.readAll()))
                supported = data.get('supported_protocols', [data.get('protocol')])
                valid = data.get('ok') is True and isinstance(supported, list) and 2 in supported
            except (ValueError, AttributeError, TypeError):
                valid = False
            if not valid:
                self.block_service()
                return
            self.service_checked = True
            self.server_info = data.get('server', {}) if isinstance(data.get('server'), dict) else {}
            features = data.get('features', [])
            self.service_features = {item for item in features if isinstance(item, str)} if isinstance(features, list) else set()
            self.connect_service()
        finally:
            reply.deleteLater()

    def abort_connect(self):
        if self.socket and not self.connected:
            socket = self.socket
            socket.abort()
            if socket is self.socket:
                self.on_disconnected(socket)

    def registered(self, reply):
        if reply is not self.registration:
            reply.deleteLater()
            return
        self.registration = None
        try:
            if not self.enabled:
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                if reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) == 404:
                    self.block_service()
                    return
                if reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) in (401, 403):
                    self.access_code = ''
                    self.block_service('服务器拒绝接入，请检查接入码或联系管理员')
                    return
                raise ValueError('暂时无法连接搭子服务，请检查网络或稍后重试')
            try:
                data = json.loads(bytes(reply.readAll()))
            except ValueError:
                self.block_service()
                return
            if not isinstance(data, dict) or not isinstance(data.get('token'), str) or not isinstance(data.get('device_id'), str):
                self.block_service()
                return
            self.identity.set_identity(data)
            self.access_code = ''
            self.connect_service()
        except (ValueError, OSError, UnicodeError) as error:
            self.schedule_retry(str(error))
        finally:
            reply.deleteLater()

    def on_connected(self, socket):
        if socket is not self.socket or not self.enabled:
            return
        self.connected = True
        self.connect_timeout.stop()
        self.ready = False
        self.last_message = time.monotonic()
        self.connection_text = '正在同步搭子状态…'
        self.heartbeat.start()
        pending = self.identity.record.get('pending_unlink')
        if pending:
            self.request('unlink', {'relation_id': pending}, self.unlinked_pending)
        else:
            self.profile(initial=True)

    def unlinked_pending(self, result):
        if result['ok']:
            self.identity.record['pending_unlink'] = ''
            self.identity.record['relation_id'] = ''
            self.save_identity()
            self.sync_state(result.get('data', {}))
            self.profile(initial=True)
        else:
            self.notice.emit(result.get('message', '解除待同步'))
            self.connection_text = '解除待同步'
            self.ready = False
            self.changed.emit()

    def profile(self, initial=False):
        def completed(result):
            if result['ok']:
                self.sync_state(result['data'])
                if not self.identity.record.get('pending_unlink'):
                    self.ready = True
                    self.connection_text = '已连接服务'
                    self.retry_seconds = 1
                    self.changed.emit()
            elif initial:
                self.notice.emit(result.get('message', '无法同步状态'))
        self.request('profile', {'paused': self.config.paused or getattr(self, 'focus_paused', False), 'visible': self.visible,
                                 'capabilities': self.capabilities_provider(), 'device_info': collect_device_info(), 'visitSupport': True,
                                 'allowVisits': self.config.allow_visits}, completed)

    def pulse(self):
        if self.connected:
            if time.monotonic() - self.last_message > 30:
                self.socket.abort()
            else:
                self.send({'type': 'heartbeat'})
                if self.identity.record.get('pending_unlink'):
                    self.request('unlink', {'relation_id': self.identity.record['pending_unlink']}, self.unlinked_pending)

    def socket_error(self, socket):
        if socket is self.socket and not self.connected:
            if '404' in socket.errorString():
                self.block_service()
                return
            if socket.error() == QAbstractSocket.SocketError.ConnectionRefusedError:
                self.connection_text = '搭子服务暂不可用，正在重试'
            # Some failed handshakes emit no disconnected signal.
            self.on_disconnected(socket)
            self.changed.emit()

    def on_disconnected(self, socket):
        if socket is not self.socket:
            return
        code = socket.closeCode().value
        self.connect_timeout.stop()
        self.socket = None
        socket.deleteLater()
        self.connected = self.ready = False
        self.status = 'offline'
        self.invitation = None
        self.rally = None
        self.heartbeat.stop()
        self.fail_requests()
        if code in (4001, 4401):
            self.enabled = False
            self.connection_text = '该身份已在另一处连接' if code == 4001 else '设备身份失效，请核对服务或重置身份'
            self.changed.emit()
        elif self.enabled:
            self.service_checked = False
            self.schedule_retry('连接中断，正在重连')

    def schedule_retry(self, message):
        if not self.enabled:
            return
        self.connection_text = message
        self.retry.start(self.retry_seconds * 1000)
        self.retry_seconds = min(30, self.retry_seconds * 2)
        self.changed.emit()

    def send(self, data):
        if self.connected and self.socket:
            self.socket.sendTextMessage(json.dumps(data, ensure_ascii=False))

    def request(self, kind, data=None, callback=None, timeout=8):
        if not self.connected:
            if callback:
                callback({'ok': False, 'code': 'offline', 'message': '服务未连接'})
            return
        request_id = uuid.uuid4().hex
        self.callbacks[request_id] = (time.monotonic() + timeout, callback)
        self.send({'type': kind, 'request_id': request_id, **(data or {})})

    def expire_requests(self):
        now = time.monotonic()
        for request_id, (deadline, callback) in list(self.callbacks.items()):
            if now >= deadline:
                self.callbacks.pop(request_id, None)
                if callback:
                    callback({'ok': False, 'code': 'unknown', 'message': '未确认结果，请等待同步或稍后再试'})
        self.received = {key: expiry for key, expiry in self.received.items() if expiry > now}
        if self.invitation and self.invitation['expires_at'] <= time.time() + self.clock_offset:
            self.invitation = None
            self.changed.emit()

    def fail_requests(self):
        callbacks, self.callbacks = self.callbacks, {}
        for _, callback in callbacks.values():
            if callback:
                callback({'ok': False, 'code': 'unknown', 'message': '连接中断，未确认送达或操作结果'})

    def on_message(self, socket, raw):
        if socket is not self.socket:
            return
        try:
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                return
            self.last_message = time.monotonic()
            if isinstance(msg.get('server_time'), (float, int)):
                self.clock_offset = msg['server_time'] - time.time()
            if msg.get('type') == 'state':
                self.sync_state(msg)
            elif msg.get('type') == 'result':
                entry = self.callbacks.pop(msg.get('request_id'), None)
                if entry and entry[1]:
                    entry[1](msg)
            elif msg.get('type') == 'action':
                self.incoming.emit(msg)
            elif msg.get('type') in ('visit_offer', 'visit_active', 'visit_end', 'visit_action', 'visit_action_status'):
                self.visit_event.emit(msg)
            elif msg.get('type') == 'rally_state':
                had_rally = self.rally is not None
                self.sync_rally(msg)
                if had_rally and self.rally is None:
                    self.notice.emit('集会已解散')
            elif msg.get('type') == 'rally_alert':
                self.rally_event.emit(msg)
            elif msg.get('type') == 'rally_chat':
                self.chat_event.emit(msg)
        except (ValueError, TypeError, KeyError):
            self.notice.emit('服务返回了无法识别的数据')

    def save_identity(self):
        try:
            if self.persist and self.identity:
                self.identity.save()
            return True
        except OSError:
            self.notice.emit('设备状态保存失败，请检查本机目录权限')
            return False

    def sync_state(self, data):
        if not self.identity:
            return
        previous = self.identity.record.get('relation_id', '')
        relation = data.get('relation')
        if relation is not None and not isinstance(relation, dict):
            return
        self.relation = relation
        self.status = data.get('partner_status', 'offline')
        current = relation['id'] if relation else ''
        if previous != current:
            self.config.buddy_note = ''
            self.invitation = None
        self.identity.record['relation_id'] = current
        self.identity.record['relation_cache'] = relation
        ended = data.get('ended_id', '')
        if ended and ended != self.identity.record.get('ended_seen'):
            self.identity.record['ended_seen'] = ended
            self.notice.emit('搭子连接已解除')
        self.save_identity()
        self.changed.emit()

    def invite(self):
        def done(result):
            if result['ok']:
                self.invitation = result['data']
                self.changed.emit()
            else:
                self.notice.emit(result.get('message', '生成邀请失败'))
        self.request('invite', callback=done)

    def revoke(self):
        def done(result):
            if result['ok']:
                self.invitation = None
                self.changed.emit()
            else:
                self.notice.emit(result.get('message', '撤销邀请失败'))
        self.request('revoke', callback=done)

    def redeem(self, code):
        def done(result):
            if result['ok']:
                self.sync_state(result['data'])
                self.notice.emit('搭子连接成功')
            else:
                self.notice.emit(result.get('message', '连接失败'))
        self.request('redeem', {'code': code}, done)

    def unlink(self):
        relation_id = self.relation['id'] if self.relation else (self.identity.record.get('relation_id') if self.identity else '')
        if not relation_id:
            return
        self.identity.record['pending_unlink'] = relation_id
        if not self.save_identity():
            return
        self.ready = False
        self.connection_text = '解除待同步'
        self.changed.emit()
        if self.connected:
            self.request('unlink', {'relation_id': relation_id}, self.unlinked_pending)

    def queue_action(self, kind):
        if not self.ready or not self.relation or self.status != 'available' or self.identity.record.get('pending_unlink'):
            self.notice.emit('对方暂不接收互动或连接未就绪')
            return
        capabilities = self.relation.get('capabilities')
        if not capabilities or not any(item['id'] == kind for item in capabilities['interactions']):
            self.notice.emit('对方当前角色不支持该互动，或需要升级客户端')
            return
        action_id = uuid.uuid4().hex
        self.notice.emit('正在发送…')
        def done(result):
            self.notice.emit(('对方客户端已排队' if result['data'].get('status') == 'queued' else '对方客户端已接收')
                             if result['ok'] else result.get('message', '未确认送达'))
        self.request('action', {'relation_id': self.relation['id'], 'interactionId': kind,
            'capabilityRevision': capabilities['revision'], 'action_id': action_id}, done, timeout=7)

    def acknowledge(self, msg, accepted, status='started'):
        self.send({'type': 'ack', 'relation_id': msg.get('relation_id'),
                   'action_id': msg.get('action_id'), 'accepted': accepted, 'status': status})

    @property
    def rally_supported(self):
        return 'rally/v1' in self.service_features

    def sync_rally(self, data):
        rally = data.get('rally')
        if rally is not None and not isinstance(rally, dict):
            return
        self.rally = rally
        limits = data.get('rally_limits')
        maximum = limits.get('max_members') if isinstance(limits, dict) else None
        if type(maximum) is int and 1 <= maximum <= 1000:
            self.rally_max_members = maximum
        self.changed.emit()

    def rally_create(self, name, member_limit=None):
        def done(result):
            if result['ok']:
                self.sync_rally(result['data'])
                self.notice.emit('集会已创建，分享集会码邀请成员')
            else:
                self.notice.emit(result.get('message', '创建集会失败'))
        data = {'name': name, 'member_name': self.config.rally_name}
        if member_limit is not None:
            data['member_limit'] = member_limit
        self.request('rally_create', data, done)

    def rally_set_limit(self, member_limit):
        def done(result):
            if result['ok']:
                self.sync_rally(result['data'])
                self.notice.emit('集会人数上限已更新')
            else:
                self.notice.emit(result.get('message', '调整人数上限失败'))
        self.request('rally_limit', {'member_limit': member_limit}, done)

    def rally_join(self, code, name=''):
        def done(result):
            if result['ok']:
                self.sync_rally(result['data'])
                self.notice.emit('已加入集会')
            else:
                self.notice.emit(result.get('message', '加入集会失败'))
        self.request('rally_join', {'code': code, 'name': name}, done)

    def rally_leave(self):
        def done(result):
            if result['ok']:
                self.sync_rally(result['data'])
                self.notice.emit('已离开集会')
            else:
                self.notice.emit(result.get('message', '离开集会失败'))
        self.request('rally_leave', callback=done)

    def rally_rename(self, name):
        def done(result):
            if result['ok']:
                self.sync_rally(result['data'])
            else:
                self.notice.emit(result.get('message', '修改集会名字失败'))
        self.request('rally_name', {'name': name}, done)

    def rally_alert(self, level, *, label=None, color=None):
        def done(result):
            if result['ok']:
                delivered = result['data'].get('delivered', 0)
                self.notice.emit(f'已广播给 {delivered} 位在线成员' if delivered else '成员都不在线，已在本机展示')
            else:
                self.notice.emit(result.get('message', '广播失败，已在本机展示'))
        data = {'level': level, 'alert_id': uuid.uuid4().hex}
        if label is not None:
            data.update(label=label, color=color)
        self.request('rally_alert', data, done, timeout=7)

    def rally_chat(self, text, callback):
        if not self.ready or not self.rally:
            callback({'ok': False, 'message': '请先联网并加入集会'})
            return
        if 'rally-chat/v1' not in self.service_features:
            callback({'ok': False, 'message': '当前服务尚未支持即时消息，可先使用本地双窗口体验'})
            return
        self.request('rally_chat', {'text': text, 'message_id': uuid.uuid4().hex}, callback, timeout=5)
