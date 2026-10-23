"""Read-only desktop geometry and visible horizontal ledges; no screen capture."""
from dataclasses import dataclass
import os
import sys


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float


@dataclass(frozen=True)
class Surface:
    key: str
    left: float
    right: float
    y: float
    origin_x: float = 0


@dataclass(frozen=True)
class Scene:
    bounds: Rect
    surfaces: tuple[Surface, ...]
    fullscreen: bool = False


def select_monitor(monitors, name, logical, ratio):
    if name in monitors:
        return monitors[name]
    # On Windows Qt keeps native screen origins but scales their sizes. QScreen's
    # name can be an EDID product name instead of the Win32 \\.\DISPLAYn device.
    for native in monitors.values():
        if (native.left == logical.left and native.top == logical.top
                and abs((native.right-native.left)/ratio-(logical.right-logical.left)) <= 2
                and abs((native.bottom-native.top)/ratio-(logical.bottom-logical.top)) <= 2):
            return native
    return None


def visible_surfaces(bounds, windows):
    """Windows are ordered front to back. Covered parts of a ledge are removed."""
    result = []
    above = []
    for key, rect in windows:
        if bounds.top <= rect.top < bounds.bottom:
            segments = [(max(bounds.left, rect.left), min(bounds.right, rect.right))]
            for blocker in above:
                if blocker.top <= rect.top < blocker.bottom:
                    remainder = []
                    for left, right in segments:
                        if blocker.right <= left or blocker.left >= right:
                            remainder.append((left, right))
                        else:
                            remainder.extend(((left, min(right, blocker.left)),
                                              (max(left, blocker.right), right)))
                    segments = remainder
            result.extend(Surface(key, left, right, rect.top, rect.left)
                          for left, right in segments if right-left >= 60)
        above.append(rect)
    result.append(Surface('desktop', bounds.left, bounds.right, bounds.bottom))
    return tuple(result)


class DesktopObserver:
    """Use DWM visible bounds, converted from native pixels to Qt screen coordinates."""
    def __init__(self):
        self.native = None
        self.internal = None
        self.internal_enabled = False
        if sys.platform == 'win32':
            self.native = _WindowsGeometry()

    def snapshot(self, screen):
        area = screen.availableGeometry()
        bounds = Rect(area.x(), area.y(), area.x()+area.width(), area.y()+area.height())
        if not self.native:
            return Scene(bounds, visible_surfaces(bounds, []))
        windows, fullscreen = self.native.windows(screen)
        surfaces = visible_surfaces(bounds, windows)
        if self.internal_enabled and not fullscreen:
            from .internal_surfaces import InternalSurfaces
            if self.internal is None:
                self.internal = InternalSurfaces()
            handle = self.native.user.GetForegroundWindow()
            key = f'window:{handle}'
            owner = next((rect for name, rect in windows if name == key), None)
            if owner is not None:
                rows = self.internal.request(handle, owner)
                extra = []
                for row in rows:
                    rect = self.native.logical_rect(row)
                    if (owner.left <= rect.left < rect.right <= owner.right and owner.top < rect.top < owner.bottom
                            and rect.top >= bounds.top and rect.top < bounds.bottom):
                        index = next(i for i, pair in enumerate(windows) if pair[0] == key)
                        # Occlusion uses only windows in front of the owning window, not its own client area.
                        candidate = visible_surfaces(bounds, [*windows[:index], (f'ui:{handle}:{row["id"]}', rect)])
                        extra.extend(s for s in candidate if s.key.startswith('ui:'))
                surfaces = tuple(extra)+surfaces
        elif self.internal is not None:
            self.internal.close()
            self.internal = None
        return Scene(bounds, surfaces, fullscreen)

    def close(self):
        if self.internal is not None:
            self.internal.close()
            self.internal = None


