"""Foreground-only controls for PilferedParrot's native browser window.

Bindings begin with a server-created document marker.  Native identifiers are
private, and every action rechecks the foreground window's identity before it
can reach Xlib or user32.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable


_MARKER = re.compile(r"\APilferedParrot Native [0-9a-f]{32}\Z")
_RESTORED_TITLE = re.compile(r"\APilferedParrot(?:\b|[ —:-])", re.IGNORECASE)
_PPI_X11_CLASS = re.compile(r"\Apilferedparrot(?:-[a-z0-9_-]+)?\Z", re.IGNORECASE)
_RESIZE_DIRECTIONS = frozenset((
    "north", "south", "east", "west", "northeast", "northwest", "southeast", "southwest",
))


class NativeWindowError(RuntimeError):
    """A native operation was unavailable, rejected, or no longer safe."""


class UnsupportedPlatform(NativeWindowError):
    """The current desktop cannot provide these controls."""


class WindowNotAllowed(NativeWindowError):
    """The foreground candidate is not PilferedParrot's Chromium host."""


def _is_marker(value: object) -> bool:
    return isinstance(value, str) and _MARKER.fullmatch(value) is not None


def _is_allowed(title: str, window_class: str) -> bool:
    """The initial candidate must have both the exact marker and browser class."""
    folded_class = window_class.casefold()
    return _is_marker(title) and (
        _PPI_X11_CLASS.fullmatch(window_class) is not None
        or any(token in folded_class for token in ("chrome", "chromium", "msedge"))
    )


class _Backend:
    def bind(self, expected_title: str) -> "_BackendBinding | None":
        raise NotImplementedError

    def minimize(self, binding: "_BackendBinding") -> bool: raise NotImplementedError
    def maximize_or_restore(self, binding: "_BackendBinding") -> bool: raise NotImplementedError
    def close(self, binding: "_BackendBinding") -> bool: raise NotImplementedError
    def drag(self, binding: "_BackendBinding") -> bool: raise NotImplementedError
    def resize(self, binding: "_BackendBinding", direction: str) -> bool: raise NotImplementedError


@dataclass(frozen=True)
class _BackendBinding:
    backend: _Backend
    handle: Any
    expected_title: str
    window_class: str
    process_id: int | None


class BoundNativeWindow:
    """An opaque binding whose actions have no handle, process, or title input."""

    def __init__(self, binding: _BackendBinding):
        self._binding = binding

    def _dispatch(self, action: str, *data: str) -> bool:
        try:
            return bool(getattr(self._binding.backend, action)(self._binding, *data))
        except (NativeWindowError, OSError, ctypes.ArgumentError):
            return False

    def minimize(self) -> bool: return self._dispatch("minimize")
    def maximize_or_restore(self) -> bool: return self._dispatch("maximize_or_restore")
    def close(self) -> bool: return self._dispatch("close")
    def drag(self) -> bool: return self._dispatch("drag")

    def resize(self, direction: str) -> bool:
        if direction not in _RESIZE_DIRECTIONS:
            raise ValueError("resize direction must be one of the eight compass directions")
        return self._dispatch("resize", direction)


class NativeWindowAdapter:
    """Create one binding from the foreground window and an exact nonce marker."""

    def __init__(self, backend: _Backend | None = None):
        self._backend = backend or _make_backend()

    def bind_active_window(self, expected_title: str) -> BoundNativeWindow | None:
        """Bind only when *expected_title* is an exact server-generated marker."""
        if not _is_marker(expected_title):
            return None
        try:
            binding = self._backend.bind(expected_title)
        except (NativeWindowError, OSError, ctypes.ArgumentError):
            return None
        return BoundNativeWindow(binding) if binding is not None else None


def _make_backend() -> _Backend:
    if sys.platform == "win32":
        return _WindowsBackend()
    if sys.platform.startswith(("linux", "freebsd")):
        return _X11Backend()
    return _UnsupportedBackend()


