"""Canvas-aligned frame rendering with a decoded-byte LRU, shared by preview/runtime."""
from collections import OrderedDict
from PIL import Image

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform

from .packages import PackageError


class FrameCache:
    def __init__(self, budget=48 * 1024**2):
        self.budget = budget
        self.bytes = 0
        self.items = OrderedDict()

    def image(self, package, path):
        key = package.digest, path
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key]
        image = QImage(str(package.asset(path)))
        if image.isNull():
            try:
                with Image.open(package.asset(path)) as source:
                    if source.width * source.height * 4 > self.budget:
                        raise PackageError(f'{path}: 图片超过播放缓存预算')
                    rgba = source.convert('RGBA')
                    image = QImage(rgba.tobytes(), rgba.width, rgba.height, QImage.Format.Format_RGBA8888).copy()
            except OSError as error:
                raise PackageError(f'{path}: 无法读取图片') from error
        size = image.sizeInBytes()
        if size > self.budget:
            raise PackageError(f'{path}: 图片超过播放缓存预算')
        while self.items and self.bytes + size > self.budget:
            _, old = self.items.popitem(last=False)
            self.bytes -= old.sizeInBytes()
        self.items[key] = image
        self.bytes += size
        return image

    def clear(self):
        self.items.clear()
        self.bytes = 0


class PackagePlayer:
    def __init__(self, runtime, budget=48 * 1024**2):
        self.runtime = runtime
        self.cache = FrameCache(budget)
        canvas = runtime.package.manifest['canvas']
        factor = min(220/canvas['width'], 170/canvas['height'])
        self.transform = QTransform().translate(140, 264).scale(factor, factor).translate(-canvas['anchor'][0], -canvas['anchor'][1])

    def point(self, point):
        inverse, _ = self.transform.inverted()
        position = inverse.map(QPointF(point))
        return position.x(), position.y()

    def contains(self, point):
        if self.runtime.stopped:
            return False
        frame = self.runtime.frame()
        canvas = self.runtime.package.manifest['canvas']
        draw = QRectF(*frame.get('drawRect', [0, 0, canvas['width'], canvas['height']]))
        local = QPointF(*self.point(point))
        if not draw.contains(local):
            return False
        image = self.cache.image(self.runtime.package, frame['file'])
        source = QRectF(*frame.get('rect', [0, 0, image.width(), image.height()]))
        x = int(source.x() + (local.x()-draw.x())/draw.width()*source.width())
        y = int(source.y() + (local.y()-draw.y())/draw.height()*source.height())
        return image.pixelColor(min(image.width()-1, x), min(image.height()-1, y)).alpha() > 30

    def draw(self, painter):
        if self.runtime.stopped:
            return
        frame = self.runtime.frame()
        image = self.cache.image(self.runtime.package, frame['file'])
        canvas = self.runtime.package.manifest['canvas']
        painter.save()
        painter.setTransform(self.transform, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(QRectF(*frame.get('drawRect', [0, 0, canvas['width'], canvas['height']])), image,
                          QRectF(*frame.get('rect', [0, 0, image.width(), image.height()])))
        painter.restore()

    def close(self):
        self.runtime.close()
        self.cache.clear()


def package_icon(package, size=64):
    return QPixmap(str(package.asset(package.manifest['thumbnail']))).scaled(size, size,
        Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
