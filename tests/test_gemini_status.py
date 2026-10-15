"""Gemini readiness and tier-error normalization regressions."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.adapters import GEMINI_AUTH_HELP, GEMINI_AUTH_TYPE_VARIABLES, GeminiAdapter
from pilferedparrot.budgets import collect_budgets
from pilferedparrot.config import load_config
from pilferedparrot.model import AUTH_UNKNOWN, STATUS_AUTH_UNVERIFIED, UNREACHABLE, Conversation


class GeminiStatusTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(Path("/definitely/missing/config.json"))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.system_settings = self.root / "system-settings.json"
        self.home = self.root / "home"
        self.system_settings = self.root / "system-settings.json"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        environment = patch.dict("os.environ", {
            "GEMINI_API_KEY": "synthetic-key",
            "GEMINI_CLI_HOME": str(self.home),
            "GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(self.system_settings),
        }, clear=False)
        environment.start()
        self.addCleanup(environment.stop)

    def _settings(self, path, auth):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"security": {"auth": auth}}), encoding="utf-8")

    def _launch(self, config=None, environment=None):
        """Run Gemini with a fake CLI; return (result, stream mock, launch env)."""
        adapter = GeminiAdapter("gemini", config or self.config)
        seen: dict = {}

        def fake(*args, stdout_line, env=None, **kwargs):
            seen.update(env)
            return subprocess.CompletedProcess(["gemini"], 0, "", "")

        with patch.dict("os.environ", environment or {}, clear=False), \
             patch.object(adapter, "_command", return_value=["gemini"]), \
             patch("pilferedparrot.dispatch._stream_process", side_effect=fake) as stream:
            result = adapter.run("synthetic prompt", self.workspace, Conversation(provider="gemini"))
        return result, stream, seen

    def test_authentication_does_not_trust_cached_credentials_or_environment(self):
        adapter = GeminiAdapter("gemini", self.config)
        with patch.dict("os.environ", {
            "GEMINI_API_KEY": "synthetic-key",
            "GOOGLE_API_KEY": "synthetic-google-key",
        }, clear=False), patch.object(Path, "is_file", return_value=True):
            self.assertIsNone(adapter.authentication())

    def test_budget_reports_installed_cli_with_unknown_auth(self):
        config = copy.deepcopy(self.config)
        with patch("pilferedparrot.budgets.provider_ids", return_value=("gemini",)), \
             patch("pilferedparrot.adapters.adapter_for") as factory:
            adapter = factory.return_value
            adapter.available.return_value = True
            result = collect_budgets(config)["gemini"]
        self.assertTrue(result.available)
        self.assertEqual(result.status, STATUS_AUTH_UNVERIFIED)
        self.assertEqual(result.auth_status, AUTH_UNKNOWN)
        self.assertEqual(result.reachability, UNREACHABLE)
        self.assertIn("checked when used", result.note or "")

    def test_unrelated_failed_stderr_is_preserved(self):
        adapter = GeminiAdapter("gemini", self.config)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            adapter, "_command", return_value=["gemini"]
        ), patch(
            "pilferedparrot.dispatch._stream_process",
            return_value=subprocess.CompletedProcess(
                ["gemini"], 7, "", "synthetic provider failure"
            ),
        ):
            result = adapter.run("synthetic prompt", Path(directory), Conversation(provider="gemini"))
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.error, "synthetic provider failure")

    def test_tier_error_is_actionable_and_does_not_expose_diagnostics(self):
        adapter = GeminiAdapter("gemini", self.config)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            adapter, "_command", return_value=["gemini"]
        ), patch(
            "pilferedparrot.dispatch._stream_process",
            return_value=subprocess.CompletedProcess(
                ["gemini"], 55, "", "IneligibleTierError: This client is no longer supported for Gemini Code Assist for individuals"
            ),
        ):
            result = adapter.run("synthetic prompt", Path(directory), Conversation(provider="gemini"))
        self.assertEqual(result.exit_code, 55)
        self.assertEqual(
            result.error,
            "Gemini CLI rejected this account tier. " + GEMINI_AUTH_HELP,
        )

    def test_successful_stream_is_unchanged(self):
        adapter = GeminiAdapter("gemini", self.config)
        lines = [
            json.dumps({"type": "init", "session_id": "synthetic-session"}),
            json.dumps({"type": "message", "role": "assistant", "content": "PPI_OK"}),
            json.dumps({"type": "result", "status": "success"}),
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            adapter, "_command", return_value=["gemini"]
        ), patch("pilferedparrot.dispatch._stream_process") as stream:
            def fake(*args, stdout_line, **kwargs):
                for line in lines:
                    stdout_line(line)
                return subprocess.CompletedProcess(["gemini"], 0, "", "")
            stream.side_effect = fake
            conversation = Conversation(provider="gemini")
            result = adapter.run("synthetic prompt", Path(directory), conversation)
        self.assertEqual(result.text, "PPI_OK")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(conversation.provider_session_id, "synthetic-session")

    def test_launch_environment_selects_only_the_key(self):
        # With no saved auth type the CLI chooses from the environment and
        # loads .env files only for unset variables, so every competing
        # selector is set explicitly.
        result, stream, env = self._launch(environment={
            "GOOGLE_GENAI_USE_GCA": "true", "GOOGLE_GEMINI_BASE_URL": "https://gateway.example",
        })
        self.assertEqual(result.exit_code, 0)
        stream.assert_called_once()
        self.assertEqual(env["GEMINI_API_KEY"], "synthetic-key")
        for name, value in (("GOOGLE_GENAI_USE_GCA", "false"), ("GOOGLE_GENAI_USE_VERTEXAI", "false"),
                            ("GEMINI_CLI_USE_COMPUTE_ADC", "false"), ("CLOUD_SHELL", "false"),
                            ("GOOGLE_GEMINI_BASE_URL", "")):
            self.assertEqual(env[name], value, name)

    def test_saved_key_auth_launches(self):
        self._settings(self.home / ".gemini" / "settings.json",
                       {"selectedType": "gemini-api-key", "enforcedType": "gemini-api-key"})
        result, stream, _ = self._launch()
        self.assertEqual(result.exit_code, 0)
        stream.assert_called_once()

    def test_configured_key_variable_supplies_the_cli_key(self):
        config = copy.deepcopy(self.config)
        config["gemini"]["api_key_env"] = "PPI_FIXTURE_GEMINI_KEY"
        result, _, env = self._launch(config, {"PPI_FIXTURE_GEMINI_KEY": "other-key"})
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(env["GEMINI_API_KEY"], "other-key")

    def test_vertex_credentials_select_vertex(self):
        config = copy.deepcopy(self.config)
        config["gemini"]["auth_type"] = "vertex-ai"
        result, _, env = self._launch(config, {"GOOGLE_API_KEY": "synthetic-vertex"})
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(env["GOOGLE_GENAI_USE_VERTEXAI"], "true")

    def test_sign_in_or_missing_credentials_refuse_before_launch(self):
        oauth = copy.deepcopy(self.config)
        oauth["gemini"]["auth_type"] = "oauth-personal"
        vertex = copy.deepcopy(self.config)
        vertex["gemini"]["auth_type"] = "vertex-ai"
        for config, environment, detail in (
            (self.config, {"GEMINI_API_KEY": ""}, "Set GEMINI_API_KEY"),
            (oauth, {}, "gemini.auth_type"),
            (vertex, {"GOOGLE_API_KEY": "", "GOOGLE_CLOUD_PROJECT": ""}, "GOOGLE_CLOUD_LOCATION"),
        ):
            with self.subTest(detail=detail):
                result, stream, _ = self._launch(config, environment)
                stream.assert_not_called()
                self.assertEqual(result.exit_code, 1)
                self.assertTrue(result.unavailable)
                self.assertIn("(OAuth) is not supported", result.error)
                self.assertIn(detail, result.error)

    def test_saved_google_sign_in_in_any_settings_layer_refuses_before_launch(self):
        # The CLI prefers a saved auth type over the environment, so a key
        # cannot displace a cached sign-in; PPI must refuse instead.
        layers = (
            self.root / "system-defaults.json",
            self.home / ".gemini" / "settings.json",
            self.workspace / ".gemini" / "settings.json",
            self.system_settings,
        )
        for path in layers:
            for auth in ({"selectedType": "oauth-personal"},
                         {"enforcedType": "compute-default-credentials"}):
                with self.subTest(path=path.name, auth=auth):
                    self._settings(path, auth)
                    try:
                        result, stream, _ = self._launch()
                    finally:
                        path.unlink()
                    stream.assert_not_called()
                    self.assertIn(str(path), result.error)
                    self.assertIn("/auth", result.error)
        legacy = self.home / ".gemini" / "settings.json"
        legacy.write_text('{"selectedAuthType": "oauth-personal"}', encoding="utf-8")
        result, stream, _ = self._launch()
        stream.assert_not_called()

    def test_commented_settings_are_read_and_unreadable_settings_refuse(self):
        path = self.home / ".gemini" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text('{\n  // "selectedType": "gemini-api-key"\n  "security": {"auth": '
                        '{"selectedType": /* saved */ "oauth-personal"}}}', encoding="utf-8")
        result, stream, _ = self._launch()
        stream.assert_not_called()
        self.assertIn("'oauth-personal'", result.error)
        path.write_text("{ not json", encoding="utf-8")
        result, stream, _ = self._launch()
        stream.assert_not_called()
        self.assertIn("could not read", result.error)

    def test_inherited_auth_type_variables_refuse_or_are_removed_in_both_modes(self):
        # Class test over every variable that names an auth type outright
        # (Gemini CLI 0.51 and 0.64 sources). 0.64 copies GEMINI_CLI_AUTH_OVERRIDE
        # into the user settings layer, above every environment selector.
        vertex = copy.deepcopy(self.config)
        vertex["gemini"]["auth_type"] = "vertex-ai"
        modes = ((self.config, "gemini-api-key", {}),
                 (vertex, "vertex-ai", {"GOOGLE_API_KEY": "synthetic-vertex"}))
        for config, allowed, credentials in modes:
            for name in GEMINI_AUTH_TYPE_VARIABLES:
                for value in ("oauth-personal", "compute-default-credentials", "gateway",
                              "cloud-shell", "vertex-ai" if allowed != "vertex-ai" else "gemini-api-key"):
                    with self.subTest(mode=allowed, name=name, value=value):
                        result, stream, _ = self._launch(config, {**credentials, name: value})
                        stream.assert_not_called()
                        self.assertIn(name, result.error)
                        self.assertIn("(OAuth) is not supported", result.error)
                with self.subTest(mode=allowed, name=name, value="matching"):
                    result, stream, env = self._launch(config, {**credentials, name: allowed})
                    stream.assert_called_once()
                    self.assertNotIn(name, env)

    def test_saved_external_auth_refuses(self):
        self._settings(self.home / ".gemini" / "settings.json", {"useExternal": True})
        result, stream, _ = self._launch()
        stream.assert_not_called()
        self.assertIn("useExternal", result.error)

    def test_relative_gemini_home_is_checked_where_the_cli_reads_it(self):
        # The proof case: user settings have no ownership gate, so a relative
        # GEMINI_CLI_HOME read from PPI's own directory would miss the file the
        # CLI loads from the project directory and let a saved sign-in through.
        self._settings(self.workspace / "relative-home" / ".gemini" / "settings.json",
                       {"selectedType": "oauth-personal"})
        result, stream, _ = self._launch(environment={"GEMINI_CLI_HOME": "relative-home"})
        stream.assert_not_called()
        self.assertIn(str(self.workspace / "relative-home"), result.error)
        (self.workspace / "relative-home" / ".gemini" / "settings.json").unlink()
        result, stream, env = self._launch(environment={"GEMINI_CLI_HOME": "relative-home"})
        stream.assert_called_once()
        self.assertEqual(env["GEMINI_CLI_HOME"], str(self.workspace / "relative-home"))

    def test_relative_system_paths_resolve_to_the_launch_directory(self):
        # Path consistency only: the files checked are the files forwarded.
        # Gemini CLI also gates system files on ownership, so these cases do
        # not by themselves show a sign-in bypass.
        for name, file_name in (("GEMINI_CLI_SYSTEM_SETTINGS_PATH", "sys.json"),
                                ("GEMINI_CLI_SYSTEM_DEFAULTS_PATH", "defaults.json")):
            with self.subTest(name=name):
                target = self.workspace / "conf" / file_name
                self._settings(target, {"selectedType": "oauth-personal"})
                result, stream, _ = self._launch(environment={name: f"conf/{file_name}"})
                stream.assert_not_called()
                self.assertIn(str(target), result.error)
                target.unlink()
                result, stream, env = self._launch(environment={name: f"conf/{file_name}"})
                stream.assert_called_once()
                self.assertEqual(env[name], str(target))

    def test_budget_names_the_missing_key(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False), \
             patch("pilferedparrot.budgets.provider_ids", return_value=("gemini",)), \
             patch("pilferedparrot.adapters.GeminiAdapter.available", return_value=True):
            result = collect_budgets(self.config)["gemini"]
        self.assertEqual(result.status, STATUS_AUTH_UNVERIFIED)
        self.assertIn("Set GEMINI_API_KEY", result.note or "")

if __name__ == "__main__":
    unittest.main()
