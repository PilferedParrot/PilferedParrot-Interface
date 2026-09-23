"""The model picker probes only the selected, owned ACP workspace."""

from __future__ import annotations

import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilferedparrot.acp_client import ACPError
from pilferedparrot.config import load_config
from pilferedparrot.web import PilferedParrotApp, make_handler


def discovery(model: str = "sonnet") -> dict:
    models = [
        {"value": "sonnet", "label": "Sonnet", "description": ""},
        {"value": "haiku", "label": "Haiku", "description": ""},
    ]
    efforts = [{"value": "deep", "label": "Deep", "description": ""}] \
        if model == "sonnet" else []
    return {
        "models": models, "efforts": efforts,
        "modes": [{"value": "plan", "label": "Plan", "description": ""}],
        "current_model": model, "current_effort": "deep" if efforts else "",
        "current_mode": "plan",
    }


class ACPOptionsBackendTests(unittest.TestCase):
    def config(self, root: Path) -> dict:
        config = load_config(root / "missing.json")
        config["claude"]["engine"] = "acp"
        config["web"]["chat_store"] = str(root / "chats.json")
        config["web"]["model_catalog_store"] = str(root / "models.json")
        config["ledger"] = str(root / "runs.jsonl")
        return config

    def handler(self, app: PilferedParrotApp, path: str, context: dict | None):
        handler = object.__new__(make_handler(app))
        handler.path = path
        handler._local_request_allowed = lambda: True
        handler._request_capability_context = lambda **_kwargs: context
        handler._json = MagicMock()
        return handler

    def test_startup_does_not_probe_or_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("pilferedparrot.web.discover_acp_options") as probe, \
                    patch("pilferedparrot.acp_adapters.AdapterManager.install") as install:
                app = PilferedParrotApp(self.config(root), root)
                app.state()
            probe.assert_not_called()
            install.assert_not_called()

    def test_picker_uses_owned_project_and_live_options_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = root / "owned"
            owned.mkdir()
            app = PilferedParrotApp(self.config(root), root)
            app.store.remember_project(owned, "claude-window")
            app.acp_adapters.locate = MagicMock(return_value=["node", "adapter.js"])
            with patch("pilferedparrot.web.discover_acp_options", side_effect=[
                discovery("sonnet"), discovery("haiku"),
            ]) as probe:
                first = app.poll_provider_models(
                    "claude", model="sonnet", window_id="claude-window",
                    window_provider="claude",
                )
                second = app.poll_provider_models(
                    "claude", model="haiku", window_id="claude-window",
                    window_provider="claude",
                )
            self.assertEqual(probe.call_args_list[0].kwargs["cwd"], owned.resolve())
            self.assertEqual(probe.call_args_list[1].kwargs["model"], "haiku")
            self.assertEqual(probe.call_args_list[0].kwargs["timeout"], 10)
            self.assertNotIn("CODEX_THREAD_ID", probe.call_args_list[0].kwargs["env"])
            self.assertEqual(first["source"], "acp")
            self.assertEqual(first["default"], "sonnet")
            self.assertEqual([item["value"] for item in first["options"]], ["sonnet", "haiku"])
            self.assertEqual(first["options"][0]["reasoning_efforts"], ["deep"])
            self.assertNotIn("reasoning_efforts", first["options"][1])
            self.assertEqual(second["acp_options"]["efforts"], [])
            self.assertEqual(second["options"][1]["reasoning_efforts"], [])
            self.assertNotIn("reasoning_efforts", second["options"][0])
            self.assertEqual(second["acp_options"]["modes"][0]["value"], "plan")
            self.assertNotIn("agent_protocol", second["acp_options"])
            app.acp_adapters.locate.assert_called_with("claude")

    def test_unadvertised_model_is_rejected_without_leaking_agent_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = PilferedParrotApp(self.config(root), root)
            app.acp_adapters.locate = MagicMock(return_value=["node", "adapter.js"])
            with patch("pilferedparrot.web.discover_acp_options",
                       side_effect=ACPError("requested ACP model is not advertised")):
                with self.assertRaisesRegex(ValueError, "not advertised"):
                    app.poll_provider_models("claude", model="invented")
            with self.assertRaisesRegex(ValueError, "valid model ID"):
                app.poll_provider_models("claude", model="")
            with patch("pilferedparrot.web.discover_acp_options",
                       side_effect=ACPError("private@example.test secret")):
                with self.assertRaisesRegex(RuntimeError, "ACP option discovery failed") as error:
                    app.poll_provider_models("claude")
                self.assertNotIn("private@example.test", str(error.exception))

    def test_route_denies_wrong_window_and_chat_before_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = PilferedParrotApp(self.config(root), root)
            with patch("pilferedparrot.web.discover_acp_options") as probe, \
                    patch.object(app.acp_adapters, "locate") as locate:
                for context in (
                    None,
                    {"scope": "dashboard", "window_id": "codex-window", "provider": "codex"},
                    {"scope": "chat", "window_id": "main", "provider": "codex"},
                ):
                    handler = self.handler(app, "/api/providers/claude/models?model=haiku", context)
                    handler.do_GET()
                    self.assertEqual(handler._json.call_args.args[1], HTTPStatus.FORBIDDEN)
            probe.assert_not_called()
            locate.assert_not_called()

    def test_chat_own_provider_gets_static_catalog_without_acp_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = PilferedParrotApp(self.config(root), root)
            context = {"scope": "chat", "window_id": "main", "provider": "claude"}
            handler = self.handler(app, "/api/providers/claude/models?model=haiku", context)
            with patch("pilferedparrot.web.discover_acp_options") as probe, \
                    patch.object(app.acp_adapters, "locate") as locate, \
                    patch("pilferedparrot.web.adapter_for") as adapter:
                adapter.return_value.models.return_value = [
                    {"value": "legacy-sonnet", "label": "Legacy Sonnet"},
                ]
                handler.do_GET()
            response = handler._json.call_args.args[0]
            self.assertEqual(len(handler._json.call_args.args), 1)  # default HTTP 200
            self.assertEqual(response["source"], "native_catalog")
            self.assertEqual(response["options"][0]["value"], "legacy-sonnet")
            self.assertNotIn("acp_options", response)
            probe.assert_not_called()
            locate.assert_not_called()
            adapter.assert_called_once_with("claude", app.config)

    def test_route_passes_exact_model_and_window_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = PilferedParrotApp(self.config(root), root)
            context = {"scope": "dashboard", "window_id": "claude-window", "provider": "claude"}
            handler = self.handler(app, "/api/providers/claude/models?model=haiku&cwd=/tmp", context)
            with patch.object(app, "poll_provider_models", return_value={"source": "acp"}) as poll:
                handler.do_GET()
            poll.assert_called_once_with(
                "claude", model="haiku", window_id="claude-window",
                window_provider="claude", scope="dashboard",
            )
            handler._json.assert_called_once_with({"source": "acp"})

    def test_provider_engines_are_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = PilferedParrotApp(self.config(root), root)
            self.assertEqual(app.state()["provider_engines"],
                             {"codex": "legacy", "claude": "acp"})
            self.assertEqual(app.state(window_id="claude-window", window_provider="claude")
                             ["provider_engines"], {"claude": "acp"})
            self.assertEqual(app.state(scope="chat", window_provider="claude")
                             ["provider_engines"], {"claude": "acp"})

    def test_acp_effort_passes_through_but_legacy_still_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = PilferedParrotApp(self.config(root), root)
            work = app.create_chat({"provider": "claude"})
            saved = app.set_reasoning_effort(work["id"], {"reasoning_effort": "deep"})
            self.assertEqual(saved["reasoning_effort"], "deep")
            with self.assertRaisesRegex(ValueError, "only supported for Codex"):
                app._selected_reasoning_effort("qwen", "any", "deep")


if __name__ == "__main__":
    unittest.main()
