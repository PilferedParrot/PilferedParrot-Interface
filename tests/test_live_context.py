"""Context telemetry advances during a request without submitting another one."""

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.config import load_config
from pilferedparrot.context_telemetry import CodexUsageReader
from pilferedparrot.dispatch import (
    RunCancelled, RunResult, _codex_session_live_usage, capture_codex,
)
from pilferedparrot.model import Conversation, ProviderBudget
from tests.test_context_usage import _app, _wait_for_idle


SESSION = "01a00000-0000-7000-8000-000000000001"


def record(used, timestamp="2026-09-14T20:00:00Z"):
    return {"type": "event_msg", "timestamp": timestamp, "payload": {
        "type": "token_count", "info": {
            "last_token_usage": {"input_tokens": used, "output_tokens": 20},
            "model_context_window": 128000,
        },
    }}


class LiveContextTests(unittest.TestCase):
    def test_resumed_capture_keeps_previous_reading_until_valid_usage_arrives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sessions").mkdir()
            path = root / "sessions" / f"rollout-{SESSION}.jsonl"
            previous = record(100292)
            empty = record(0, "2026-09-14T20:00:01Z")
            empty["payload"]["info"]["last_token_usage"] = {
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            }
            compacted = record(0, "2026-09-14T20:00:02Z")
            compacted["payload"]["info"]["last_token_usage"] = {
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 16453,
            }
            path.write_text(json.dumps(previous) + "\n", encoding="utf-8")
            updates = []
            cancel = threading.Event()
            cancel._pilferedparrot_usage = updates.append

            def stream(command, prompt, cwd, *, stdout_line, on_tick, **kwargs):
                # Resuming a quiet provider should immediately retain the last
                # request's occupancy, even before this turn reports usage.
                on_tick()
                self.assertEqual(updates[-1]["input_tokens"], 100292)
                stdout_line(json.dumps(empty))
                self.assertEqual(len(updates), 1)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(empty) + "\n")
                on_tick()
                self.assertEqual(len(updates), 1)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(compacted) + "\n")
                on_tick()
                self.assertEqual(updates[-1]["input_tokens"], 16453)
                self.assertEqual(updates[-1]["output_tokens"], 0)
                stdout_line(json.dumps({"type": "turn.completed", "usage": {
                    "input_tokens": 900000, "output_tokens": 12000,
                }}))
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.dict("os.environ", {"CODEX_HOME": directory}), \
                    patch("pilferedparrot.dispatch._codex_command", return_value=["codex"]), \
                    patch("pilferedparrot.dispatch._stream_process", side_effect=stream):
                result = capture_codex(
                    "follow-up", root, Conversation(provider_session_id=SESSION),
                    load_config(root / "missing.json"), cancel,
                )
            self.assertEqual(result.live_input_tokens, 16453)
            self.assertEqual(result.input_tokens, 900000)
            self.assertEqual(len(updates), 2)

    def test_compaction_total_is_used_by_live_and_final_log_readers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sessions").mkdir()
            path = root / "sessions" / f"rollout-{SESSION}.jsonl"
            compacted = record(0)
            compacted["payload"]["info"]["last_token_usage"] = {
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 16453,
            }
            path.write_text(json.dumps(compacted) + "\n", encoding="utf-8")
            self.assertEqual(CodexUsageReader(root).read(SESSION), compacted)
            with patch.dict("os.environ", {"CODEX_HOME": directory}):
                self.assertEqual(
                    _codex_session_live_usage(SESSION, load_config(root / "missing.json")),
                    (16453, 0, 128000),
                )

    def test_empty_or_invalid_usage_does_not_hide_previous_log_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sessions").mkdir()
            path = root / "sessions" / f"rollout-{SESSION}.jsonl"
            previous = record(10000)
            for total in (None, 0, -1, True, "16453", 1.5):
                with self.subTest(total=total):
                    empty = record(0)
                    empty["payload"]["info"]["last_token_usage"] = {
                        "input_tokens": 0, "output_tokens": 0, "total_tokens": total,
                    }
                    path.write_text(
                        json.dumps(previous) + "\n" + json.dumps(empty) + "\n",
                        encoding="utf-8",
                    )
                    self.assertEqual(CodexUsageReader(root).read(SESSION), previous)
                    with patch.dict("os.environ", {"CODEX_HOME": directory}):
                        self.assertEqual(
                            _codex_session_live_usage(SESSION, load_config(root / "missing.json")),
                            (10000, 20, 128000),
                        )

    def test_usage_advances_while_provider_stdout_is_quiet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sessions").mkdir()
            path = root / "sessions" / f"rollout-{SESSION}.jsonl"
            script = (
                "import sys,time; from pathlib import Path; "
                f"print({json.dumps({'type': 'thread.started', 'thread_id': SESSION})!r}, flush=True); "
                f"Path(sys.argv[1]).write_text({(json.dumps(record(32000)) + chr(10))!r}, encoding='utf-8'); "
                "time.sleep(1.5)"
            )
            cancel = threading.Event()
            observations = []
            cancel._pilferedparrot_usage = lambda usage: observations.append((time.monotonic(), usage))
            with patch.dict("os.environ", {"CODEX_HOME": directory}), \
                    patch("pilferedparrot.dispatch._codex_command", return_value=[
                        sys.executable, "-c", script, str(path),
                    ]):
                result = capture_codex("one request", root, Conversation(), load_config(), cancel)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(observations[0][1]["input_tokens"], 32000)
            self.assertGreater(time.monotonic() - observations[0][0], 0.1)

    def test_log_reader_handles_append_partial_records_compaction_and_session_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sessions").mkdir()
            path = root / "sessions" / f"rollout-{SESSION}.jsonl"
            path.write_text(json.dumps(record(10000)) + "\n", encoding="utf-8")
            reader = CodexUsageReader(root)
            self.assertEqual(reader.read(SESSION), record(10000))
            with patch.object(Path, "open", side_effect=AssertionError("unchanged log reopened")):
                self.assertIsNone(reader.read(SESSION))
            next_line = json.dumps(record(5000))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(next_line[:60])
            self.assertIsNone(reader.read(SESSION))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(next_line[60:] + "\n")
            self.assertEqual(reader.read(SESSION), record(5000))
            path.write_text(json.dumps(record(100)) + "\n", encoding="utf-8")
            self.assertEqual(reader.read(SESSION), record(100))
            self.assertIsNone(reader.read(SESSION + "2"))
            self.assertIsNone(reader.read("../unsafe"))

    def test_log_rotation_and_malformed_trailing_usage_do_not_lose_valid_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sessions").mkdir()
            path = root / "sessions" / f"first-{SESSION}.jsonl"
            path.write_text(json.dumps(record(10000)) + "\n", encoding="utf-8")
            reader = CodexUsageReader(root)
            self.assertEqual(reader.read(SESSION), record(10000))
            rotated = root / "sessions" / f"new-{SESSION}.jsonl"
            rotated.write_text(json.dumps(record(1234)) + "\n" + json.dumps({
                "type": "token_count", "info": {},
            }) + "\n", encoding="utf-8")
            reader.next_discovery = 0
            self.assertEqual(reader.read(SESSION), record(1234))

    def test_capture_reads_live_file_and_ignores_aggregate_turn_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sessions").mkdir()
            path = root / "sessions" / f"rollout-{SESSION}.jsonl"
            config = load_config(root / "missing.json")
            config["codex"]["config_path"] = str(root / "config.toml")
            updates = []
            cancel = threading.Event()
            cancel._pilferedparrot_usage = updates.append

            def stream(command, prompt, cwd, *, stdout_line, on_tick, **kwargs):
                stdout_line(json.dumps({"type": "thread.started", "thread_id": SESSION}))
                path.write_text(json.dumps(record(42000)) + "\n", encoding="utf-8")
                on_tick()
                self.assertEqual(updates[-1]["input_tokens"], 42000)
                self.assertEqual(updates[-1]["observed_at"], 1789416000)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record(8000, "2026-09-14T20:00:01Z")) + "\n")
                on_tick()
                self.assertEqual(updates[-1]["input_tokens"], 8000)
                stdout_line(json.dumps({"type": "turn.completed", "usage": {
                    "input_tokens": 900000, "output_tokens": 12000,
                }}))
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.dict("os.environ", {"CODEX_HOME": directory}), \
                    patch("pilferedparrot.dispatch._codex_command", return_value=["codex"]), \
                    patch("pilferedparrot.dispatch._stream_process", side_effect=stream) as run:
                result = capture_codex("one request", root, Conversation(), config, cancel)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(result.live_input_tokens, 8000)
            self.assertEqual(result.input_tokens, 900000)
            self.assertEqual(len(updates), 2)

    def test_both_views_publish_live_counts_before_completion_and_preserve_on_cancel(self):
        for chat_mode in (False, True):
            with self.subTest(chat_mode=chat_mode), tempfile.TemporaryDirectory() as directory:
                app = _app(directory)
                emitted, release = threading.Event(), threading.Event()

                def dispatch(provider, prompt, cwd, conversation, config, cancel):
                    callback = cancel._pilferedparrot_usage
                    callback({"input_tokens": 64000, "output_tokens": 0, "observed_at": 12345})
                    emitted.set()
                    release.wait(3)
                    raise RunCancelled("stopped")

                with patch("pilferedparrot.web.capture_dispatch", side_effect=dispatch) as run:
                    chat_id = None
                    if chat_mode:
                        app.send_chat_message({"content": "test"})
                    else:
                        chat_id = app.store.create(Path(directory), "codex")["id"]
                        app.send_message(chat_id, {"content": "test"})
                    try:
                        self.assertTrue(emitted.wait(2))
                        public = app.store.chat_public() if chat_mode else app.store.public(app.store.get(chat_id))
                        usage = public["context_usage"]
                        self.assertEqual(usage["percent"], 50)
                        self.assertEqual(usage["source"], "provider")
                        self.assertEqual(usage["observed_at"], 12345)
                        self.assertTrue(any(m.get("pending") for m in public["messages"]))
                    finally:
                        release.set()
                        _wait_for_idle(app, chat_id)
                    public = app.store.chat_public() if chat_mode else app.store.public(app.store.get(chat_id))
                    self.assertEqual(public["context_usage"]["used_tokens"], 64000)
                    self.assertEqual(run.call_count, 1)

    def test_multiple_windows_share_allowance_probe_until_cache_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            app = _app(directory)
            with patch("pilferedparrot.web.collect_budgets", return_value={
                "codex": ProviderBudget("codex", True),
            }) as probe:
                app.budgets()
                app.budget_refreshed_at = time.monotonic() - 15
                app.budgets()
                self.assertEqual(probe.call_count, 1)
                app.budget_refreshed_at = time.monotonic() - 31
                app.budgets()
                self.assertEqual(probe.call_count, 2)


if __name__ == "__main__":
    unittest.main()
