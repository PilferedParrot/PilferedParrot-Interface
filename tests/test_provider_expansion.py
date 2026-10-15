"""Application integration checks for the new coding-provider paths."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.adapters import GeminiAdapter, ProviderCapabilities, adapter_for
from pilferedparrot.budgets import collect_budgets
from pilferedparrot.config import load_config, open_compatible_url
from pilferedparrot.model import provider_ids
from pilferedparrot.web import PROVIDER_TEMPLATES, PilferedParrotApp
from pilferedparrot.web_provider import ProviderRunOrchestrator


class ProviderExpansionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = load_config(self.root / "missing.json")
        self.config["web"].update({
            "chat_store": str(self.root / "chats.json"),
            "model_catalog_store": str(self.root / "models.json"),
        })
        self.config["ledger"] = str(self.root / "runs.jsonl")

    def test_coding_templates_persist_and_restore_without_discovery(self):
        app = PilferedParrotApp(self.config, self.root)
        with patch.object(app, "_discover_provider_models") as discover:
            cards = [app.add_provider({"template": template, "model": model})
                     for template, model in (
                         ("openrouter", "deepseek/fixture"),
                         ("mistral", "devstral-fixture"),
                         ("lmstudio", "local-fixture"),
                     )]
        discover.assert_not_called()
        restored = PilferedParrotApp(self.config, self.root)
        for card, endpoint, variable in zip(cards, (
            "https://openrouter.ai/api/v1", "https://api.mistral.ai/v1",
            "http://127.0.0.1:1234/v1",
        ), ("OPENROUTER_API_KEY", "MISTRAL_API_KEY", "")):
            settings = restored.config[card["id"]]
            self.assertEqual(settings["base_url"], endpoint)
            self.assertEqual(settings["api_key_env"], variable)
            self.assertTrue(adapter_for(card["id"], restored.config).capabilities.tools)

    def test_work_only_provider_keeps_resume_and_rejects_chat_before_mutation(self):
        # No shipped adapter is Work only today; the capability boundary is
        # provider-neutral and must still hold for any adapter that declares it.
        work_only = ProviderCapabilities(chat=False, resume=True, tools=True, models=True)
        with patch.object(GeminiAdapter, "capabilities", work_only):
            app = PilferedParrotApp(self.config, self.root)
            orchestrator = ProviderRunOrchestrator(self.config)
            prepared = orchestrator.prepare(
                "gemini", self.root, session_id="exact-session", model="auto",
                current_provider="gemini", current_model="auto",
            )
            self.assertEqual(prepared.conversation.provider_session_id, "exact-session")
            original = app.store.data["chat"].copy()
            for operation in (
                lambda: orchestrator.prepare("gemini", self.root, mode="chat"),
                lambda: app.issue_capability("chat", provider="gemini"),
                lambda: app.open_chat_window("http://127.0.0.1", {"provider": "gemini"}),
                lambda: app.send_chat_message({"provider": "gemini", "content": "hello"}),
            ):
                with self.assertRaisesRegex(ValueError, "Work only"):
                    operation()
            self.assertEqual(app.store.data["chat"], original)
            self.assertIsNone(app.chat_run)

    def test_gemini_presence_does_not_claim_authenticated_access(self):
        self.config["_hidden_providers"] = ["qwen", "codex", "claude"]
        with patch("pilferedparrot.config.resolve_command", return_value="/fake/gemini"), \
             patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic-key",
                                         "GEMINI_CLI_HOME": str(self.root)}):
            budget = collect_budgets(self.config)["gemini"]
        self.assertTrue(budget.available)
        self.assertEqual(budget.status, "auth_unverified")
        self.assertIn("checked when used", budget.note)

    def test_removed_providers_are_absent_and_stale_settings_fall_back(self):
        # Antigravity and the xAI template were removed (provider terms; owner's
        # choice). Settings saved by an earlier release must still load.
        self.assertNotIn("antigravity", provider_ids())
        self.assertNotIn("xai", {item["id"] for item in PROVIDER_TEMPLATES})
        with self.assertRaisesRegex(ValueError, "unknown provider"):
            adapter_for("antigravity", self.config)
        stale = self.root / "stale.json"
        stale.write_text(json.dumps({
            "antigravity": {"command": "agy", "model": "fixture", "mode": "accept-edits"},
            "web": {"default_provider": "antigravity"},
            "_hidden_providers": ["antigravity"],
        }), encoding="utf-8")
        config = load_config(stale)
        config["web"].update({
            "chat_store": str(self.root / "stale-chats.json"),
            "model_catalog_store": str(self.root / "stale-models.json"),
        })
        app = PilferedParrotApp(config, self.root)
        self.assertEqual(app.default_provider, "codex")
        self.assertNotIn("antigravity", provider_ids(config))
        with self.assertRaisesRegex(ValueError, "unknown provider template"):
            app.add_provider({"template": "xai", "model": "fixture"})

    def test_saved_xai_cards_are_retired_and_never_contact_the_endpoint(self):
        # Cards saved from the removed template survive in the dashboard store.
        # They load (history stays readable) but show a notice and never run;
        # a user's own generic compatible card is untouched.
        app = PilferedParrotApp(self.config, self.root)
        cards = app.dashboard_models.setdefault("provider_cards", {})
        cards["xai-grok"] = {  # the reviewer's prior-release repro
            "label": "xAI / Grok", "adapter": "openai_compatible",
            "base_url": "https://api.x.ai/v1", "api_key_env": "XAI_API_KEY",
            "model": "grok-fixture", "model_options": ["grok-fixture"],
        }
        cards["my-renamed-card"] = {
            "label": "Renamed", "description": "Grok models through xAI's OpenAI-compatible API.",
            "adapter": "openai_compatible", "base_url": "https://api.x.ai/v1",
            "api_key_env": "MY_KEY", "model": "grok-fixture", "model_options": [],
        }
        cards["my-endpoint"] = {
            "label": "Mine", "description": "An OpenAI-compatible LLM endpoint.",
            "adapter": "openai_compatible", "base_url": "https://models.example/v1",
            "api_key_env": "MY_KEY", "model": "fixture", "model_options": [],
        }
        for provider in cards:
            app.dashboard_models.setdefault("providers", {})[provider] = {"models": [], "hidden": []}
        app._save_dashboard_models()
        restarted = PilferedParrotApp(self.config, self.root)
        catalog = {item["id"]: item for item in restarted._provider_catalog(include_hidden=True)}
        with patch("pilferedparrot.config._compatible_opener") as opener:
            for provider in ("xai-grok", "my-renamed-card"):
                with self.subTest(provider=provider):
                    self.assertTrue(catalog[provider]["retired"])
                    self.assertIn("removed from PPI", catalog[provider]["description"])
                    with self.assertRaisesRegex(ValueError, "removed from PPI"):
                        adapter_for(provider, restarted.config)
                    with self.assertRaisesRegex(ValueError, "removed from PPI"):
                        open_compatible_url(restarted.config, provider,
                                            "https://api.x.ai/v1/models", timeout=1)
                    budget = collect_budgets(restarted.config)[provider]
                    self.assertFalse(budget.available)
                    self.assertIn("removed from PPI", budget.note)
            opener.assert_not_called()
        self.assertNotIn("retired", catalog["my-endpoint"])
        self.assertTrue(adapter_for("my-endpoint", restarted.config).capabilities.tools)
        restarted.remove_provider({"provider": "xai-grok"})
        self.assertNotIn("restore:xai-grok",
                         [item["id"] for item in restarted.provider_templates()])
        with self.assertRaisesRegex(ValueError, "removed from PPI"):
            restarted.add_provider({"template": "restore:xai-grok"})


if __name__ == "__main__":
    unittest.main()