class _UnsupportedBackend(_Backend):
    def bind(self, expected_title: str) -> None: return None
    def minimize(self, binding: _BackendBinding) -> bool: return False
    def maximize_or_restore(self, binding: _BackendBinding) -> bool: return False
    def close(self, binding: _BackendBinding) -> bool: return False
    def drag(self, binding: _BackendBinding) -> bool: return False
    def resize(self, binding: _BackendBinding, direction: str) -> bool: return False


# Xlib's Window and Atom typedefs are unsigned long, including on 64-bit X11.
_XDisplay = ctypes.c_void_p
_XWindow = ctypes.c_ulong
_XAtom = ctypes.c_ulong
_XBool = ctypes.c_int
_XStatus = ctypes.c_int
_XTime = ctypes.c_ulong


class _XClientMessageData(ctypes.Union):
    _fields_ = [("b", ctypes.c_char * 20), ("s", ctypes.c_short * 10), ("l", ctypes.c_long * 5)]


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", _XBool),
        ("display", _XDisplay), ("window", _XWindow), ("message_type", _XAtom),
        ("format", ctypes.c_int), ("data", _XClientMessageData),
    ]


class _XEvent(ctypes.Union):
    # XEvent is a union whose ABI storage is long pad[24], not just xclient.
    _fields_ = [("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


class _XClassHint(ctypes.Structure):
    _fields_ = [("res_name", ctypes.c_void_p), ("res_class", ctypes.c_void_p)]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int), ("display", _XDisplay), ("resourceid", _XWindow),
        ("serial", ctypes.c_ulong), ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte), ("minor_code", ctypes.c_ubyte),
    ]


_XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, _XDisplay, ctypes.POINTER(_XErrorEvent))
_X_CLIENT_MESSAGE = 33
_X_PROP_MODE_REPLACE = 0
_X_SUBSTRUCTURE_MASK = (1 << 20) | (1 << 19)
_X_CURRENT_TIME = 0
_X_NET_WM_STATE_TOGGLE = 2
_X_MOVERESIZE_MOVE = 8
_X_MOVERESIZE_DIRECTIONS = {
    "northwest": 0, "north": 1, "northeast": 2, "east": 3,
    "southeast": 4, "south": 5, "southwest": 6, "west": 7,
}


