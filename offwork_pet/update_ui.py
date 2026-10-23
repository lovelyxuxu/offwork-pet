"""Background update checks and downloads, followed by an explicit restart."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import Request, urlopen
import uuid

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout

from . import __version__
from .core import data_dir
from .ui_theme import card, label, style_window
from .update_format import MANIFEST_URL, UpdateError, select_delta, verify_manifest, version_key
from .update_install import clean_job, marker, owned_path, remove_owned, stage_release, validate_install
from .update_delta import stage_delta

CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000
RETRY_INTERVAL_MS = 30 * 60 * 1000


def fetch_manifest():
    with urlopen(Request(MANIFEST_URL, headers={'User-Agent': 'OffworkPet/' + __version__}), timeout=12) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise UpdateError('更新信息过大，请稍后重试')
    document = json.loads(raw)
    verify_manifest(document)
    return document


class UpdateWorker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, release=None, parent=None):
        super().__init__(parent)
        self.document = release
        self.job = self.stage = self.install = None
        self.token = uuid.uuid4().hex

    def cancelled(self):
        if self.isInterruptionRequested():
            raise UpdateError('已取消更新')

    def download_archive(self, artifact, archive, incremental=False):
        self.cancelled()
        if shutil.disk_usage(self.job).free < artifact['size'] + 64 * 1024 * 1024:
            raise UpdateError('磁盘空间不足，请清理后重试')
        count = 0
        kind = '增量更新' if incremental else '完整更新'
        self.progress.emit(0, '正在下载' + kind + '，宠物仍可正常使用…')
        with urlopen(Request(artifact['url'], headers={'User-Agent': 'OffworkPet/' + __version__}), timeout=15) as response, archive.open('wb') as destination:
            while True:
                self.cancelled()
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > artifact['size']:
                    raise UpdateError('更新包大小与发布信息不一致')
                destination.write(chunk)
                self.progress.emit(min(85, int(count * 85 / artifact['size'])),
                    f'下载{kind} {count / 1048576:.1f} / {artifact["size"] / 1048576:.1f} MB')
        self.cancelled()

    def prepare_release(self, release, archive, progress):
        delta = select_delta(release, marker(self.install).get('version'))
        if delta:
            try:
                self.download_archive(delta, archive, incremental=True)
                self.stage = stage_delta(archive, self.install, self.token, release, delta, progress)
                return delta
            except Exception:
                # Cancellation must never start a second download.
                self.cancelled()
                stage = owned_path(self.install, self.token, 'stage')
                if stage.exists():
                    remove_owned(stage, self.install, self.token, 'stage')
                archive.unlink(missing_ok=True)
                self.progress.emit(0, '增量更新暂不可用，正在切换为完整更新…')
        self.download_archive(release, archive)
        self.stage = stage_release(archive, self.install, self.token, release, progress)
        return None

    def run(self):
        try:
            if self.document is None:
                document = fetch_manifest()
                self.cancelled()
                self.result.emit(('checked', document))
                return
            if not getattr(sys, 'frozen', False):
                raise UpdateError('开发模式只检查版本，请使用发布版进行自动更新')
            release = verify_manifest(self.document)
            if version_key(release['version']) <= version_key(__version__):
                raise UpdateError('当前版本无需更新，请重新检查版本')
            self.install = validate_install(Path(sys.executable).parent)
            self.job = data_dir() / 'updates' / self.token
            self.job.mkdir(parents=True)
            archive = self.job / 'release.zip'
            def progress(value):
                self.cancelled()
                self.progress.emit(85 + value * 14 // 100, '正在校验并准备新版本…')
            delta = self.prepare_release(release, archive, progress)
            source = (self.stage if delta else self.install) / 'OffworkPetUpdater.exe'
            if not source.is_file():
                raise UpdateError('更新组件缺失，请重新下载完整发布版')
            shutil.copy2(source, self.job / 'OffworkPetUpdater.exe')
            plan = {'manifest': self.document, 'install': str(self.install), 'pid': os.getpid()}
            if delta:
                plan['deltaFrom'] = delta['fromVersion']
            (self.job / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            self.cancelled()
            self.result.emit(('prepared', str(self.job)))
        except Exception as error:
            try:
                if self.install:
                    stage = owned_path(self.install, self.token, 'stage')
                    if stage.exists():
                        remove_owned(stage, self.install, self.token, 'stage')
                if self.job and self.job.exists():
                    # The job name is generated above and constrained to this app's updates directory.
                    if self.job.resolve().parent == (data_dir() / 'updates').resolve():
                        shutil.rmtree(self.job)
            except OSError:
                pass
            text = str(error) if isinstance(error, (UpdateError, PermissionError)) else '暂时无法完成更新，请检查网络后重试'
            if isinstance(error, PermissionError):
                text = '程序目录不可写，请把完整程序文件夹移到有写入权限的位置后重试'
            self.failed.emit(text)


class UpdateDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.pet)
        self.controller = controller
        self.setWindowTitle('下班搭子 · 软件更新')
        self.setMinimumWidth(480)
        style_window(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        root.addWidget(label('让搭子，常用常新。', 'heading'))
        root.addWidget(label('下载、替换和重启交给程序完成。', 'subtitle'))
        surface, layout = card('当前版本  ' + __version__)
        self.status = label('')
        layout.addWidget(self.status)
        self.notes = label('', 'muted')
        layout.addWidget(self.notes)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setStyleSheet('QProgressBar{border:none;background:#E8E4F3;border-radius:5px;height:10px;text-align:center;} QProgressBar::chunk{background:#7964CB;border-radius:5px;}')
        layout.addWidget(self.progress)
        root.addWidget(surface)
        self.automatic = QCheckBox('自动检查更新，有新版时提醒我')
        self.automatic.setChecked(controller.pet.config.auto_update_check)
        self.automatic.toggled.connect(controller.set_automatic)
        root.addWidget(self.automatic)
        root.addWidget(label('更新会保留你的设置、已导入角色和搭子关系。', 'muted'))
        buttons = QHBoxLayout()
        self.check = QPushButton('检查更新')
        self.check.clicked.connect(lambda: controller.check(manual=True))
        self.cancel = QPushButton('取消下载')
        self.cancel.clicked.connect(controller.cancel_download)
        self.install = QPushButton('更新并重启')
        self.install.setProperty('variant', 'primary')
        self.install.clicked.connect(controller.download)
        buttons.addWidget(self.check)
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.install)
        root.addLayout(buttons)
        controller.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        controller = self.controller
        self.status.setText(controller.message)
        self.notes.setText((controller.document or {}).get('release', {}).get('notes', ''))
        self.progress.setVisible(controller.downloading)
        self.progress.setValue(controller.percent)
        busy = controller.busy or controller.waiting_helper
        self.check.setEnabled(not busy)
        self.install.setVisible(controller.available)
        self.install.setEnabled(not busy)
        self.cancel.setVisible(controller.downloading and not controller.waiting_helper)
        self.cancel.setEnabled(controller.busy)
        self.automatic.blockSignals(True)
        self.automatic.setChecked(controller.pet.config.auto_update_check)
        self.automatic.blockSignals(False)

    def closeEvent(self, event):
        self.hide()
        event.ignore()


class UpdateController(QObject):
    changed = Signal()

    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.document = self.task = self.dialog = None
        self.message = '检查一下，看看有没有新的小变化。'
        self.available = self.downloading = self.waiting_helper = self.closing = False
        self.percent = 0
        self.prompted = set()
        self.helper = self.job = None
        self.helper_deadline = 0
        self.helper_timer = QTimer(self)
        self.helper_timer.timeout.connect(self.poll_helper)
        self.check_timer = QTimer(self)
        self.check_timer.setSingleShot(True)
        self.check_timer.timeout.connect(self.automatic_check)
        self.configure_automatic()
        if pet.persist:
            QTimer.singleShot(6000, self.clean_completed_jobs)

    @property
    def busy(self):
        return bool(self.task and self.task.isRunning())

    def show(self):
        if self.dialog is None:
            self.dialog = UpdateDialog(self)
        self.dialog.refresh()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def set_automatic(self, enabled):
        self.pet.config.auto_update_check = enabled
        self.pet.write_config()
        self.configure_automatic()

    def configure_automatic(self):
        if self.closing or not self.pet.persist or not self.pet.config.auto_update_check:
            self.check_timer.stop()
        elif not self.check_timer.isActive():
            self.check_timer.start(8000)

    def schedule_check(self, delay=CHECK_INTERVAL_MS):
        if not self.closing and self.pet.persist and self.pet.config.auto_update_check:
            self.check_timer.start(delay)

    def automatic_check(self):
        if not self.pet.config.auto_update_check or self.closing:
            return
        if self.busy or self.waiting_helper:
            self.schedule_check(RETRY_INTERVAL_MS)
        else:
            self.check()

    def check(self, manual=False):
        if self.busy or self.waiting_helper or self.closing:
            if manual:
                self.show()
            return
        self.message = '正在检查更新…'
        self.available = False
        self.downloading = False
        self.task = UpdateWorker(parent=self)
        self.task.result.connect(lambda result: self.completed(result, manual))
        self.task.failed.connect(self.failed)
        self.task.finished.connect(lambda task=self.task: self.task_finished(task))
        self.task.start()
        if manual:
            self.show()
        self.changed.emit()

    def completed(self, result, manual=False):
        if self.closing:
            return
        kind, value = result
        if kind == 'checked':
            self.document = value
            self.available = version_key(value['release']['version']) > version_key(__version__)
            self.message = ('发现新版本  ' + value['release']['version']) if self.available else '已经是最新版本，安心陪伴。'
            version = value['release']['version']
            if self.available and (manual or version not in self.prompted):
                self.prompted.add(version)
                self.show()
            self.schedule_check()
        else:
            self.start_helper(Path(value))
        self.changed.emit()

    def download(self):
        if not self.available or self.busy or self.waiting_helper:
            return
        self.downloading = True
        self.percent = 0
        self.message = '开始下载更新…'
        self.task = UpdateWorker(self.document, self)
        self.task.progress.connect(self.progress)
        self.task.result.connect(self.completed)
        self.task.failed.connect(self.failed)
        self.task.finished.connect(lambda task=self.task: self.task_finished(task))
        self.task.start()
        self.changed.emit()

    def task_finished(self, task):
        if self.task is task:
            self.task = None
        task.deleteLater()
        self.changed.emit()

    def clean_completed_jobs(self):
        root = data_dir() / 'updates'
        if not root.is_dir():
            return
        for job in list(root.iterdir())[:50]:
            if job.is_dir() and (job / 'result.json').is_file():
                try:
                    result = json.loads((job / 'result.json').read_text(encoding='utf-8'))
                    if result.get('ok') is True:
                        clean_job(job, root)
                except (OSError, ValueError, TypeError):
                    pass

    def progress(self, value, message):
        self.percent, self.message = value, message
        self.changed.emit()

    def failed(self, message):
        if self.closing:
            return
        self.downloading = False
        self.message = message
        self.changed.emit()
        self.schedule_check(RETRY_INTERVAL_MS)
        # Automatic checks fail quietly; the pet remains usable.

    def cancel_download(self):
        if self.downloading and self.busy and not self.waiting_helper:
            self.task.requestInterruption()
            self.message = '正在取消并清理本次下载…'
            self.changed.emit()

    def start_helper(self, job):
        self.job = job
        try:
            self.helper = subprocess.Popen([str(job / 'OffworkPetUpdater.exe'), str(job / 'plan.json')], cwd=job,
                env=dict(os.environ, OFFWORK_HOME=str(data_dir())), creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            self.discard_prepared(job)
            self.failed('更新组件无法启动，旧版继续可用，请稍后重试')
            return
        self.waiting_helper = True
        self.helper_deadline = time.monotonic() + 20
        self.message = '更新已准备好，正在安全重启…'
        self.helper_timer.start(100)

    def discard_prepared(self, job):
        try:
            job = Path(job)
            if job.resolve().parent != (data_dir() / 'updates').resolve():
                return
            install = validate_install(Path(sys.executable).parent)
            stage = owned_path(install, job.name, 'stage')
            if stage.exists():
                remove_owned(stage, install, job.name, 'stage')
            clean_job(job, data_dir() / 'updates')
        except (OSError, ValueError):
            # Keep recoverable files if a cleanup path is invalid or still locked.
            pass

    def poll_helper(self):
        if (self.job / 'helper-ready').exists() and self.helper.poll() is None:
            self.helper_timer.stop()
            self.waiting_helper = False
            self.pet.quit()
        elif self.helper.poll() is not None or time.monotonic() > self.helper_deadline:
            self.helper_timer.stop()
            self.waiting_helper = False
            if self.helper.poll() is None:
                self.helper.terminate()
            self.failed('更新组件未能就绪，旧版继续可用，请重新尝试')

    def shutdown(self):
        self.closing = True
        self.check_timer.stop()
        if self.busy:
            self.task.requestInterruption()
            self.task.wait(20000)
        if self.dialog:
            self.dialog.hide()
