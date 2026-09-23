"""Explicit ACP setup and GPU inventory never launch providers in these tests."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pilferedparrot.acp_engine import ACPWorkResult
from pilferedparrot.config import DEFAULTS
from pilferedparrot.web import ActiveRun, PilferedParrotApp
from pilferedparrot.web_server import BrowserHTTPServer, make_handler


class ACPSetupBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = deepcopy(DEFAULTS)
        self.config["web"]["chat_store"] = str(self.root / "chats.json")
        self.config["web"]["port"] = 0
        self.config["web"]["model_catalog_store"] = str(self.root / "models.json")
        self.config["ledger"] = str(self.root / "runs.jsonl")
        self.install_patch = patch("pilferedparrot.acp_adapters.AdapterManager.install")
        self.locate_patch = patch("pilferedparrot.acp_adapters.AdapterManager.locate", return_value=None)
        self.gpu_patch = patch("pilferedparrot.gpu_inventory.snapshot_gpus", return_value={
            "available": True, "gpus": [{"uuid": "GPU-test"}], "error": None,
        })
        self.install = self.install_patch.start()
        self.locate = self.locate_patch.start()
        self.gpu = self.gpu_patch.start()
        self.app = PilferedParrotApp(self.config, self.root)
        self.server = BrowserHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.provider_token = self.app.issue_capability(
            "dashboard", window_id="provider-codex", provider="codex",
        )
        self.chat_token = self.app.issue_capability("chat")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.app.shutdown()
        self.gpu_patch.stop()
        self.locate_patch.stop()
        self.install_patch.stop()
        self.temp.cleanup()

    def request(self, path, *, method="GET", token=None, payload=None, origin=True):
        headers = {}
        if token is not None:
            headers["X-PilferedParrot-Capability"] = token
        if method == "POST":
            headers["Content-Type"] = "application/json"
            if origin:
                headers["Origin"] = self.origin
        request = Request(self.origin + path, method=method, headers=headers,
                          data=json.dumps(payload or {}).encode() if method == "POST" else None)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_start_and_state_do_not_probe_or_install(self):
        self.assertEqual(self.config["codex"]["engine"], "legacy")
        self.app.state("dashboard", window_id="main", window_provider="codex")
        self.install.assert_not_called()
        self.locate.assert_not_called()
        self.gpu.assert_not_called()

    def test_setup_install_and_gpu_are_on_demand(self):
        status, result = self.request("/api/acp/setup", token=self.app.dashboard_capability)
        self.assertEqual(status, 200)
        self.assertEqual(set(result["providers"]), {"codex", "claude"})
        self.assertEqual(result["providers"]["codex"], {
            "engine": "legacy", "installed": False, "error": "ACP adapter is not installed",
        })
        self.assertEqual(self.locate.call_count, 2)
        self.install.assert_not_called()
        self.gpu.assert_not_called()
        status, result = self.request("/api/acp/install", method="POST",
                                      token=self.app.dashboard_capability)
        self.assertEqual(status, 200)
        self.install.assert_called_once_with()
        self.assertEqual(set(result["providers"]), {"codex", "claude"})
        status, result = self.request("/api/hardware/gpus", token=self.app.dashboard_capability)
        self.assertEqual((status, result["gpus"][0]["uuid"]), (200, "GPU-test"))
        self.gpu.assert_called_once_with()

    def test_provider_window_only_owns_its_setup_and_engine(self):
        status, result = self.request("/api/acp/setup", token=self.provider_token)
        self.assertEqual((status, set(result["providers"])), (200, {"codex"}))
        status, _ = self.request("/api/acp/providers/claude/engine", method="POST",
                                 token=self.provider_token, payload={"engine": "acp"})
        self.assertEqual(status, 403)
        status, result = self.request("/api/acp/providers/codex/engine", method="POST",
                                      token=self.provider_token, payload={"engine": "acp"})
        self.assertEqual((status, result["providers"]["codex"]["engine"]), (200, "acp"))
        self.assertEqual(set(result["providers"]), {"codex"})

    def test_chat_missing_capability_and_origin_are_denied_without_effects(self):
        for token in (None, self.chat_token):
            for path in ("/api/acp/setup", "/api/hardware/gpus"):
                self.assertEqual(self.request(path, token=token)[0], 403)
            for path in ("/api/acp/install", "/api/acp/providers/codex/engine"):
                self.assertEqual(self.request(path, method="POST", token=token,
                                              payload={"engine": "acp"})[0], 403)
        self.assertEqual(self.request("/api/acp/install", method="POST",
                                      token=self.app.dashboard_capability, origin=False)[0], 403)
        self.assertEqual(self.request("/api/acp/providers/codex/engine", method="POST",
                                      token=self.app.dashboard_capability,
                                      payload={"engine": "acp"}, origin=False)[0], 403)
        self.install.assert_not_called()
        self.locate.assert_not_called()
        self.gpu.assert_not_called()

    def test_choice_persists_and_active_run_rejects_switch(self):
        status, _ = self.request("/api/acp/providers/codex/engine", method="POST",
                                 token=self.app.dashboard_capability, payload={"engine": "acp"})
        self.assertEqual(status, 200)
        chat = self.app.create_chat({"cwd": str(self.root)}, window_id="main",
                                    window_provider="codex")
        with self.app.runs_lock:
            self.app.runs[chat["id"]] = ActiveRun()
        try:
            status, _ = self.request("/api/acp/providers/codex/engine", method="POST",
                                     token=self.app.dashboard_capability,
                                     payload={"engine": "legacy"})
            self.assertEqual(status, 409)
            self.assertEqual(self.config["codex"]["engine"], "acp")
        finally:
            with self.app.runs_lock:
                self.app.runs.pop(chat["id"])
        restarted = PilferedParrotApp(deepcopy(DEFAULTS) | {
            "web": deepcopy(self.config["web"]), "ledger": self.config["ledger"],
        }, self.root)
        try:
            self.assertEqual(restarted.config["codex"]["engine"], "acp")
            self.assertEqual(restarted.config["claude"]["engine"], "legacy")
        finally:
            restarted.shutdown()

    def test_bad_choice_and_private_errors_are_safe(self):
        status, _ = self.request("/api/acp/providers/codex/engine", method="POST",
                                 token=self.app.dashboard_capability, payload={"engine": "auto"})
        self.assertEqual(status, 400)
        self.locate.side_effect = RuntimeError("/secret/path private@example.test")
        status, result = self.request("/api/acp/setup", token=self.app.dashboard_capability)
        self.assertEqual(status, 200)
        self.assertNotIn("/secret/path", json.dumps(result))
        self.assertNotIn("private@example.test", json.dumps(result))

    def test_engine_change_does_not_resume_previous_engine_session(self):
        self.locate.return_value = ["fake-node", "fake-adapter"]
        chat = self.app.create_chat({"cwd": str(self.root)}, window_id="main",
                                    window_provider="codex")
        with self.app.store.lock:
            saved = self.app.store.get(chat["id"])
            saved["provider"] = "codex"
            saved["model"] = self.config["codex"]["model"]
            saved["provider_session_id"] = "legacy-session"
            self.app.store.save()
        self.app.set_acp_engine("codex", "acp")
        resumed = []

        def fake_turn(_argv, **kwargs):
            resumed.append(kwargs["session_id"])
            return ACPWorkResult(text="done", text_truncated=False,
                                 session_id="acp-session", stop_reason="end_turn")

        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(chat["id"], {"content": "hello"}, window_id="main")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with self.app.runs_lock:
                    if chat["id"] not in self.app.runs:
                        break
                time.sleep(.01)
        self.assertEqual(resumed, [None])
