"""Prepare immutable character content off the GUI thread; commit only the latest choice."""
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, Signal

from .packages import PackageError, load_package
from .package_player import FrameCache


def prepare_character(root):
    package = load_package(root)
    animation = package.behavior['states'][package.behavior['initialState']]['animation']
    first = package.behavior['animations'][animation]['frames'][0]
    cache = FrameCache()
    cache.image(package, first['file'])
    return package, cache


class CharacterLoader(QObject):
    completed = Signal(int, object, object)

    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.generation = 0
        self.closed = False
        self.pool = None
        self.future = None
        self.callback = None
        self.completed.connect(self.finish, Qt.ConnectionType.QueuedConnection)
        pet.destroyed.connect(self.shutdown)

    def cancel(self):
        self.generation += 1
        self.callback = None
        future, self.future = self.future, None
        if future:
            future.cancel()

    def request(self, identifier, callback):
        self.cancel()
        if self.closed:
            return
        try:
            root = self.pet.library.path(identifier)
            expected = dict(self.pet.library.index['packages'][identifier])
        except (PackageError, OSError) as error:
            callback(error)
            return
        if self.pool is None:
            self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='character-loader')
        self.callback = callback
        self.expected = identifier, expected
        generation = self.generation
        self.future = self.pool.submit(prepare_character, root)

        def done(future):
            if future.cancelled():
                return
            try:
                result, error = future.result(), None
            except Exception as exc:
                result, error = None, exc
            if not self.closed and generation == self.generation:
                try:
                    self.completed.emit(generation, result, error)
                except RuntimeError:
                    pass  # The owning window was destroyed while decoding.
        self.future.add_done_callback(done)

    def finish(self, generation, prepared, error):
        if self.closed or generation != self.generation:
            return
        callback, self.callback = self.callback, None
        identifier, expected = self.expected
        if self.pet.library.index['packages'].get(identifier) != expected:
            error = PackageError('角色已更新或删除，请重新选择')
        if error is None:
            try:
                self.pet.select_package(identifier, _prepared=prepared)
            except Exception as exc:
                error = exc
        if callback:
            callback(error)

    def shutdown(self):
        self.closed = True
        self.cancel()
        if self.pool:
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.pool = None
