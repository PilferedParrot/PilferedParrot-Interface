from __future__ import annotations

import ctypes
from types import SimpleNamespace
import unittest

from pilferedparrot.native_window import (
    BoundNativeWindow,
    NativeWindowAdapter,
    NativeWindowError,
    _Backend,
    _BackendBinding,
    _Gesture,
    _Rect,
    _UnsupportedBackend,
    _MIN_WINDOW_SIZE,
    _POINT,
    _RECT,
    _RESIZE_DIRECTIONS,
    _XClientMessageEvent,
    _X11Backend,
    _XDisplay,
    _XEvent,
    _X_NET_WM_STATE_REMOVE,
    _RESTORE_POLLS,
    _XWindow,
    _gesture_rect,
    _is_allowed,
    _is_marker,
)


MARKER = "PilferedParrot Native 0123456789abcdef0123456789abcdef"


class _CFunction:
    """Enough of a ctypes function object to inspect configured ABI metadata."""


def _xlib_functions():
    names = (
        "XOpenDisplay", "XDefaultRootWindow", "XDefaultScreen", "XInternAtom",
        "XGetWindowProperty", "XFetchName", "XGetClassHint", "XFree",
        "XChangeProperty", "XIconifyWindow", "XGetGeometry", "XTranslateCoordinates",
        "XQueryPointer", "XMoveResizeWindow",
        "XSendEvent", "XFlush", "XSync", "XSetErrorHandler",
    )
    return SimpleNamespace(**{name: _CFunction() for name in names})


class _FakeBackend(_Backend):
    def __init__(self, *, valid=True, fail=False):
        self.valid = valid
        self.fail = fail
        self.calls: list[tuple[object, ...]] = []
        self.handle = object()
        self.pointer = (100, 200)
        self.rect = _Rect(10, 20, 300, 200)
        self.restored_rect = _Rect(80, 90, 700, 500)
        self.maximized = False

    def bind(self, expected_title):
        self.calls.append(("bind", expected_title))
        if not self.valid or expected_title != MARKER:
            return None
        return _BackendBinding(self, self.handle, expected_title, "Chrome_WidgetWin_1", 42)

    def _action(self, name, binding, *data):
        if not self.valid:
            return False
        if self.fail:
            raise NativeWindowError("simulated native failure")
        self.calls.append((name, binding, *data))
        return True

    def minimize(self, binding): return self._action("minimize", binding)
    def maximize_or_restore(self, binding): return self._action("maximize", binding)
    def close(self, binding): return self._action("close", binding)
    def begin_geometry(self, binding, direction):
        def begin():
            rect = self.rect
            binding._gesture = _Gesture(direction, *self.pointer, rect, self.maximized)
            self.calls.append(("capture", binding, self.pointer, rect))
            return True
        return self._action("begin_geometry", binding, direction) and begin()

    def update_geometry(self, binding):
        def update():
            if binding._gesture is None:
                return False
            gesture = binding._gesture
            if self.pointer == (gesture.pointer_x, gesture.pointer_y):
                return True
            if gesture.maximized:
                self.calls.append(("restore", binding))
                self.maximized = False
                gesture = _Gesture(
                    gesture.direction, gesture.pointer_x, gesture.pointer_y,
                    self.restored_rect, False,
                )
                binding._gesture = gesture
            self.calls.append(("applied", _gesture_rect(gesture, *self.pointer)))
            return True
        return self._action("update_geometry", binding) and update()


