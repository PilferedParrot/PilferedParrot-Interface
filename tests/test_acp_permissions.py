"""Permission decisions are scoped, bounded, and default-deny."""

from __future__ import annotations

import json
import queue
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from pilferedparrot.acp_permissions import PermissionBroker


def permission() -> dict:
    return {
        "sessionId": "session-1",
        "toolCall": {"toolCallId": "tool-1", "title": "Write a file"},
        "options": [
            {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
            {"optionId": "deny", "name": "Reject once", "kind": "reject_once"},
        ],
    }


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.value += seconds


class PermissionBrokerTests(unittest.TestCase):
    def test_allow_requires_explicit_offered_choice(self):
        broker = PermissionBroker(timeout_seconds=2)
        seen = queue.Queue()
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(broker.request, "chat-a", permission(), None, seen.put)
            pending = seen.get(timeout=1)
            self.assertFalse(broker.decide("chat-a", pending["requestId"], "invented"))
            self.assertFalse(result.done())
            self.assertTrue(broker.decide("chat-a", pending["requestId"], "allow"))
            self.assertEqual(result.result(timeout=1), "allow")
            self.assertFalse(broker.decide("chat-a", pending["requestId"], "deny"))

    def test_invalid_requests_full_broker_and_callback_failure_deny(self):
        broker = PermissionBroker(timeout_seconds=2, max_pending=1)
        seen = queue.Queue()
        for malformed in ({}, {**permission(), "sessionId": 1},
                          {**permission(), "options": []},
                          {**permission(), "options": [{"optionId": "yes"}]},
                          {**permission(), "options": [permission()["options"][0]] * 2}):
            self.assertIsNone(broker.request("chat-a", malformed, None, seen.put))
        self.assertIsNone(broker.request("", permission(), None, seen.put))
        self.assertTrue(seen.empty())
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(broker.request, "chat-a", permission(), None, seen.put)
            pending = seen.get(timeout=1)
            self.assertIsNone(broker.request("chat-b", permission(), None, seen.put))
            self.assertEqual(len(broker.pending_for_chat("chat-a")), 1)
            self.assertTrue(broker.decide("chat-a", pending["requestId"], "deny"))
            self.assertEqual(active.result(timeout=1), "deny")
        def fail(_pending):
            raise RuntimeError("publisher failed")
        self.assertIsNone(broker.request("chat-a", permission(), None, fail))
        self.assertEqual(broker.pending_for_chat("chat-a"), [])

    def test_cross_chat_duplicate_and_late_choices_rejected(self):
        clock = FakeClock()
        broker = PermissionBroker(timeout_seconds=10, clock=clock, poll_interval=0.001)
        seen = queue.Queue()
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(broker.request, "chat-a", permission(), None, seen.put)
            pending = seen.get(timeout=1)
            request_id = pending["requestId"]
            self.assertEqual(broker.pending_for_chat("chat-b"), [])
            self.assertFalse(broker.decide("chat-b", request_id, "allow"))
            self.assertFalse(broker.decide("chat-a", "unknown", "allow"))
            self.assertTrue(broker.decide("chat-a", request_id, "deny"))
            self.assertFalse(broker.decide("chat-a", request_id, "allow"))
            self.assertEqual(active.result(timeout=1), "deny")
            late = pool.submit(broker.request, "chat-a", permission(), None, seen.put)
            late_id = seen.get(timeout=1)["requestId"]
            self.assertNotEqual(request_id, late_id)
            clock.advance(11)
            self.assertFalse(broker.decide("chat-a", late_id, "allow"))
            self.assertIsNone(late.result(timeout=1))
            self.assertFalse(broker.decide("chat-a", late_id, "deny"))

    def test_timeout_and_cancellation_default_deny(self):
        clock = FakeClock()
        broker = PermissionBroker(timeout_seconds=5, clock=clock, poll_interval=0.001)
        seen = []
        def expire(pending):
            seen.append(pending)
            clock.advance(5)
        self.assertIsNone(broker.request("chat-a", permission(), None, expire))
        self.assertFalse(broker.decide("chat-a", seen[0]["requestId"], "allow"))
        cancel = threading.Event()
        published = queue.Queue()
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(broker.request, "chat-a", permission(), cancel, published.put)
            request_id = published.get(timeout=1)["requestId"]
            cancel.set()
            self.assertFalse(broker.decide("chat-a", request_id, "allow"))
            self.assertIsNone(active.result(timeout=1))
        cancel_before = threading.Event()
        cancel_before.set()
        self.assertIsNone(broker.request("chat-a", permission(), cancel_before, published.put))

    def test_choice_accepted_before_deadline_survives_wake_delay(self):
        clock = FakeClock()
        broker = PermissionBroker(timeout_seconds=5, clock=clock)
        def decide_then_advance(pending):
            self.assertTrue(broker.decide("chat-a", pending["requestId"], "allow"))
            clock.advance(6)
        self.assertEqual(broker.request("chat-a", permission(), None, decide_then_advance),
                         "allow")

    def test_concurrent_chats_and_shutdown(self):
        broker = PermissionBroker(timeout_seconds=2, max_pending=3)
        seen = queue.Queue()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = [pool.submit(broker.request, f"chat-{i}", permission(), None, seen.put)
                       for i in range(3)]
            pending = [seen.get(timeout=1) for _ in range(3)]
            ids = {item["requestId"] for item in pending}
            self.assertEqual(len(ids), 3)
            self.assertTrue(all(len(request_id) >= 24 for request_id in ids))
            broker.shutdown()
            self.assertEqual([future.result(timeout=1) for future in results], [None] * 3)
            self.assertFalse(any(broker.decide("chat-0", request_id, "allow")
                                 for request_id in ids))
            self.assertIsNone(broker.request("chat-new", permission(), None, seen.put))
            self.assertEqual(broker.pending_for_chat("chat-0"), [])

    def test_public_snapshot_omits_private_fields(self):
        broker = PermissionBroker(timeout_seconds=2)
        incoming = permission()
        incoming["accountEmail"] = "private@example.test"
        incoming["secret"] = "top-secret"
        incoming["toolCall"].update({"title": "Send to private@example.test",
                                     "account": {"email": "private@example.test"},
                                     "_meta": {"token": "top-secret"}})
        incoming["options"][0].update({"name": "Allow private@example.test",
                                       "credentials": "top-secret"})
        seen = queue.Queue()
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(broker.request, "chat-a", incoming, None, seen.put)
            public = seen.get(timeout=1)
            serialized = json.dumps(public)
            self.assertNotIn("private@example.test", serialized)
            self.assertNotIn("top-secret", serialized)
            self.assertNotIn("account", serialized)
            self.assertNotIn("credentials", serialized)
            self.assertNotIn("token", serialized)
            self.assertNotIn("sessionId", serialized)
            self.assertEqual(set(public), {"requestId", "toolCall", "options"})
            public["options"][0]["optionId"] = "tampered"
            self.assertEqual(broker.pending_for_chat("chat-a")[0]["options"][0]["optionId"],
                             "allow")
            self.assertTrue(broker.decide("chat-a", public["requestId"], "allow"))
            self.assertEqual(active.result(timeout=1), "allow")

    def test_diff_preview_is_complete_and_sanitized(self):
        broker = PermissionBroker(timeout_seconds=2)
        incoming = permission()
        incoming["toolCall"].update({
            "name": "Edit", "kind": "edit",
            "rawInput": {"file_path": "src/app.py", "token": "hidden-token"},
            "content": [
                {"type": "diff", "path": "src/app.py",
                 "oldText": "owner = 'private@example.test'\n",
                 "newText": "owner = 'new@example.test'\n",
                 "account": {"email": "private@example.test"},
                 "_meta": {"secret": "hidden-token"}},
            ],
        })
        published = queue.Queue()
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(broker.request, "chat-a", incoming, None, published.put)
            public = published.get(timeout=1)
            tool = public["toolCall"]
            self.assertEqual(tool["name"], "Edit")
            self.assertEqual(tool["kind"], "edit")
            self.assertEqual(tool["content"], [{
                "type": "diff", "path": "src/app.py",
                "oldText": "owner = '[redacted-email]'\n",
                "newText": "owner = '[redacted-email]'\n",
            }])
            self.assertNotIn("hidden-token", json.dumps(public))
            self.assertNotIn("account", json.dumps(public))
            self.assertTrue(broker.decide("chat-a", public["requestId"], "allow"))
            self.assertEqual(active.result(timeout=1), "allow")

    def test_shell_command_preview_omits_raw_input_metadata(self):
        broker = PermissionBroker(timeout_seconds=2)
        incoming = permission()
        incoming["toolCall"].update({
            "name": "functions.execute", "kind": "execute",
            "rawInput": {"command": "echo hello && cat app.py",
                         "accountEmail": "private@example.test",
                         "_meta": {"accessToken": "hidden-token"}},
        })
        published = queue.Queue()
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(broker.request, "chat-a", incoming, None, published.put)
            public = published.get(timeout=1)
            self.assertEqual(public["toolCall"]["command"], "echo hello && cat app.py")
            self.assertNotIn("rawInput", json.dumps(public))
            self.assertNotIn("hidden-token", json.dumps(public))
            self.assertNotIn("private@example.test", json.dumps(public))
            self.assertTrue(broker.decide("chat-a", public["requestId"], "deny"))
            self.assertEqual(active.result(timeout=1), "deny")

    def test_new_file_diff_preserves_null_old_text(self):
        broker = PermissionBroker(timeout_seconds=2)
        incoming = permission()
        incoming["toolCall"].update({
            "name": None, "kind": "edit",
            "content": [{"type": "diff", "path": "new.txt",
                         "oldText": None, "newText": "new file\n"}],
        })
        published = queue.Queue()
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(broker.request, "chat-a", incoming, None, published.put)
            public = published.get(timeout=1)
            self.assertEqual(public["toolCall"]["content"], [{
                "type": "diff", "path": "new.txt", "oldText": None,
                "newText": "new file\n",
            }])
            self.assertTrue(broker.decide("chat-a", public["requestId"], "deny"))
            self.assertEqual(active.result(timeout=1), "deny")

    def test_oversized_or_incomplete_review_material_default_denies(self):
        broker = PermissionBroker(timeout_seconds=2, max_preview_bytes=512)
        published = queue.Queue()
        incoming = permission()
        incoming["toolCall"]["content"] = [{
            "type": "diff", "path": "src/app.py", "oldText": "before",
            "newText": "x" * 1000,
        }]
        self.assertIsNone(broker.request("chat-a", incoming, None, published.put))
        self.assertTrue(published.empty())
        incoming = permission()
        incoming["toolCall"].update({"name": "shell", "rawInput": {"command": "x" * 1000}})
        self.assertIsNone(broker.request("chat-a", incoming, None, published.put))
        self.assertTrue(published.empty())
        incoming["toolCall"]["rawInput"] = {"description": "command hidden"}
        self.assertIsNone(broker.request("chat-a", incoming, None, published.put))
        incoming = permission()
        incoming["toolCall"]["content"] = [{"type": "diff", "path": "src/app.py"}]
        self.assertIsNone(broker.request("chat-a", incoming, None, published.put))
        incoming = permission()
        incoming["toolCall"].update({"kind": "edit", "content": []})
        self.assertIsNone(broker.request("chat-a", incoming, None, published.put))
        incoming = permission()
        incoming["toolCall"].update({"kind": "execute", "name": "Run"})
        self.assertIsNone(broker.request("chat-a", incoming, None, published.put))
        incoming = permission()
        incoming["toolCall"]["title"] = "x" * 600 + "@example.test"
        self.assertIsNone(broker.request("chat-a", incoming, None, published.put))
        incoming = permission()
        incoming["options"][0]["optionId"] = "private@example.test"
        self.assertIsNone(broker.request("chat-a", incoming, None, published.put))
        self.assertTrue(published.empty())


if __name__ == "__main__":
    unittest.main()
