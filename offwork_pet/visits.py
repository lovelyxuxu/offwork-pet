"""A visiting character has its own window, runtime, assets and lifecycle."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import uuid

from PySide6.QtCore import QFile, QIODevice, QObject, QPoint, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from .behavior import BehaviorRuntime
from .character_sound import CharacterSound
from .package_player import PackagePlayer
from .packages import PackageError
from .visit_assets import MAX_ARCHIVE, VisitorCache, export_package
from .visitor_portal import PortalTransition, draw_character, draw_door

STATUS_TEXT = {'queued': '访客动作已排队', 'started': '访客正在表演', 'completed': '访客表演完成',
    'cancelled': '访客动作已取消', 'expired': '访客动作等待超时', 'busy': '访客正在忙，请稍后再试',
    'cooldown': '这个动作需要休息一下', 'condition': '访客当前状态不能做这个动作',
    'unsupported': '访客没有这个动作', 'paused': '串门暂时暂停', 'error': '访客动作播放失败'}


class VisitorWindow(QWidget):
    def __init__(self, controller, package):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus
        if controller.pet.config.topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(controller.pet, flags)
        self.controller = controller
        self.runtime = BehaviorRuntime(package)
        self.player = PackagePlayer(self.runtime, budget=24 * 1024**2)
        self.sound = CharacterSound()
        self.disposing = False
        self.portal = None
        self.arrival_callback = None
        self.departure_image = None
        self.transition_timer = QTimer(self)
        self.transition_timer.setInterval(16)
        self.transition_timer.timeout.connect(self.advance_transition)
        self.caption = '来串门啦'
        self.setWindowTitle('来串门的 '+package.name)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.ratio = controller.pet.config.scale / 100
        self.setFixedSize(round(280*self.ratio), round(280*self.ratio))
        self.player.cache.image(package, self.runtime.frame()['file'])
        self.menu = QMenu(self)
        self.menu.addSection(package.name+' · 来访中')
        for item in sorted(package.behavior.get('interactions', []), key=lambda i: i.get('order', 0)):
            action = self.menu.addAction(item['label'])
            action.triggered.connect(lambda checked=False, key=item['id']: controller.local_action(key))
        self.menu.addSeparator()
        self.menu.addAction('送客', controller.dismiss)
        self.place()

    def place(self):
        host = self.controller.pet.frameGeometry()
        screen = QApplication.screenAt(host.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        right, left = host.right()+8, host.left()-self.width()-8
        x = right if right+self.width() <= area.right()+1 else left
        self.move(max(area.left(), min(x, area.right()+1-self.width())),
                  max(area.top(), min(host.bottom()+1-self.height(), area.bottom()+1-self.height())))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.scale(self.ratio, self.ratio)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setPen(QColor('#765D95'))
        painter.setFont(QFont('Microsoft YaHei', 9))
        transitioning = self.portal is not None
        leaving = self.departure_image is not None
        painter.drawText(QRectF(10, 17 if transitioning else 54, 260, 26), Qt.AlignmentFlag.AlignCenter,
            self.runtime.package.name+(' · 下次见' if leaving else ' · 来串门啦' if transitioning else ' · 来访'))
        if self.controller.pet.config.remote_text and not transitioning:
            painter.drawText(QRectF(10, 78, 260, 20), Qt.AlignmentFlag.AlignCenter, self.caption[:24])
        try:
            if self.portal:
                frame = self.portal.sample()
                draw_door(painter, frame)
                draw_character(painter, frame, self.draw_character)
            else:
                self.draw_character(painter)
        except (PackageError, OSError, KeyError, ValueError):
            QTimer.singleShot(0, self.controller.dismiss)

    def draw_character(self, painter):
        if self.departure_image is not None:
            painter.drawImage(QPoint(0, 0), self.departure_image)
        else:
            self.player.draw(painter)

    def arrive(self, callback):
        self.runtime.suspend(True)
        self.arrival_callback = callback
        self.portal = PortalTransition()
        self.transition_timer.start()
        self.show()
        self.update()

    def depart(self):
        if self.disposing or not self.isVisible() or self.portal is not None:
            self.dispose()
            return
        # Release package playback and sound immediately. Only a small rendered
        # snapshot remains while the doorway takes the guest home.
        density = self.ratio*self.devicePixelRatioF()
        image = QImage(round(280*density), round(280*density), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(density)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            self.player.draw(painter)
        except (PackageError, OSError, KeyError, ValueError):
            painter.end()
            self.dispose()
            return
        painter.end()
        self.departure_image = image
        self.menu.close()
        self.sound.stop()
        self.player.close()
        self.portal = PortalTransition(leaving=True)
        self.transition_timer.start()
        self.update()

    def advance_transition(self):
        if self.disposing or self.portal is None:
            return
        if self.portal.sample().done:
            self.portal = None
            self.transition_timer.stop()
            if self.departure_image is not None:
                self.dispose()
                return
            callback, self.arrival_callback = self.arrival_callback, None
            if callback:
                callback()
        self.update()

    @property
    def can_interact(self):
        return not self.disposing and self.portal is None and self.departure_image is None

    def contextMenuEvent(self, event):
        if self.can_interact:
            self.menu.popup(event.globalPos())
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.can_interact:
            self.menu.popup(self.mapToGlobal(event.position().toPoint()))

    def closeEvent(self, event):
        if self.disposing:
            event.accept()
        else:
            event.ignore()
            self.controller.dismiss()

    def dispose(self):
        if self.disposing:
            return
        self.disposing = True
        self.arrival_callback = None
        self.transition_timer.stop()
        self.menu.close()
        self.sound.stop()
        if not self.runtime.stopped:
            self.player.close()
        self.departure_image = None
        self.close()
        self.controller.retiring.discard(self)
        self.deleteLater()


class VisitController(QObject):
    changed = Signal()
    work_done = Signal(str, int, object, object)

    def __init__(self, pet):
        super().__init__(pet)
        self.pet, self.client = pet, pet.buddy
        root = pet.library.root.parent/'visitors' if pet.persist else pet.library.root/'visitor-cache'
        self.cache = VisitorCache(root)
        self.temporary = TemporaryDirectory(prefix='offwork-visit-client-')
        self.pool = None
        self.generation = 0
        self.starting = False
        self.outgoing = self.incoming = self.guest = None
        self.retiring = set()
        self.receipts = {}
        self.transfers = {}
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.tick)
        self.work_done.connect(self.finished_job)
        self.client.visit_event.connect(self.handle_message)
        self.client.changed.connect(self.connection_changed)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        pet.destroyed.connect(self.shutdown)

    @property
    def supported(self):
        return 'visits/v1' in self.client.service_features and bool((self.client.relation or {}).get('visitSupport'))

    @property
    def allowed(self):
        return (self.client.ready and self.pet.config.allow_visits and not self.pet.config.paused
                and not self.pet.focus.active and self.pet.isVisible())

    def notice(self, message):
        self.client.notice.emit(message)

    def job(self, kind, function):
        if self.pool is None:
            self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='visitor-assets')
        generation = self.generation
        future = self.pool.submit(function)
        def completed(future):
            try:
                result, error = future.result(), None
            except BaseException as exc:
                result, error = None, exc
            if not self.closed:
                try:
                    self.work_done.emit(kind, generation, result, error)
                except RuntimeError:
                    pass
            if kind == 'export' and (self.closed or generation != self.generation) and result:
                Path(result['path']).unlink(missing_ok=True)
        future.add_done_callback(completed)

    def start(self):
        if self.closed:
            return
        client = self.client
        if self.starting or self.outgoing or self.incoming:
            self.notice('已经在串门，请先告别')
            return
        if not self.supported:
            self.notice('双方客户端和搭子服务需要支持串门，请升级后再试')
            return
        if (not client.ready or client.status != 'available' or not client.relation.get('allowVisits')
                or self.pet.config.paused or self.pet.focus.active or not self.pet.isVisible()):
            self.notice('对方暂不接待串门或连接未就绪')
            return
        package = self.pet.runtime.package if self.pet.runtime and not self.pet.runtime.stopped else None
        if package is None:
            self.notice('先选择自己的角色，再去串门')
            return
        self.starting = True
        self.start_context = (client.relation['id'], self.pet.runtime.revision)
        self.notice('正在准备串门角色…')
        self.changed.emit()
        path = Path(self.temporary.name)/(uuid.uuid4().hex+'.petpack')
        self.job('export', lambda: {'path': path, **export_package(package, path)})

    def finished_job(self, kind, generation, result, error):
        if self.closed or generation != self.generation:
            if kind == 'export' and result:
                result['path'].unlink(missing_ok=True)
            return
        if error:
            self.notice('串门素材准备失败：'+str(error))
            self.stop()
            return
        if kind == 'export':
            relation, revision = self.start_context
            if not self.client.ready or not self.pet.runtime or self.pet.runtime.revision != revision:
                result['path'].unlink(missing_ok=True)
                self.stop()
                return
            def started(response):
                if generation != self.generation or self.closed:
                    result['path'].unlink(missing_ok=True)
                    if response['ok']:
                        self.client.request('visit_end', {'visit_id': response['data']['id']})
                    return
                self.starting = False
                if response['ok']:
                    self.outgoing = response['data']
                    self.timer.start()
                    self.upload(result['path'])
                else:
                    result['path'].unlink(missing_ok=True)
                    self.notice(response.get('message', '暂时无法串门'))
                self.changed.emit()
            self.client.request('visit_start', {'relation_id': relation, 'capabilityRevision': revision,
                'sha256': result['sha256'], 'size': result['size']}, started)
        elif kind == 'lookup':
            if result:
                self.show_guest(result)
            else:
                self.download()
        elif kind == 'install':
            self.show_guest(result)

    def http_request(self, vid):
        request = QNetworkRequest(QUrl(self.client.endpoint+'/v2/visits/'+vid+'/package'))
        request.setTransferTimeout(45000)
        request.setRawHeader(b'Authorization', ('Bearer '+self.client.identity.token).encode())
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        return request

    def upload(self, path):
        source = QFile(str(path), self)
        if not source.open(QIODevice.OpenModeFlag.ReadOnly):
            source.deleteLater()
            path.unlink(missing_ok=True)
            self.notice('串门素材无法读取')
            self.stop()
            return
        request = self.http_request(self.outgoing['id'])
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/octet-stream')
        reply = self.client.http.put(request, source)
        generation = self.generation
        self.transfers[reply] = (source, path)
        def finished():
            self.transfers.pop(reply, None)
            source.close(); source.deleteLater(); path.unlink(missing_ok=True)
            valid = reply.error() == QNetworkReply.NetworkError.NoError and reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) == 200
            reply.deleteLater()
            if generation == self.generation and not self.closed:
                if not valid:
                    self.notice('角色包上传失败，请稍后重新串门')
                    self.stop()
                elif self.outgoing and self.outgoing['stage'] != 'active':
                    self.notice('角色已送达，正在等搭子接待…')
        reply.finished.connect(finished)

    def download(self):
        visit = self.incoming
        path = Path(self.temporary.name)/(uuid.uuid4().hex+'.petpack')
        try:
            output = path.open('wb')
        except OSError:
            self.notice('访客素材无法保存，请检查本机空间')
            self.stop()
            return
        reply = self.client.http.get(self.http_request(visit['id']))
        reply.setReadBufferSize(128 * 1024)
        self.transfers[reply] = (output, path)
        generation, size = self.generation, [0]
        def read():
            chunk = bytes(reply.readAll())
            size[0] += len(chunk)
            if size[0] > min(visit['size'], MAX_ARCHIVE):
                reply.abort()
                return
            try:
                output.write(chunk)
            except OSError:
                reply.abort()
        reply.readyRead.connect(read)
        def finished():
            if reply.bytesAvailable():
                read()
            self.transfers.pop(reply, None)
            output.close()
            valid = (reply.error() == QNetworkReply.NetworkError.NoError
                and reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) == 200 and size[0] == visit['size'])
            reply.deleteLater()
            if generation != self.generation or self.closed:
                path.unlink(missing_ok=True)
                return
            if not valid:
                path.unlink(missing_ok=True)
                self.notice('来访角色下载失败')
                self.stop()
                return
            def install():
                try:
                    return self.cache.install(path, visit)
                finally:
                    path.unlink(missing_ok=True)
            self.job('install', install)
        reply.finished.connect(finished)

    def show_guest(self, package):
        if not self.incoming or not self.allowed:
            self.stop()
            return
        try:
            self.clear_retiring()
            self.guest = VisitorWindow(self, package)
        except (PackageError, OSError, ValueError) as error:
            self.notice('来访角色无法显示：'+str(error))
            self.stop()
            return
        # The doorway introduces the guest before declaring it ready for actions.
        vid, generation = self.incoming['id'], self.generation
        def ready(response):
            if (not response['ok'] and self.incoming and self.incoming['id'] == vid
                    and generation == self.generation):
                self.stop()
        guest = self.guest
        def arrived():
            if (not self.closed and self.incoming and self.incoming['id'] == vid and self.guest is guest
                    and generation == self.generation and self.allowed):
                self.client.request('visit_ready', {'visit_id': vid, 'accepted': True}, ready)
        guest.arrive(arrived)

    def handle_message(self, msg):
        if self.closed:
            return
        kind, vid = msg['type'], msg.get('visit_id')
        if kind == 'visit_offer':
            visit = msg.get('visit', {})
            if (not self.allowed or self.incoming or not self.client.relation
                    or visit.get('relation_id') != self.client.relation['id']
                    or visit.get('sender') != self.client.relation['partner_id']):
                if visit.get('id'):
                    self.client.request('visit_end', {'visit_id': visit['id']})
                return
            self.incoming = visit
            self.receipts.clear()
            self.timer.start()
            self.notice('搭子来串门了，正在准备角色…')
            self.job('lookup', lambda: self.cache.find(visit['capabilities']))
        elif kind == 'visit_active':
            visit = msg.get('visit', {})
            if self.outgoing and visit.get('id') == self.outgoing['id']:
                self.outgoing = visit
                self.notice('已经到搭子桌面，可以选择访客动作了')
            elif self.incoming and visit.get('id') == self.incoming['id'] and self.guest and self.allowed:
                self.incoming = visit
                self.guest.runtime.suspend(False)
                self.guest.show()
                self.notice('搭子来串门啦 · 点击来访角色可选动作或送客')
        elif kind == 'visit_end':
            if any(v and v['id'] == vid for v in (self.outgoing, self.incoming)):
                self.remove_session(vid, animate=msg.get('reason') in ('left', 'dismissed', 'expired'))
                self.notice('串门结束，角色已离开')
        elif kind == 'visit_action':
            if not self.incoming or self.incoming['id'] != vid or not self.guest:
                return
            if msg.get('action_id') in self.receipts:
                return
            if not self.allowed or self.incoming['stage'] != 'active':
                self.receipt(msg['action_id'], 'paused')
                return
            remaining = msg['expires_at']-time.time()-self.client.clock_offset
            if not 0 < remaining <= 6:
                self.receipt(msg['action_id'], 'expired')
                return
            try:
                result = self.guest.runtime.entry('interactions', msg['interactionId'],
                    {'source': 'visitor', 'request': msg['action_id'], 'visit_id': vid}, time.monotonic()+remaining)
            except (PackageError, OSError, ValueError, KeyError):
                result = 'error'
            self.receipt(msg['action_id'], result)
            if result == 'started' and not (self.guest.runtime.current
                    and self.guest.runtime.context.get('request') == msg['action_id']):
                # State/variable operations finish synchronously and produce no
                # animation outcome. Random operations can resolve to these too.
                self.receipt(msg['action_id'], 'completed')
        elif kind == 'visit_action_status' and self.outgoing and self.outgoing['id'] == vid:
            self.notice(STATUS_TEXT.get(msg.get('status'), '串门动作状态已变化'))
        self.changed.emit()

    def receipt(self, action_id, status):
        if self.incoming and self.receipts.get(action_id) != status:
            self.receipts[action_id] = status
            self.client.send({'type': 'visit_receipt', 'visit_id': self.incoming['id'],
                              'action_id': action_id, 'status': status})

    def play(self, identifier):
        if not self.outgoing or self.outgoing['stage'] != 'active':
            self.notice('先去搭子桌面串门，再选择动作')
            return
        self.client.request('visit_action', {'visit_id': self.outgoing['id'], 'interactionId': identifier,
            'action_id': uuid.uuid4().hex}, lambda result: self.notice(
                STATUS_TEXT.get(result.get('code'), result.get('message', '动作未送达'))) if not result['ok'] else None,
            timeout=8)

    def local_action(self, identifier):
        if (self.guest and self.guest.can_interact and self.allowed
                and self.incoming and self.incoming['stage'] == 'active'):
            try:
                result = self.guest.runtime.entry('interactions', identifier, {'source': 'guest-local'})
            except (PackageError, OSError, ValueError, KeyError):
                result = 'error'
            self.notice(STATUS_TEXT.get(result, '已选择访客动作'))

    def tick(self):
        for visit in (self.incoming, self.outgoing):
            if visit and time.time()+self.client.clock_offset >= visit['expires_at']:
                self.end(visit)
        if self.guest and self.incoming and self.incoming['stage'] == 'active':
            try:
                runtime = self.guest.runtime
                runtime.tick()
                while runtime.effects:
                    action_id, _ = runtime.effects.popleft()
                    spec = runtime.data['actions'][action_id]
                    self.guest.caption = spec.get('text', '在这里陪你一会儿')
                    if spec.get('sound') and self.pet.config.remote_sound:
                        self.guest.sound.play(runtime.package.asset(spec['sound']))
                while runtime.outcomes:
                    status, context = runtime.outcomes.popleft()
                    if context.get('request'):
                        self.receipt(context['request'], 'cancelled' if status == 'interrupted' else status)
                self.guest.update()
            except (PackageError, OSError, ValueError, KeyError):
                self.notice('访客动作播放失败，串门已结束')
                self.dismiss()
        if not self.incoming and not self.outgoing:
            self.timer.stop()

    def remove_session(self, vid, animate=False):
        if not any(v and v['id'] == vid for v in (self.incoming, self.outgoing)):
            return
        self.generation += 1
        for reply in list(self.transfers):
            reply.abort()
        if self.incoming and self.incoming['id'] == vid:
            self.incoming = None
            self.receipts.clear()
            if self.guest:
                guest, self.guest = self.guest, None
                if animate and not self.closed and self.pet.isVisible():
                    self.retiring.add(guest)
                    guest.depart()
                else:
                    guest.dispose()
        if self.outgoing and self.outgoing['id'] == vid:
            self.outgoing = None
        if not self.incoming and not self.outgoing:
            self.timer.stop()
        if not self.closed:
            self.changed.emit()

    def end(self, visit):
        if visit:
            self.client.request('visit_end', {'visit_id': visit['id']})
            self.remove_session(visit['id'], animate=True)

    def leave(self):
        self.end(self.outgoing)

    def dismiss(self):
        self.end(self.incoming)

    def stop(self, notify=True):
        self.generation += 1
        self.starting = False
        for reply in list(self.transfers):
            reply.abort()
        for visit in (self.incoming, self.outgoing):
            if visit:
                if notify and self.client.connected:
                    self.client.request('visit_end', {'visit_id': visit['id']})
                self.remove_session(visit['id'])
        self.timer.stop()
        self.clear_retiring()
        if not self.closed:
            self.changed.emit()

    def clear_retiring(self):
        for guest in list(self.retiring):
            guest.dispose()

    def connection_changed(self):
        relation = self.client.relation
        if (not self.client.ready or not relation or self.pet.config.paused or self.pet.focus.active
                or any(v and v['relation_id'] != relation['id'] for v in (self.incoming, self.outgoing))):
            self.stop()
        elif self.incoming and (not self.pet.config.allow_visits or self.client.status != 'available'):
            self.dismiss()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.stop(notify=False)
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
        self.temporary.cleanup()
