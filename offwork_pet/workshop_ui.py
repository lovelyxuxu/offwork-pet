"""Native searchable character gallery and download/detail popup."""
from tempfile import TemporaryDirectory

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout)

from . import workshop
from .packages import PackageError
from .ui_theme import label, style_window


class WorkshopWorker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, query='', page=1, entry=None, parent=None, origin=None):
        super().__init__(parent)
        self.query, self.page, self.entry = query, page, entry
        self.origin = origin
        self.temp = None
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    def shutdown(self):
        self.requestInterruption()
        self.wait(15000)

    def run(self):
        try:
            if not self.origin:
                raise PackageError('请先在搭子设置中配置资源服务器')
            if self.entry is None:
                result = workshop.catalog(self.query, self.page, self.isInterruptionRequested, origin=self.origin)
            else:
                self.temp = TemporaryDirectory(prefix='offwork-character-')
                result = workshop.prepare(self.entry, self.temp.name, self.isInterruptionRequested,
                    lambda count: self.progress.emit(f'正在下载… {count/1048576:.1f} MB'), origin=self.origin)
            if not self.isInterruptionRequested():
                self.result.emit(result)
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit('角色加载失败：'+str(error))

    def cleanup(self):
        if self.temp:
            self.temp.cleanup()
            self.temp = None


