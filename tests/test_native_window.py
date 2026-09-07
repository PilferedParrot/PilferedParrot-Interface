from __future__ import annotations

import ctypes
import unittest

from pilferedparrot.native_window import (
    BoundNativeWindow,
    NativeWindowAdapter,
    NativeWindowError,
    _Backend,
    _BackendBinding,
    _UnsupportedBackend,
    _XClientMessageEvent,
    _XEvent,
    _X_MOVERESIZE_DIRECTIONS,
    _is_allowed,
    _is_marker,
)


MARKER = "PilferedParrot Native 0123456789abcdef0123456789abcdef"


class _FakeBackend(_Backend):
    def __init__(self, *, valid=True, fail=False):
        self.valid = valid
        self.fail = fail
        self.calls: list[tuple[object, ...]] = []
        self.handle = object()

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
    def drag(self, binding): return self._action("drag", binding)
    def resize(self, binding, direction): return self._action("resize", binding, direction)


class NativeWindowTests(unittest.TestCase):
    def test_binding_requires_an_exact_nonce_marker(self):
        backend = _FakeBackend()
        self.assertIsNone(NativeWindowAdapter(backend).bind_active_window("PilferedParrot"))
        self.assertEqual(backend.calls, [])
        self.assertIsNotNone(NativeWindowAdapter(backend).bind_active_window(MARKER))
        self.assertEqual(backend.calls, [("bind", MARKER)])

    def test_bound_actions_are_opaque_and_accept_no_handle_or_pid(self):
        backend = _FakeBackend()
        window = NativeWindowAdapter(backend).bind_active_window(MARKER)
        self.assertIsInstance(window, BoundNativeWindow)
        self.assertTrue(window.minimize())
        self.assertTrue(window.maximize_or_restore())
        self.assertTrue(window.close())
        self.assertTrue(window.drag())
        self.assertTrue(window.resize("northwest"))
        self.assertEqual([call[0] for call in backend.calls], ["bind", "minimize", "maximize", "close", "drag", "resize"])

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

    def test_resize_data_is_an_allowlist(self):
        window = NativeWindowAdapter(_FakeBackend()).bind_active_window(MARKER)
        self.assertIsNotNone(window)
        for direction in _X_MOVERESIZE_DIRECTIONS:
            self.assertTrue(window.resize(direction))
        with self.assertRaises(ValueError):
            window.resize("diagonal")

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

    def test_ewmh_resize_direction_values_match_the_specification(self):
        self.assertEqual(_X_MOVERESIZE_DIRECTIONS, {
            "northwest": 0, "north": 1, "northeast": 2, "east": 3,
            "southeast": 4, "south": 5, "southwest": 6, "west": 7,
        })


if __name__ == "__main__":
    unittest.main()
