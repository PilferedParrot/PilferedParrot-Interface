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
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable


_MARKER = re.compile(r"\APilferedParrot Native [0-9a-f]{32}\Z")
_RESTORED_TITLE = re.compile(r"\APilferedParrot(?:\b|[ —:-])", re.IGNORECASE)
_PPI_X11_CLASS = re.compile(r"\Apilferedparrot(?:-[a-z0-9_-]+)?\Z", re.IGNORECASE)
_MIN_WINDOW_SIZE = 64
_MAX_COORDINATE = (1 << 31) - 1
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


@dataclass(frozen=True)
class _Rect:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class _Gesture:
    direction: str | None  # None is a whole-window move.
    pointer_x: int
    pointer_y: int
    rect: _Rect
    maximized: bool = False


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _gesture_rect(gesture: _Gesture, pointer_x: int, pointer_y: int) -> _Rect:
    """Apply a physical pointer delta to the rectangle captured at gesture start."""
    delta_x = pointer_x - gesture.pointer_x
    delta_y = pointer_y - gesture.pointer_y
    rect = gesture.rect
    left, top, width, height = rect.left, rect.top, rect.width, rect.height
    direction = gesture.direction
    if direction is None:
        return _Rect(
            _clamp(left + delta_x, -_MAX_COORDINATE, _MAX_COORDINATE),
            _clamp(top + delta_y, -_MAX_COORDINATE, _MAX_COORDINATE), width, height,
        )
    right, bottom = left + width, top + height
    if "west" in direction:
        left = min(right - _MIN_WINDOW_SIZE, left + delta_x)
        width = right - left
    if "east" in direction:
        width = max(_MIN_WINDOW_SIZE, width + delta_x)
    if "north" in direction:
        top = min(bottom - _MIN_WINDOW_SIZE, top + delta_y)
        height = bottom - top
    if "south" in direction:
        height = max(_MIN_WINDOW_SIZE, height + delta_y)
    return _Rect(
        _clamp(left, -_MAX_COORDINATE, _MAX_COORDINATE),
        _clamp(top, -_MAX_COORDINATE, _MAX_COORDINATE),
        _clamp(width, _MIN_WINDOW_SIZE, _MAX_COORDINATE),
        _clamp(height, _MIN_WINDOW_SIZE, _MAX_COORDINATE),
    )


class _Backend:
    def bind(self, expected_title: str) -> "_BackendBinding | None":
        raise NotImplementedError

    def minimize(self, binding: "_BackendBinding") -> bool: raise NotImplementedError
    def maximize_or_restore(self, binding: "_BackendBinding") -> bool: raise NotImplementedError
    def close(self, binding: "_BackendBinding") -> bool: raise NotImplementedError
    def begin_geometry(self, binding: "_BackendBinding", direction: str | None) -> bool: raise NotImplementedError
    def update_geometry(self, binding: "_BackendBinding") -> bool: raise NotImplementedError


@dataclass
class _BackendBinding:
    backend: _Backend
    handle: Any
    expected_title: str
    window_class: str
    process_id: int | None
    _gesture: _Gesture | None = field(default=None, repr=False)
    _gesture_lock: Any = field(default_factory=threading.RLock, repr=False)


class BoundNativeWindow:
    """An opaque binding whose actions have no handle, process, or title input."""

    def __init__(self, binding: _BackendBinding):
        self._binding = binding

    def _dispatch(self, action: str, *data: Any) -> bool:
        with self._binding._gesture_lock:
            try:
                return bool(getattr(self._binding.backend, action)(self._binding, *data))
            except (NativeWindowError, OSError, ctypes.ArgumentError):
                return False

    def minimize(self) -> bool: return self._dispatch("minimize")
    def maximize_or_restore(self) -> bool: return self._dispatch("maximize_or_restore")
    def close(self) -> bool: return self._dispatch("close")

    def begin_move(self) -> bool:
        return self._dispatch("begin_geometry", None)

    def begin_resize(self, direction: str) -> bool:
        if direction not in _RESIZE_DIRECTIONS:
            raise ValueError("resize direction must be one of the eight compass directions")
        return self._dispatch("begin_geometry", direction)

    def update_geometry(self) -> bool:
        return self._dispatch("update_geometry")

    def end_geometry(self) -> bool:
        """Clear local gesture state without touching a possibly unfocused window."""
        with self._binding._gesture_lock:
            self._binding._gesture = None
        return True


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
    def begin_geometry(self, binding: _BackendBinding, direction: str | None) -> bool: return False
    def update_geometry(self, binding: _BackendBinding) -> bool: return False