class WorkshopDialog(QDialog):
    def __init__(self, manager):
        super().__init__(manager)
        self.manager, self.pet = manager, manager.pet
        self.catalog_origin = self.pet.buddy.endpoint
        self.pet.buddy.changed.connect(self.server_changed)
        self.worker = self.detail_dialog = None
        self.page, self.pages = 1, 1
        self.setWindowTitle('浏览在线角色')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(720, 620)
        style_window(self)
        root = QVBoxLayout(self)
        root.addWidget(label('遇见新的搭子', 'heading'))
        root.addWidget(label('浏览下班搭子角色资源库，查看预览，安装后直接使用。', 'subtitle'))
        search = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('搜索角色名称或作者')
        self.query.setMaxLength(120)
        self.query.returnPressed.connect(self.search)
        self.search_button = QPushButton('搜索')
        self.search_button.clicked.connect(self.search)
        search.addWidget(self.query, 1)
        search.addWidget(self.search_button)
        root.addLayout(search)
        self.list = QListWidget()
        self.list.setIconSize(QSize(100, 100))
        self.list.itemDoubleClicked.connect(self.details)
        root.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.previous = QPushButton('上一页')
        self.previous.clicked.connect(lambda: self.load(self.page-1))
        self.page_label = QLabel()
        self.next = QPushButton('下一页')
        self.next.clicked.connect(lambda: self.load(self.page+1))
        self.inspect = QPushButton('预览 / 安装…')
        self.inspect.setProperty('variant', 'primary')
        self.inspect.clicked.connect(self.details)
        for control in (self.previous, self.page_label, self.next, self.inspect):
            row.addWidget(control)
        root.addLayout(row)
        self.message = label('', 'subtitle')
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        root.addWidget(self.message)

    def busy(self, value):
        self.query.setEnabled(not value)
        self.search_button.setEnabled(not value)
        self.previous.setEnabled(not value and self.page > 1)
        self.next.setEnabled(not value and self.page < self.pages)
        self.inspect.setEnabled(not value and self.list.count() > 0)

    def start(self, worker, callback):
        if self.worker is not None:
            return
        self.worker = worker
        self.busy(True)
        def deliver(result):
            if worker.isInterruptionRequested() or worker.origin != self.pet.buddy.endpoint:
                self.message.setText('服务器已切换或操作已取消，请重新搜索。')
                return
            callback(result)
        worker.result.connect(deliver)
        worker.failed.connect(self.failed)
        worker.finished.connect(lambda: self.finished(worker))
        worker.start()

    def server_changed(self):
        if self.catalog_origin == self.pet.buddy.endpoint:
            return
        self.catalog_origin = self.pet.buddy.endpoint
        self.list.clear()
        if self.worker:
            self.worker.requestInterruption()
        if self.detail_dialog:
            self.detail_dialog.reject()
        self.message.setText('服务器已切换，请重新搜索角色。')

    def finished(self, worker):
        QApplication.instance().aboutToQuit.disconnect(worker.shutdown)
        worker.cleanup()
        self.worker = None
        self.busy(False)
        if worker.isInterruptionRequested():
            self.message.setText('已取消。可以重新搜索或选择角色。')
        if self.detail_dialog:
            self.download_button.setEnabled(True)
            self.progress.hide()
        worker.deleteLater()

    def failed(self, text):
        self.message.setText(text+'，可重试或继续使用本地角色。')
        if self.detail_dialog:
            self.download_status.setText(text)

    def search(self):
        self.load(1)

    def load(self, page=1):
        if self.worker:
            return
        self.page = max(1, page)
        self.message.setText('正在获取角色和预览…')
        self.start(WorkshopWorker(self.query.text(), self.page, parent=self, origin=self.pet.buddy.endpoint), self.loaded)

    def loaded(self, result):
        entries, self.pages = result
        self.list.clear()
        installed = self.pet.library.index['packages']
        for entry in entries:
            title = entry['displayName']+(' · 已安装' if entry.get('packageId', entry['id']) in installed else '')
            item = QListWidgetItem(title+'\n作者：'+entry['ownerName'])
            item.setSizeHint(QSize(500, 108))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            pixmap = QPixmap()
            if pixmap.loadFromData(entry.get('preview', b'')):
                item.setIcon(QIcon(pixmap))
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.page_label.setText(f'{self.page} / {self.pages}')
        self.message.setText('双击角色查看详情。' if entries else '没有找到匹配角色，换个关键词试试。')

    def details(self, *_):
        item = self.list.currentItem()
        if not item or self.worker:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        dialog = QDialog(self)
        self.detail_dialog = dialog
        dialog.setWindowTitle(entry['displayName'])
        dialog.setMinimumWidth(450)
        style_window(dialog)
        root = QVBoxLayout(dialog)
        preview = QLabel()
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap()
        if pixmap.loadFromData(entry.get('preview', b'')):
            preview.setPixmap(pixmap.scaled(180, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        root.addWidget(preview)
        text = QLabel(f"{entry['displayName']}\n作者：{entry['ownerName']}\n版本：{entry.get('version', '')}\n\n{entry['description']}\n\n角色资源库：{workshop.ORIGIN}/characters/{entry['id']}\n\n素材署名与授权：\n{entry.get('creditsText', '以角色包内的作者说明为准。')}")
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setWordWrap(True)
        root.addWidget(text)
        self.download_status = label('安装会校验素材，成功后启用这个角色。', 'subtitle')
        self.download_status.setTextFormat(Qt.TextFormat.PlainText)
        self.download_status.setWordWrap(True)
        root.addWidget(self.download_status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        root.addWidget(self.progress)
        installed = entry.get('packageId', entry['id']) in self.pet.library.index['packages']
        self.download_button = QPushButton('使用已安装角色' if installed else '安装并使用')
        self.download_button.setProperty('variant', 'primary')
        self.download_button.clicked.connect(lambda: self.download(entry))
        root.addWidget(self.download_button)
        cancel = QPushButton('取消下载 / 关闭')
        cancel.clicked.connect(dialog.reject)
        root.addWidget(cancel)
        dialog.finished.connect(self.cancel_download)
        dialog.exec()
        self.detail_dialog = None
        dialog.deleteLater()

    def download(self, entry):
        if self.worker:
            return
        package_id = entry.get('packageId', entry['id'])
        if package_id in self.pet.library.index['packages']:
            self.download_status.setText('正在准备角色…')
            def finished(error):
                if not self.detail_dialog:
                    return
                if error is not None:
                    self.failed(str(error))
                else:
                    self.manager.refresh()
                    self.download_status.setText('已启用：'+entry['displayName'])
            self.pet.character_loader.request(package_id, finished)
            self.character_generation = self.pet.character_loader.generation
            return
        self.download_button.setEnabled(False)
        self.progress.show()
        self.download_status.setText('正在准备角色…')
        worker = WorkshopWorker(entry=entry, parent=self, origin=self.pet.buddy.endpoint)
        worker.progress.connect(self.download_status.setText)
        self.start(worker, self.install)

    def install(self, path):
        if not self.detail_dialog or self.worker.isInterruptionRequested():
            return
        try:
            package = self.pet.import_package(path)
            self.manager.refresh()
            self.download_status.setText('已安装并启用：'+package.name)
            self.message.setText('已安装并启用：'+package.name)
        except (PackageError, OSError) as error:
            self.failed(str(error))

    def cancel_download(self, *_):
        if getattr(self, 'character_generation', None) == self.pet.character_loader.generation:
            self.pet.character_loader.cancel()
        if self.worker:
            self.worker.requestInterruption()

    def closeEvent(self, event):
        self.cancel_download()
        super().closeEvent(event)