class _X11Backend(_Backend):
    """A serialized Xlib backend, including Chromium windows hosted by XWayland."""

    def __init__(self):
        self._display: int | None = None
        self._x11: Any | None = None
        self._lock = threading.RLock()

    def _configure(self, x: Any) -> None:
        x.XOpenDisplay.argtypes, x.XOpenDisplay.restype = [ctypes.c_char_p], _XDisplay
        x.XDefaultRootWindow.argtypes, x.XDefaultRootWindow.restype = [_XDisplay], _XWindow
        x.XDefaultScreen.argtypes, x.XDefaultScreen.restype = [_XDisplay], ctypes.c_int
        x.XInternAtom.argtypes, x.XInternAtom.restype = [_XDisplay, ctypes.c_char_p, _XBool], _XAtom
        x.XGetWindowProperty.argtypes = [_XDisplay, _XWindow, _XAtom, ctypes.c_long, ctypes.c_long, _XBool, _XAtom, ctypes.POINTER(_XAtom), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
        x.XGetWindowProperty.restype = _XStatus
        x.XFetchName.argtypes, x.XFetchName.restype = [_XDisplay, _XWindow, ctypes.POINTER(ctypes.c_void_p)], _XStatus
        x.XGetClassHint.argtypes, x.XGetClassHint.restype = [_XDisplay, _XWindow, ctypes.POINTER(_XClassHint)], _XStatus
        x.XFree.argtypes, x.XFree.restype = [ctypes.c_void_p], ctypes.c_int
        x.XChangeProperty.argtypes, x.XChangeProperty.restype = [_XDisplay, _XWindow, _XAtom, _XAtom, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int], ctypes.c_int
        x.XIconifyWindow.argtypes, x.XIconifyWindow.restype = [_XDisplay, _XWindow, ctypes.c_int], _XStatus
        x.XSendEvent.argtypes, x.XSendEvent.restype = [_XDisplay, _XWindow, _XBool, ctypes.c_long, ctypes.POINTER(_XEvent)], _XStatus
        x.XQueryPointer.argtypes, x.XQueryPointer.restype = [_XDisplay, _XWindow, ctypes.POINTER(_XWindow), ctypes.POINTER(_XWindow), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint)], _XBool
        x.XUngrabPointer.argtypes, x.XUngrabPointer.restype = [_XDisplay, _XTime], ctypes.c_int
        x.XFlush.argtypes, x.XFlush.restype = [_XDisplay], ctypes.c_int
        x.XSync.argtypes, x.XSync.restype = [_XDisplay, _XBool], ctypes.c_int
        x.XSetErrorHandler.argtypes, x.XSetErrorHandler.restype = [_XErrorHandler], _XErrorHandler

    def _load_locked(self) -> tuple[Any, int]:
        if not os.environ.get("DISPLAY"):
            raise UnsupportedPlatform("native controls require an X11 display")
        if self._x11 is None:
            self._x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            self._configure(self._x11)
        if not self._display:
            self._display = self._x11.XOpenDisplay(None)
        if not self._display:
            raise UnsupportedPlatform("could not open the X11 display")
        return self._x11, self._display

    def _checked(self, operation: Callable[[Any, int], Any]) -> Any:
        """Run every Xlib sequence under one lock and synchronize X errors."""
        with self._lock:
            x, display = self._load_locked()
            errors: list[int] = []

            @_XErrorHandler
            def record_error(_display: int, event: ctypes.POINTER(_XErrorEvent)) -> int:
                if event:
                    errors.append(int(event.contents.error_code))
                return 0

            previous = x.XSetErrorHandler(record_error)
            try:
                result = operation(x, display)
                x.XSync(display, 0)
            finally:
                x.XSetErrorHandler(previous)
            if errors:
                raise NativeWindowError("X11 rejected the window operation")
            return result

    @staticmethod
    def _take_string(x: Any, pointer: int | None) -> str:
        if not pointer:
            return ""
        try:
            return ctypes.string_at(pointer).decode("utf-8", errors="replace")
        finally:
            x.XFree(ctypes.c_void_p(pointer))

    def _atom(self, x: Any, display: int, name: str) -> int:
        return int(x.XInternAtom(display, name.encode("ascii"), 0))

    def _cardinal(self, x: Any, display: int, window: int, property_name: str) -> int | None:
        actual_type, actual_format = _XAtom(), ctypes.c_int()
        items, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        status = x.XGetWindowProperty(display, window, self._atom(x, display, property_name), 0, 1, 0, 0, ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(items), ctypes.byref(after), ctypes.byref(data))
        if status or not data.value or items.value != 1 or actual_format.value != 32:
            if data.value:
                x.XFree(data)
            return None
        try:
            return int(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0])
        finally:
            x.XFree(data)

    def _text_property(
        self, x: Any, display: int, window: int, property_name: str,
    ) -> str:
        """Read one bounded UTF-8 EWMH string and release Xlib's allocation."""
        actual_type, actual_format = _XAtom(), ctypes.c_int()
        items, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        utf8 = self._atom(x, display, "UTF8_STRING")
        status = x.XGetWindowProperty(
            display, window, self._atom(x, display, property_name), 0, 1024,
            0, utf8, ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(items), ctypes.byref(after), ctypes.byref(data),
        )
        if (
            status or actual_type.value != utf8 or actual_format.value != 8
            or items.value > 4096 or after.value or not data.value
        ):
            if data.value:
                x.XFree(data)
            return ""
        try:
            return ctypes.string_at(data.value, items.value).decode(
                "utf-8", errors="replace",
            )
        finally:
            x.XFree(data)

    def _identity(self, x: Any, display: int, handle: int) -> tuple[str, str]:
        title = ctypes.c_void_p()
        hint = _XClassHint()
        x.XFetchName(display, handle, ctypes.byref(title))
        x.XGetClassHint(display, handle, ctypes.byref(hint))
        legacy_title = self._take_string(x, title.value)
        title_text = self._text_property(x, display, handle, "_NET_WM_NAME") \
            or legacy_title
        _ = self._take_string(x, hint.res_name)  # Xlib allocated it too.
        return title_text, self._take_string(x, hint.res_class)

    def _active(self, x: Any, display: int) -> int:
        return self._cardinal(x, display, x.XDefaultRootWindow(display), "_NET_ACTIVE_WINDOW") or 0

    def _valid(self, x: Any, display: int, binding: _BackendBinding) -> bool:
        if self._active(x, display) != binding.handle:
            return False
        title, window_class = self._identity(x, display, binding.handle)
        title_ok = title == binding.expected_title or _RESTORED_TITLE.match(title) is not None
        pid = self._cardinal(x, display, binding.handle, "_NET_WM_PID")
        return title_ok and window_class == binding.window_class and (binding.process_id is None or pid == binding.process_id)

    def _remove_decorations(self, x: Any, display: int, handle: int) -> None:
        # flags=MWM_HINTS_DECORATIONS (2), decorations=0.
        hints = (ctypes.c_ulong * 5)(2, 0, 0, 0, 0)
        atom = self._atom(x, display, "_MOTIF_WM_HINTS")
        x.XChangeProperty(display, handle, atom, atom, 32, _X_PROP_MODE_REPLACE, ctypes.cast(hints, ctypes.POINTER(ctypes.c_ubyte)), 5)
        x.XFlush(display)

    def bind(self, expected_title: str) -> _BackendBinding | None:
        try:
            def operation(x: Any, display: int) -> _BackendBinding | None:
                handle = self._active(x, display)
                if not handle:
                    return None
                title, window_class = self._identity(x, display, handle)
                if title != expected_title or not _is_allowed(title, window_class):
                    return None
                self._remove_decorations(x, display, handle)
                return _BackendBinding(self, handle, expected_title, window_class, self._cardinal(x, display, handle, "_NET_WM_PID"))
            return self._checked(operation)
        except (NativeWindowError, OSError, ctypes.ArgumentError):
            return None

    def _event(self, x: Any, display: int, binding: _BackendBinding, message: str, data: tuple[int, ...]) -> bool:
        event = _XEvent()
        event.xclient.type, event.xclient.send_event, event.xclient.display = _X_CLIENT_MESSAGE, 1, display
        event.xclient.window, event.xclient.message_type, event.xclient.format = binding.handle, self._atom(x, display, message), 32
        for index, value in enumerate(data[:5]):
            event.xclient.data.l[index] = value
        result = x.XSendEvent(display, x.XDefaultRootWindow(display), 0, _X_SUBSTRUCTURE_MASK, ctypes.byref(event))
        x.XFlush(display)
        return bool(result)

    def _pointer(self, x: Any, display: int) -> tuple[int, int] | None:
        root, child = _XWindow(), _XWindow()
        root_x, root_y, win_x, win_y, mask = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_uint()
        if not x.XQueryPointer(display, x.XDefaultRootWindow(display), ctypes.byref(root), ctypes.byref(child), ctypes.byref(root_x), ctypes.byref(root_y), ctypes.byref(win_x), ctypes.byref(win_y), ctypes.byref(mask)):
            return None
        return root_x.value, root_y.value

    def _action(self, binding: _BackendBinding, callback: Callable[[Any, int], bool]) -> bool:
        try:
            return bool(self._checked(lambda x, d: self._valid(x, d, binding) and callback(x, d)))
        except (NativeWindowError, OSError, ctypes.ArgumentError):
            return False

    def minimize(self, binding: _BackendBinding) -> bool:
        return self._action(binding, lambda x, d: bool(x.XIconifyWindow(d, binding.handle, x.XDefaultScreen(d))))

    def maximize_or_restore(self, binding: _BackendBinding) -> bool:
        return self._action(binding, lambda x, d: self._event(x, d, binding, "_NET_WM_STATE", (_X_NET_WM_STATE_TOGGLE, self._atom(x, d, "_NET_WM_STATE_MAXIMIZED_VERT"), self._atom(x, d, "_NET_WM_STATE_MAXIMIZED_HORZ"), 1, 0)))

    def close(self, binding: _BackendBinding) -> bool:
        return self._action(binding, lambda x, d: self._event(x, d, binding, "_NET_CLOSE_WINDOW", (0, 1, 0, 0, 0)))

    def _move_resize(self, binding: _BackendBinding, direction: int) -> bool:
        def operation(x: Any, display: int) -> bool:
            pointer = self._pointer(x, display)
            if pointer is None:
                return False
            x.XUngrabPointer(display, _X_CURRENT_TIME)
            return self._event(x, display, binding, "_NET_WM_MOVERESIZE", (pointer[0], pointer[1], direction, 1, 1))
        return self._action(binding, operation)

    def drag(self, binding: _BackendBinding) -> bool: return self._move_resize(binding, _X_MOVERESIZE_MOVE)
    def resize(self, binding: _BackendBinding, direction: str) -> bool:
        return self._move_resize(binding, _X_MOVERESIZE_DIRECTIONS[direction]) if direction in _X_MOVERESIZE_DIRECTIONS else False