# Xlib's Window and Atom typedefs are unsigned long, including on 64-bit X11.
_XDisplay = ctypes.c_void_p
_XWindow = ctypes.c_ulong
_XAtom = ctypes.c_ulong
_XBool = ctypes.c_int
_XStatus = ctypes.c_int


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
_X_NET_WM_STATE_TOGGLE = 2
_X_NET_WM_STATE_REMOVE = 0
_RESTORE_POLL_SECONDS = 0.02
_RESTORE_POLLS = 10


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
        x.XGetGeometry.argtypes, x.XGetGeometry.restype = [_XDisplay, _XWindow, ctypes.POINTER(_XWindow), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)], _XStatus
        x.XTranslateCoordinates.argtypes, x.XTranslateCoordinates.restype = [_XDisplay, _XWindow, _XWindow, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(_XWindow)], _XBool
        x.XQueryPointer.argtypes, x.XQueryPointer.restype = [_XDisplay, _XWindow, ctypes.POINTER(_XWindow), ctypes.POINTER(_XWindow), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint)], _XBool
        x.XMoveResizeWindow.argtypes, x.XMoveResizeWindow.restype = [_XDisplay, _XWindow, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint], ctypes.c_int
        x.XSendEvent.argtypes, x.XSendEvent.restype = [_XDisplay, _XWindow, _XBool, ctypes.c_long, ctypes.POINTER(_XEvent)], _XStatus
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

    def _atoms(self, x: Any, display: int, window: int, property_name: str) -> tuple[int, ...] | None:
        """Read the complete, bounded EWMH atom list without retaining Xlib memory."""
        expected_type = self._atom(x, display, "ATOM")
        property_atom = self._atom(x, display, property_name)
        values: list[int] = []
        offset = 0
        while len(values) < 4096:
            actual_type, actual_format = _XAtom(), ctypes.c_int()
            items, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
            status = x.XGetWindowProperty(
                display, window, property_atom, offset, 256, 0, expected_type,
                ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(items),
                ctypes.byref(after), ctypes.byref(data),
            )
            if not actual_type.value and not actual_format.value and not items.value and not after.value:
                if data.value:
                    x.XFree(data)
                return tuple(values)
            if status or actual_type.value != expected_type or actual_format.value != 32:
                if data.value:
                    x.XFree(data)
                return None
            try:
                if items.value:
                    values.extend(
                        int(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[index])
                        for index in range(int(items.value))
                    )
            finally:
                if data.value:
                    x.XFree(data)
            if not after.value:
                return tuple(values)
            if not items.value:
                return None
            offset += int(items.value)
        return None

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
        root_x, root_y = ctypes.c_int(), ctypes.c_int()
        window_x, window_y, mask = ctypes.c_int(), ctypes.c_int(), ctypes.c_uint()
        if not x.XQueryPointer(
            display, x.XDefaultRootWindow(display), ctypes.byref(root), ctypes.byref(child),
            ctypes.byref(root_x), ctypes.byref(root_y), ctypes.byref(window_x),
            ctypes.byref(window_y), ctypes.byref(mask),
        ):
            return None
        return root_x.value, root_y.value

    def _rect(self, x: Any, display: int, handle: int) -> _Rect | None:
        parent = _XWindow()
        relative_x, relative_y = ctypes.c_int(), ctypes.c_int()
        width, height, border, depth = (ctypes.c_uint() for _ in range(4))
        if not x.XGetGeometry(
            display, handle, ctypes.byref(parent), ctypes.byref(relative_x), ctypes.byref(relative_y),
            ctypes.byref(width), ctypes.byref(height), ctypes.byref(border), ctypes.byref(depth),
        ):
            return None
        root_x, root_y, child = ctypes.c_int(), ctypes.c_int(), _XWindow()
        if not x.XTranslateCoordinates(
            display, handle, x.XDefaultRootWindow(display), 0, 0,
            ctypes.byref(root_x), ctypes.byref(root_y), ctypes.byref(child),
        ):
            return None
        if width.value < _MIN_WINDOW_SIZE or height.value < _MIN_WINDOW_SIZE:
            return None
        return _Rect(root_x.value, root_y.value, int(width.value), int(height.value))

    def _is_maximized(self, x: Any, display: int, handle: int) -> bool | None:
        vertical = self._atom(x, display, "_NET_WM_STATE_MAXIMIZED_VERT")
        horizontal = self._atom(x, display, "_NET_WM_STATE_MAXIMIZED_HORZ")
        state = self._atoms(x, display, handle, "_NET_WM_STATE")
        if state is None:
            return None
        return vertical in state or horizontal in state

    def _restore_if_maximized(self, x: Any, display: int, binding: _BackendBinding) -> _Rect | None:
        vertical = self._atom(x, display, "_NET_WM_STATE_MAXIMIZED_VERT")
        horizontal = self._atom(x, display, "_NET_WM_STATE_MAXIMIZED_HORZ")
        maximized = self._is_maximized(x, display, binding.handle)
        if maximized is None:
            return None
        if not maximized:
            return self._rect(x, display, binding.handle)
        if not self._event(
            x, display, binding, "_NET_WM_STATE",
            (_X_NET_WM_STATE_REMOVE, vertical, horizontal, 1, 0),
        ):
            return None
        for _ in range(_RESTORE_POLLS):
            x.XSync(display, 0)
            if not self._valid(x, display, binding):
                return None
            maximized = self._is_maximized(x, display, binding.handle)
            rect = self._rect(x, display, binding.handle)
            if maximized is False and rect is not None:
                return rect
            time.sleep(_RESTORE_POLL_SECONDS)
        return None

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

    def begin_geometry(self, binding: _BackendBinding, direction: str | None) -> bool:
        def operation(x: Any, display: int) -> bool:
            pointer, rect = self._pointer(x, display), self._rect(x, display, binding.handle)
            maximized = self._is_maximized(x, display, binding.handle)
            if pointer is None or rect is None or maximized is None:
                return False
            binding._gesture = _Gesture(direction, pointer[0], pointer[1], rect, maximized)
            return True
        return self._action(binding, operation)

    def _restore_gesture_rect(self, binding: _BackendBinding) -> _Rect | None:
        self._u.ShowWindow(binding.handle, self._SW_RESTORE)
        for _ in range(_RESTORE_POLLS):
            if not self._valid(binding):
                return None
            if not self._u.IsZoomed(binding.handle):
                rect = self._rect(binding.handle)
                if rect is not None:
                    return rect
            time.sleep(_RESTORE_POLL_SECONDS)
        return None

    def update_geometry(self, binding: _BackendBinding) -> bool:
        def operation(x: Any, display: int) -> bool:
            gesture = binding._gesture
            pointer = self._pointer(x, display)
            if gesture is None or pointer is None:
                return False
            if pointer == (gesture.pointer_x, gesture.pointer_y):
                return True
            if gesture.maximized:
                restored = self._restore_if_maximized(x, display, binding)
                if restored is None:
                    return False
                gesture = replace(gesture, rect=restored, maximized=False)
                binding._gesture = gesture
            rect = _gesture_rect(gesture, pointer[0], pointer[1])
            x.XMoveResizeWindow(display, binding.handle, rect.left, rect.top, rect.width, rect.height)
            x.XFlush(display)
            return True
        return self._action(binding, operation)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int32), ("top", ctypes.c_int32),
        ("right", ctypes.c_int32), ("bottom", ctypes.c_int32),
    ]