class _WindowsGeometry:
    def __init__(self):
        import ctypes as ct
        from ctypes import wintypes as wt
        self.ct, self.wt = ct, wt
        self.user = ct.WinDLL('user32', use_last_error=True)
        self.dwm = ct.WinDLL('dwmapi', use_last_error=True)
        self.callback = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        self.monitor_callback = ct.WINFUNCTYPE(wt.BOOL, wt.HANDLE, wt.HDC, ct.POINTER(wt.RECT), wt.LPARAM)
        class MonitorInfo(ct.Structure):
            _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT),
                        ('rcWork', wt.RECT), ('dwFlags', wt.DWORD), ('szDevice', wt.WCHAR*32)]
        self.MonitorInfo = MonitorInfo
        signatures = {
            'EnumWindows': ([self.callback, wt.LPARAM], wt.BOOL),
            'EnumDisplayMonitors': ([wt.HDC, ct.POINTER(wt.RECT), self.monitor_callback, wt.LPARAM], wt.BOOL),
            'GetMonitorInfoW': ([wt.HANDLE, ct.POINTER(MonitorInfo)], wt.BOOL),
            'GetForegroundWindow': ([], wt.HWND),
            'IsWindowVisible': ([wt.HWND], wt.BOOL),
            'IsIconic': ([wt.HWND], wt.BOOL),
            'GetWindowThreadProcessId': ([wt.HWND, ct.POINTER(wt.DWORD)], wt.DWORD),
            'GetWindowLongW': ([wt.HWND, ct.c_int], wt.LONG),
            'GetWindowRect': ([wt.HWND, ct.POINTER(wt.RECT)], wt.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = arguments, result
        self.dwm.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ct.c_void_p, wt.DWORD]
        self.dwm.DwmGetWindowAttribute.restype = ct.c_long

    def windows(self, screen):
        ct, wt = self.ct, self.wt
        monitors = {}
        @self.monitor_callback
        def monitor(handle, dc, rectangle, data):
            info = self.MonitorInfo()
            info.cbSize = ct.sizeof(info)
            if self.user.GetMonitorInfoW(handle, ct.byref(info)):
                monitors[info.szDevice] = info.rcMonitor
            return True
        self.user.EnumDisplayMonitors(None, None, monitor, 0)
        logical = screen.geometry()
        ratio = screen.devicePixelRatio()
        native = select_monitor(monitors, screen.name(),
            Rect(logical.x(), logical.y(), logical.x()+logical.width(), logical.y()+logical.height()), ratio)
        if native is None:
            # Never mix physical and logical coordinates when the monitor is unknown.
            return [], False
        self.transform = (logical.x(), logical.y(), native.left, native.top, ratio)
        foreground = self.user.GetForegroundWindow()
        windows, full = [], False
        @self.callback
        def visit(handle, data):
            nonlocal full
            if not self.user.IsWindowVisible(handle) or self.user.IsIconic(handle):
                return True
            pid = wt.DWORD()
            self.user.GetWindowThreadProcessId(handle, ct.byref(pid))
            if pid.value == os.getpid():
                return True
            cloak = wt.DWORD()
            if self.dwm.DwmGetWindowAttribute(handle, 14, ct.byref(cloak), ct.sizeof(cloak)) == 0 and cloak.value:
                return True
            rectangle = wt.RECT()
            if self.dwm.DwmGetWindowAttribute(handle, 9, ct.byref(rectangle), ct.sizeof(rectangle)) != 0:
                if not self.user.GetWindowRect(handle, ct.byref(rectangle)):
                    return True
            if rectangle.right <= native.left or rectangle.left >= native.right or rectangle.bottom <= native.top or rectangle.top >= native.bottom:
                return True
            style = self.user.GetWindowLongW(handle, -16)
            # Shell desktop windows have no caption either, but are not foreground apps.
            if handle == foreground and not style & 0x00C00000 and style & 0x80000000:
                full = (rectangle.left <= native.left and rectangle.top <= native.top
                        and rectangle.right >= native.right and rectangle.bottom >= native.bottom)
            rect = Rect(logical.x()+(rectangle.left-native.left)/ratio,
                        logical.y()+(rectangle.top-native.top)/ratio,
                        logical.x()+(rectangle.right-native.left)/ratio,
                        logical.y()+(rectangle.bottom-native.top)/ratio)
            # Ignore tiny popups and shell surfaces as destinations, but keep popups as occluders.
            if style & 0x00C00000 or style & 0x80000000:
                windows.append((f'window:{handle}', rect))
            return True
        self.user.EnumWindows(visit, 0)
        return windows, full

    def logical_rect(self, row):
        x, y, left, top, ratio = self.transform
        return Rect(x+(row['left']-left)/ratio, y+(row['top']-top)/ratio,
                    x+(row['right']-left)/ratio, y+(row['bottom']-top)/ratio)