class _WindowsBackend(_Backend):
    """Pointer-size-correct user32 adapter for the same opaque binding model."""

    _HWND, _LONG_PTR, _UINT, _WPARAM, _LPARAM, _LRESULT, _BOOL = ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_int
    _GWL_STYLE, _WS_CAPTION = -16, 0x00C00000
    _SW_MINIMIZE, _SW_MAXIMIZE, _SW_RESTORE = 6, 3, 9
    _SWP_FRAMECHANGED = 0x0020
    _SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | _SWP_FRAMECHANGED
    _WM_CLOSE, _WM_NCLBUTTONDOWN, _HTCAPTION = 0x0010, 0x00A1, 2
    _HIT_TEST = {"west": 10, "east": 11, "north": 12, "northwest": 13, "northeast": 14, "south": 15, "southwest": 16, "southeast": 17}

    def __init__(self):
        self._u = ctypes.WinDLL("user32", use_last_error=True)
        self._configure(self._u)

    def _configure(self, u: Any) -> None:
        u.GetForegroundWindow.argtypes, u.GetForegroundWindow.restype = [], self._HWND
        u.IsWindow.argtypes, u.IsWindow.restype = [self._HWND], self._BOOL
        u.GetWindowTextLengthW.argtypes, u.GetWindowTextLengthW.restype = [self._HWND], ctypes.c_int
        u.GetWindowTextW.argtypes, u.GetWindowTextW.restype = [self._HWND, ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int
        u.GetClassNameW.argtypes, u.GetClassNameW.restype = [self._HWND, ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int
        u.GetWindowThreadProcessId.argtypes, u.GetWindowThreadProcessId.restype = [self._HWND, ctypes.POINTER(ctypes.c_ulong)], ctypes.c_ulong
        u.GetWindowLongPtrW.argtypes, u.GetWindowLongPtrW.restype = [self._HWND, ctypes.c_int], self._LONG_PTR
        u.SetWindowLongPtrW.argtypes, u.SetWindowLongPtrW.restype = [self._HWND, ctypes.c_int, self._LONG_PTR], self._LONG_PTR
        u.SetWindowPos.argtypes, u.SetWindowPos.restype = [self._HWND, self._HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, self._UINT], self._BOOL
        u.ShowWindow.argtypes, u.ShowWindow.restype = [self._HWND, ctypes.c_int], self._BOOL
        u.IsZoomed.argtypes, u.IsZoomed.restype = [self._HWND], self._BOOL
        u.PostMessageW.argtypes, u.PostMessageW.restype = [self._HWND, self._UINT, self._WPARAM, self._LPARAM], self._BOOL
        u.ReleaseCapture.argtypes, u.ReleaseCapture.restype = [], self._BOOL
        u.SendMessageW.argtypes, u.SendMessageW.restype = [self._HWND, self._UINT, self._WPARAM, self._LPARAM], self._LRESULT

    def _identity(self, handle: int) -> tuple[str, str]:
        title = ctypes.create_unicode_buffer(max(1, int(self._u.GetWindowTextLengthW(handle)) + 1))
        window_class = ctypes.create_unicode_buffer(256)
        self._u.GetWindowTextW(handle, title, len(title))
        self._u.GetClassNameW(handle, window_class, len(window_class))
        return title.value, window_class.value

    def _pid(self, handle: int) -> int | None:
        pid = ctypes.c_ulong()
        self._u.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        return int(pid.value) or None

    def _valid(self, binding: _BackendBinding) -> bool:
        handle = binding.handle
        if not handle or not self._u.IsWindow(handle) or self._u.GetForegroundWindow() != handle:
            return False
        title, window_class = self._identity(handle)
        return (title == binding.expected_title or _RESTORED_TITLE.match(title) is not None) and window_class == binding.window_class and self._pid(handle) == binding.process_id

    def bind(self, expected_title: str) -> _BackendBinding | None:
        handle = self._u.GetForegroundWindow()
        if not handle:
            return None
        title, window_class = self._identity(handle)
        if title != expected_title or not _is_allowed(title, window_class):
            return None
        pid = self._pid(handle)
        if pid is None:
            return None
        style = self._u.GetWindowLongPtrW(handle, self._GWL_STYLE)
        self._u.SetWindowLongPtrW(handle, self._GWL_STYLE, style & ~self._WS_CAPTION)  # Retain WS_THICKFRAME.
        if not self._u.SetWindowPos(handle, None, 0, 0, 0, 0, self._SWP_FLAGS):
            return None
        return _BackendBinding(self, handle, expected_title, window_class, pid)

    def _action(self, binding: _BackendBinding, callback: Callable[[], bool]) -> bool:
        try:
            return bool(self._valid(binding) and callback())
        except (OSError, ctypes.ArgumentError):
            return False

    def minimize(self, binding: _BackendBinding) -> bool: return self._action(binding, lambda: bool(self._u.ShowWindow(binding.handle, self._SW_MINIMIZE)))
    def maximize_or_restore(self, binding: _BackendBinding) -> bool: return self._action(binding, lambda: bool(self._u.ShowWindow(binding.handle, self._SW_RESTORE if self._u.IsZoomed(binding.handle) else self._SW_MAXIMIZE)))
    def close(self, binding: _BackendBinding) -> bool: return self._action(binding, lambda: bool(self._u.PostMessageW(binding.handle, self._WM_CLOSE, 0, 0)))

    def _nonclient(self, binding: _BackendBinding, hit_test: int) -> bool:
        def operation() -> bool:
            self._u.ReleaseCapture()
            return bool(self._u.SendMessageW(binding.handle, self._WM_NCLBUTTONDOWN, hit_test, 0))
        return self._action(binding, operation)

    def drag(self, binding: _BackendBinding) -> bool: return self._nonclient(binding, self._HTCAPTION)
    def resize(self, binding: _BackendBinding, direction: str) -> bool:
        return self._nonclient(binding, self._HIT_TEST[direction]) if direction in self._HIT_TEST else False
