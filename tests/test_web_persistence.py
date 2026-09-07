import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot import web_persistence
from pilferedparrot.web_persistence import (
    DashboardModelStore,
    PersistentChatStore,
    chat_store_path,
    dashboard_capability_path,
    legacy_chat_store_path,
    load_dashboard_models,
    model_catalog_path,
    read_dashboard_capability,
    remove_dashboard_capability,
    write_dashboard_capability,
)


class PersistencePathTests(unittest.TestCase):
    def test_paths_resolve_configured_and_default_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"web": {"chat_store": str(root / "nested" / "chats.json"), "port": 8765}}
            self.assertEqual(chat_store_path(config), root / "nested" / "chats.json")
            self.assertEqual(model_catalog_path(config), root / "nested" / "models.json")
            self.assertEqual(
                model_catalog_path({"web": {"chat_store": str(root / "chats.json"),
                                               "model_catalog_store": str(root / "catalog.json")}}),
                root / "catalog.json",
            )
            self.assertEqual(dashboard_capability_path(config), root / "nested" / "server-8765.json")

    def test_legacy_path_is_only_for_the_default_store(self):
        config = {"web": {"chat_store": "~/.local/state/pilferedparrot/chats.json"}}
        self.assertEqual(
            legacy_chat_store_path(config),
            web_persistence.expanded_path("~/.local/state/ai-conductor/chats.json"),
        )
        config["web"]["chat_store"] = "/tmp/custom-pilferedparrot-chats.json"
        self.assertIsNone(legacy_chat_store_path(config))


class DashboardModelPersistenceTests(unittest.TestCase):
    def test_malformed_catalog_is_normalized_to_empty_model_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps({
                "version": "bad",
                "providers": {
                    "codex": {"models": [{"value": "ok"}, "bad"], "hidden": ["x", 3]},
                    "BAD!": {"models": [{"value": "ignored"}]},
                    "qwen": "invalid",
                },
                "provider_cards": {"ollama": {"model": "local"}, "BAD!": []},
                "hidden_providers": ["qwen", 4],
            }), encoding="utf-8")
            data = load_dashboard_models(path, ("codex", "qwen"))
            self.assertEqual(data["version"], 2)
            self.assertEqual(data["providers"]["codex"], {"models": [{"value": "ok"}], "hidden": ["x"]})
            self.assertEqual(data["providers"]["qwen"], {})
            self.assertEqual(data["provider_cards"], {"ollama": {"model": "local"}})
            self.assertEqual(data["hidden_providers"], ["qwen"])

    def test_save_is_owner_only_and_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            store = DashboardModelStore(path, ("codex",))
            store.save({"version": 2, "providers": {"codex": {}}, "provider_cards": {}, "hidden_providers": []})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 2)
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])


class DashboardCapabilityPersistenceTests(unittest.TestCase):
    def test_origin_and_owner_mode_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"web": {"chat_store": str(Path(directory) / "chats.json"), "port": 1234}}
            write_dashboard_capability(config, "http://127.0.0.1:1234", "secret")
            path = dashboard_capability_path(config)
            self.assertEqual(read_dashboard_capability("http://127.0.0.1:1234", config), "secret")
            self.assertIsNone(read_dashboard_capability("http://127.0.0.1:9999", config))
            if os.name == "posix":
                path.chmod(0o644)
                self.assertIsNone(read_dashboard_capability("http://127.0.0.1:1234", config))

    def test_remove_only_removes_a_matching_token(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"web": {"chat_store": str(Path(directory) / "chats.json"), "port": 1234}}
            write_dashboard_capability(config, "http://127.0.0.1:1234", "secret")
            remove_dashboard_capability(config, "wrong")
            self.assertTrue(dashboard_capability_path(config).exists())
            remove_dashboard_capability(config, "secret")
            self.assertFalse(dashboard_capability_path(config).exists())