class NativeWindowTests(unittest.TestCase):
    def test_binding_requires_an_exact_nonce_marker(self):
        backend = _FakeBackend()
        self.assertIsNone(NativeWindowAdapter(backend).bind_active_window("PilferedParrot"))
        self.assertEqual(backend.calls, [])
        self.assertIsNotNone(NativeWindowAdapter(backend).bind_active_window(MARKER))
        self.assertEqual(backend.calls, [("bind", MARKER)])

    def test_bound_actions_dispatch_native_gesture_without_handle_or_pid(self):
        backend = _FakeBackend()
        window = NativeWindowAdapter(backend).bind_active_window(MARKER)
        self.assertIsInstance(window, BoundNativeWindow)
        self.assertTrue(window.minimize())
        self.assertTrue(window.maximize_or_restore())
        self.assertTrue(window.close())
        self.assertTrue(window.begin_move())
        backend.pointer = (130, 245)
        self.assertTrue(window.update_geometry())
        self.assertTrue(window.end_geometry())
        self.assertEqual([call[0] for call in backend.calls], ["bind", "minimize", "maximize", "close", "begin_geometry", "capture", "update_geometry", "applied"])
        self.assertEqual(backend.calls[-1][1], _Rect(40, 65, 300, 200))

    def test_begin_end_on_maximized_window_does_not_restore(self):
        backend = _FakeBackend()
        backend.maximized = True
        window = NativeWindowAdapter(backend).bind_active_window(MARKER)
        self.assertIsNotNone(window)
        self.assertTrue(window.begin_resize("southwest"))
        self.assertTrue(window._binding._gesture.maximized)
        self.assertTrue(window.end_geometry())
        self.assertTrue(backend.maximized)
        self.assertNotIn("restore", [call[0] for call in backend.calls])

    def test_first_maximized_drag_update_restores_once_and_uses_original_delta(self):
        backend = _FakeBackend()
        backend.maximized = True
        window = NativeWindowAdapter(backend).bind_active_window(MARKER)
        self.assertIsNotNone(window)
        self.assertTrue(window.begin_move())
        self.assertTrue(window.update_geometry())  # Zero delta must preserve maximization.
        self.assertTrue(backend.maximized)
        backend.pointer = (130, 245)
        self.assertTrue(window.update_geometry())
        self.assertFalse(backend.maximized)
        self.assertEqual(backend.calls[-1][1], _Rect(110, 135, 700, 500))
        backend.pointer = (150, 250)
        self.assertTrue(window.update_geometry())
        self.assertEqual([call[0] for call in backend.calls].count("restore"), 1)
        self.assertEqual(backend.calls[-1][1], _Rect(130, 140, 700, 500))

    def test_stale_maximized_gesture_cannot_trigger_restore(self):
        backend = _FakeBackend()
        backend.maximized = True
        window = NativeWindowAdapter(backend).bind_active_window(MARKER)
        self.assertIsNotNone(window)
        self.assertTrue(window.begin_move())
        backend.pointer = (130, 245)
        backend.valid = False
        self.assertFalse(window.update_geometry())
        self.assertTrue(backend.maximized)
        self.assertNotIn("restore", [call[0] for call in backend.calls])

    def test_stale_or_reused_binding_is_rejected_before_action(self):
        backend = _FakeBackend()
        window = NativeWindowAdapter(backend).bind_active_window(MARKER)
        self.assertIsNotNone(window)
        backend.valid = False  # Models changed title/class/process after bind.
        self.assertFalse(window.close())
        self.assertEqual(backend.calls, [("bind", MARKER)])

    def test_native_failures_are_normalized_to_false(self):
        window = NativeWindowAdapter(_FakeBackend(fail=True)).bind_active_window(MARKER)
        self.assertIsNotNone(window)
        self.assertFalse(window.minimize())

    def test_begin_update_end_and_stale_gesture_validation(self):
        backend = _FakeBackend()
        window = NativeWindowAdapter(backend).bind_active_window(MARKER)
        self.assertIsNotNone(window)
        self.assertTrue(window.begin_resize("east"))
        backend.pointer = (150, 200)
        self.assertTrue(window.update_geometry())
        self.assertEqual(backend.calls[-1][1], _Rect(10, 20, 350, 200))
        backend.valid = False
        self.assertFalse(window.update_geometry())  # Native backend rejects stale identity.
        self.assertTrue(window.end_geometry())  # Clearing local state is safe after focus loss.
        backend.valid = True
        self.assertFalse(window.update_geometry())

    def test_resize_direction_is_an_allowlist(self):
        window = NativeWindowAdapter(_FakeBackend()).bind_active_window(MARKER)
        self.assertIsNotNone(window)
        for direction in _RESIZE_DIRECTIONS:
            self.assertTrue(window.begin_resize(direction))
            self.assertTrue(window.end_geometry())
        with self.assertRaises(ValueError):
            window.begin_resize("diagonal")

    def test_all_resize_directions_keep_the_opposite_edge_anchored(self):
        rect = _Rect(100, 200, 300, 200)
        start = (10, 20)
        current = (40, -30)  # physical delta: +30, -50
        expected = {
            "north": _Rect(100, 150, 300, 250),
            "south": _Rect(100, 200, 300, 150),
            "east": _Rect(100, 200, 330, 200),
            "west": _Rect(130, 200, 270, 200),
            "northeast": _Rect(100, 150, 330, 250),
            "northwest": _Rect(130, 150, 270, 250),
            "southeast": _Rect(100, 200, 330, 150),
            "southwest": _Rect(130, 200, 270, 150),
        }
        for direction, result in expected.items():
            with self.subTest(direction=direction):
                self.assertEqual(_gesture_rect(_Gesture(direction, *start, rect), *current), result)

    def test_native_physical_pointer_delta_has_no_js_dpi_conversion(self):
        rect = _Rect(200, 400, 600, 300)
        gesture = _Gesture(None, 1_000, 800, rect)
        # These are already physical pixels from GetCursorPos/XQueryPointer.
        self.assertEqual(_gesture_rect(gesture, 1_040, 820), _Rect(240, 420, 600, 300))
        self.assertEqual(
            _gesture_rect(_Gesture("west", 1_000, 800, rect), 2_000, 800),
            _Rect(736, 400, _MIN_WINDOW_SIZE, 300),
        )

    def test_identity_requires_title_marker_and_chromium_class(self):
        self.assertTrue(_is_marker(MARKER))
        self.assertTrue(_is_allowed(MARKER, "Chrome_WidgetWin_1"))
        self.assertTrue(_is_allowed(MARKER, "chromium"))
        self.assertTrue(_is_allowed(MARKER, "pilferedparrot-chat"))
        self.assertFalse(_is_allowed(MARKER, "Firefox"))
        self.assertFalse(_is_allowed("PilferedParrot Native " + "a" * 31, "Chrome_WidgetWin_1"))
        self.assertFalse(_is_allowed("Other Native " + "a" * 32, "Chrome_WidgetWin_1"))

    def test_unsupported_platform_is_cleanly_unavailable(self):
        self.assertIsNone(NativeWindowAdapter(_UnsupportedBackend()).bind_active_window(MARKER))

    def test_xevent_uses_the_full_xlib_union_storage(self):
        self.assertEqual(ctypes.sizeof(_XEvent), 24 * ctypes.sizeof(ctypes.c_long))
        self.assertGreaterEqual(ctypes.sizeof(_XEvent), ctypes.sizeof(_XClientMessageEvent))
        # These offsets catch accidental 32-bit pointer declarations on LP64.
        if ctypes.sizeof(ctypes.c_void_p) == 8 and ctypes.sizeof(ctypes.c_long) == 8:
            self.assertEqual(_XClientMessageEvent.window.offset, 32)
            self.assertEqual(_XClientMessageEvent.message_type.offset, 40)
            self.assertEqual(_XClientMessageEvent.data.offset, 56)

    def test_x11_geometry_and_pointer_abi_uses_native_coordinate_types(self):
        xlib = _xlib_functions()
        _X11Backend()._configure(xlib)
        self.assertEqual(xlib.XGetGeometry.argtypes[:2], [_XDisplay, _XWindow])
        self.assertEqual(xlib.XTranslateCoordinates.argtypes[:3], [_XDisplay, _XWindow, _XWindow])
        self.assertEqual(xlib.XQueryPointer.argtypes[:2], [_XDisplay, _XWindow])
        self.assertEqual(xlib.XMoveResizeWindow.argtypes, [
            _XDisplay, _XWindow, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
        ])

    def test_x11_restore_protocol_uses_evmh_remove_and_a_bounded_wait(self):
        self.assertEqual(_X_NET_WM_STATE_REMOVE, 0)
        self.assertGreater(_RESTORE_POLLS, 0)
        self.assertLessEqual(_RESTORE_POLLS, 20)

    def test_win32_pointer_and_rectangle_are_fixed_width_native_structures(self):
        self.assertEqual(ctypes.sizeof(_POINT), 8)
        self.assertEqual(ctypes.sizeof(_RECT), 16)

if __name__ == "__main__":
    unittest.main()
