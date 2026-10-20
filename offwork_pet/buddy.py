"""Asynchronous Qt companion connection. No sockets are opened until enabled."""
import json
import time
import uuid

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from .credentials import IdentityStore, normalize_endpoint


class BuddyClient(QObject):
    changed = Signal()
    notice = Signal(str)
    incoming = Signal(dict)
    outgoing = Signal(str, int)

    def __init__(self, config, parent=None, persist=True):
        super().__init__(parent)
        self.config, self.persist = config, persist
        self.identity = None
        self.socket = None
        self.http = QNetworkAccessManager(self)
        self.registration = None
        self.enabled = False
        self.connected = False
        self.ready = False
        self.visible = True
        self.relation = None
        self.status = 'offline'
        self.connection_text = '联网未开启'
        self.invitation = None
        self.callbacks = {}
        self.last_message = 0.0
        self.clock_offset = 0.0
        self.retry_seconds = 1
        self.batch = None
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
        self.batch_timer = QTimer(self)
        self.batch_timer.setSingleShot(True)
        self.batch_timer.timeout.connect(self.flush_action)

    def update_config(self, config):
        self.config = config
        if self.ready:
            self.profile()

    def start(self):
        if self.enabled:
            return
        try:
            endpoint = normalize_endpoint(self.config.relay_url)
            self.identity = IdentityStore(endpoint)
            cached = self.identity.record.get('relation_cache')
            self.relation = cached if isinstance(cached, dict) and cached.get('id') == self.identity.record.get('relation_id') else None
        except (ValueError, OSError, UnicodeError) as error:
            self.connection_text = f'无法读取连接设置：{error}'
            self.changed.emit()
            return
        self.enabled = True
        self.connect_service()

    def stop(self):
        self.enabled = False
        self.retry.stop()
        self.connect_timeout.stop()
        self.heartbeat.stop()
        self.batch_timer.stop()
        self.batch = None
        self.invitation = None
        if self.registration:
            old_reply, self.registration = self.registration, None
            old_reply.abort()
        self.connected = self.ready = False
        self.status = 'offline'
        self.connection_text = '联网未开启'
        if self.socket:
            old, self.socket = self.socket, None
            old.abort()
            old.deleteLater()
        self.fail_requests()
        self.changed.emit()

    def connect_service(self):
        if not self.enabled or self.registration or self.socket:
            return
        self.connection_text = '正在连接…'
        self.changed.emit()
        if not self.identity.token:
            request = QNetworkRequest(QUrl(self.identity.endpoint + '/v1/devices'))
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/json')
            request.setTransferTimeout(10000)
            self.registration = self.http.post(request, b'{}')
            reply = self.registration
            reply.finished.connect(lambda: self.registered(reply))
            return
        socket = self.socket = QWebSocket('', parent=self)
        socket.connected.connect(lambda: self.on_connected(socket))
        socket.disconnected.connect(lambda: self.on_disconnected(socket))
        socket.textMessageReceived.connect(lambda raw: self.on_message(socket, raw))
        socket.errorOccurred.connect(lambda _: self.socket_error(socket))
        endpoint = self.identity.endpoint.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)
        request = QNetworkRequest(QUrl(endpoint + '/v1/connect'))
        request.setRawHeader(b'Authorization', ('Bearer ' + self.identity.token).encode())
        socket.open(request)
        self.connect_timeout.start(12000)

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
                raise ValueError('无法连接服务，请检查服务地址和网络')
            data = json.loads(bytes(reply.readAll()))
            self.identity.set_identity(data)
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
        self.request('profile', {'paused': self.config.paused, 'visible': self.visible,
                                 'character': self.config.character}, completed)

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
            # Some failed handshakes emit no disconnected signal.
            self.on_disconnected(socket)

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
        self.heartbeat.stop()
        self.batch_timer.stop()
        self.batch = None
        self.fail_requests()
        if code in (4001, 4401):
            self.enabled = False
            self.connection_text = '该身份已在另一处连接' if code == 4001 else '设备身份失效，请核对服务或重置身份'
            self.changed.emit()
        elif self.enabled:
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
            self.batch = None
            self.batch_timer.stop()
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
        self.batch_timer.stop()
        self.batch = None
        self.changed.emit()
        if self.connected:
            self.request('unlink', {'relation_id': relation_id}, self.unlinked_pending)

    def queue_action(self, kind):
        if not self.ready or not self.relation or self.status != 'available' or self.identity.record.get('pending_unlink'):
            self.notice.emit('对方暂不接收互动或连接未就绪')
            return
        if self.batch and self.batch['action'] != kind:
            self.flush_action()
        if not self.batch:
            self.batch = {'action': kind, 'count': 0, 'relation_id': self.relation['id']}
            self.batch_timer.start(350)
        self.batch['count'] = min(5, self.batch['count'] + 1)

    def flush_action(self):
        batch, self.batch = self.batch, None
        if not batch or not self.ready or not self.relation or batch['relation_id'] != self.relation['id']:
            return
        action_id = uuid.uuid4().hex
        self.outgoing.emit(batch['action'], batch['count'])
        self.notice.emit('正在发送…')
        def done(result):
            self.notice.emit('对方客户端已接收' if result['ok'] else result.get('message', '未确认送达'))
        self.request('action', {**batch, 'action_id': action_id}, done, timeout=7)

    def acknowledge(self, msg, accepted):
        self.send({'type': 'ack', 'relation_id': msg.get('relation_id'),
                   'action_id': msg.get('action_id'), 'accepted': accepted})