class _WindowsBackend(_Backend):
    """Pointer-size-correct user32 adapter for the same opaque binding model."""

    _HWND, _LONG_PTR, _UINT, _WPARAM, _LPARAM, _BOOL = ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t, ctypes.c_int
    _GWL_STYLE, _WS_CAPTION = -16, 0x00C00000
    _SW_MINIMIZE, _SW_MAXIMIZE, _SW_RESTORE = 6, 3, 9
    _SWP_FRAMECHANGED = 0x0020
    _SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | _SWP_FRAMECHANGED
    _SWP_RESIZE_FLAGS = 0x0004 | 0x0010
    _WM_CLOSE = 0x0010

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
        u.GetCursorPos.argtypes, u.GetCursorPos.restype = [ctypes.POINTER(_POINT)], self._BOOL
        u.GetWindowRect.argtypes, u.GetWindowRect.restype = [self._HWND, ctypes.POINTER(_RECT)], self._BOOL
        u.GetWindowLongPtrW.argtypes, u.GetWindowLongPtrW.restype = [self._HWND, ctypes.c_int], self._LONG_PTR
        u.SetWindowLongPtrW.argtypes, u.SetWindowLongPtrW.restype = [self._HWND, ctypes.c_int, self._LONG_PTR], self._LONG_PTR
        u.SetWindowPos.argtypes, u.SetWindowPos.restype = [self._HWND, self._HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, self._UINT], self._BOOL
        u.ShowWindow.argtypes, u.ShowWindow.restype = [self._HWND, ctypes.c_int], self._BOOL
        u.IsZoomed.argtypes, u.IsZoomed.restype = [self._HWND], self._BOOL
        u.PostMessageW.argtypes, u.PostMessageW.restype = [self._HWND, self._UINT, self._WPARAM, self._LPARAM], self._BOOL

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

    def _pointer(self) -> tuple[int, int] | None:
        point = _POINT()
        return (int(point.x), int(point.y)) if self._u.GetCursorPos(ctypes.byref(point)) else None

    def _rect(self, handle: int) -> _Rect | None:
        rectangle = _RECT()
        if not self._u.GetWindowRect(handle, ctypes.byref(rectangle)):
            return None
        width, height = int(rectangle.right - rectangle.left), int(rectangle.bottom - rectangle.top)
        if width < _MIN_WINDOW_SIZE or height < _MIN_WINDOW_SIZE:
            return None
        return _Rect(int(rectangle.left), int(rectangle.top), width, height)

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

    def begin_geometry(self, binding: _BackendBinding, direction: str | None) -> bool:
        def operation() -> bool:
            pointer, rect = self._pointer(), self._rect(binding.handle)
            if pointer is None or rect is None:
                return False
            binding._gesture = _Gesture(
                direction, pointer[0], pointer[1], rect, bool(self._u.IsZoomed(binding.handle)),
            )
            return True
        return self._action(binding, operation)

    def update_geometry(self, binding: _BackendBinding) -> bool:
        def operation() -> bool:
            gesture, pointer = binding._gesture, self._pointer()
            if gesture is None or pointer is None:
                return False
            if pointer == (gesture.pointer_x, gesture.pointer_y):
                return True
            if gesture.maximized:
                restored = self._restore_gesture_rect(binding)
                if restored is None:
                    return False
                gesture = replace(gesture, rect=restored, maximized=False)
                binding._gesture = gesture
            rect = _gesture_rect(gesture, pointer[0], pointer[1])
            return bool(self._u.SetWindowPos(
                binding.handle, None, rect.left, rect.top, rect.width, rect.height,
                self._SWP_RESIZE_FLAGS,
            ))
        return self._action(binding, operation)