class AtomicWriteFailureTests(unittest.TestCase):
    def test_serialization_failure_cleans_temporary_file_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text("original\n", encoding="utf-8")
            store = DashboardModelStore(path, ("codex",))
            with self.assertRaises(TypeError):
                store.save({"not-json": object()})
            self.assertEqual(path.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_replace_failure_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            store = DashboardModelStore(path, ("codex",))
            with patch("pilferedparrot.web_persistence.os.replace", side_effect=OSError("replace")):
                with self.assertRaises(OSError):
                    store.save({"version": 2, "providers": {}, "provider_cards": {}, "hidden_providers": []})
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])


class PersistentChatPersistenceTests(unittest.TestCase):
    @staticmethod
    def _usage(*args, **kwargs):
        return {"percent": 0, "used_tokens": 0, "limit_tokens": 100}

    def test_corrupt_input_is_rejected_without_replacing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                PersistentChatStore(path, context_usage=self._usage)
            self.assertEqual(path.read_text(encoding="utf-8"), "{not-json")

    def test_save_is_owner_only_with_injected_context_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.json"
            store = PersistentChatStore(path, context_usage=self._usage)
            store.save()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 8)

    def test_work_draft_round_trips_and_protects_session_from_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.json"
            store = PersistentChatStore(path, context_usage=self._usage)
            chat = store.create(Path(directory), "codex")
            updated = store.set_draft(chat["id"], "keep this")
            self.assertEqual(updated["draft"], "keep this")
            reloaded = PersistentChatStore(path, context_usage=self._usage)
            self.assertEqual(reloaded.get(chat["id"])["draft"], "keep this")
            reloaded.get(chat["id"])["updated_at"] = 0
            reloaded.save()
            self.assertEqual(reloaded.cleanup_stale_empty_sessions(
                now=86_400, protected_chat_ids=(chat["id"],)), 0)

    def test_daily_cleanup_removes_only_old_strictly_empty_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.json"
            store = PersistentChatStore(path, context_usage=self._usage)
            empty = store.create(Path(directory), "codex")
            draft = store.create(Path(directory), "codex")
            store.get(draft["id"])["draft"] = "typed"
            active = store.create(Path(directory), "codex")
            store.get(active["id"])["provider_session_id"] = "session"
            for chat in (empty, draft, active): store.get(chat["id"])["updated_at"] = 0
            store.save()
            self.assertEqual(store.cleanup_stale_empty_sessions(now=86_400), 1)
            self.assertNotIn(empty["id"], {chat["id"] for chat in store.data["chats"]})
            self.assertEqual(len(store.data["chats"]), 2)
            self.assertEqual(store.cleanup_stale_empty_sessions(now=86_401), 0)

    def test_cleanup_preserves_selected_sessions_and_all_activity_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PersistentChatStore(Path(directory) / "chats.json", context_usage=self._usage)
            kept = []
            for field, value in (("draft", " "), ("messages", [{"role": "user", "content": "keep"}]),
                                 ("harness_tasks", [{"id": "task"}]), ("attachments", ["file"]),
                                 ("provider_messages", [{"role": "user", "content": "keep"}]),
                                 ("title", "Named by user")):
                chat = store.create(Path(directory), "codex")
                raw = store.get(chat["id"]); raw[field] = value; raw["updated_at"] = 0
                kept.append(chat["id"])
            for _ in range(2):
                chat = store.create(Path(directory), "codex")
                store.get(chat["id"])["updated_at"] = 0
                kept.append(chat["id"])
            empty = store.create(Path(directory), "codex")
            store.get(empty["id"])["updated_at"] = 0
            self.assertEqual(store.cleanup_stale_empty_sessions(now=86400, protected_chat_ids=kept[-2:]), 1)
            self.assertEqual({c["id"] for c in store.data["chats"]}, set(kept))
            reloaded = PersistentChatStore(store.path, context_usage=self._usage)
            self.assertEqual(reloaded.cleanup_stale_empty_sessions(now=86401), 0)
            self.assertEqual(reloaded.cleanup_stale_empty_sessions(now=172800), 2)


if __name__ == "__main__":
    unittest.main()
