"""Dynamic ACP option probes use advertised values without prompting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pilferedparrot.acp_client import ACPError
from pilferedparrot.acp_catalog import discover_acp_options


def options(model: str = "sonnet", effort: bool = True):
    result = [{"id": "model", "category": "model", "currentValue": model,
               "options": [
                   {"value": "sonnet", "name": "Sonnet"},
                   {"value": "haiku", "name": "Haiku",
                    "description": "Account private@example.test"},
               ]}]
    if effort:
        result.append({"id": "effort", "category": "thought_level", "currentValue": "high",
                       "options": [{"value": "low", "name": "Low"},
                                   {"value": "high", "name": "High"}]})
    return result


class FakeClient:
    instances = []

    def __init__(self, _argv, *, cwd, env):
        self.cwd = cwd
        self.env = env
        self.agent_capabilities = {"sessionCapabilities": {"close": {}}}
        self.closed = False
        self.session_closed = False
        self.prompted = False
        self.model = "sonnet"
        type(self).instances.append(self)

    def initialize(self, *, timeout):
        return {"protocolVersion": 1}

    def new_session(self, cwd, *, timeout):
        return {"sessionId": "temporary-session", "configOptions": options(),
                "modes": {"availableModes": [
                    {"id": "default", "name": "Default"},
                    {"id": "plan", "name": "Plan", "accountEmail": "private@example.test"},
                ], "currentModeId": "default"}}

    def set_config_option(self, session_id, config_id, value, *, timeout):
        self.model = value
        return {"configOptions": options(value, effort=value != "haiku")}

    def close_session(self, session_id, *, timeout):
        self.session_closed = True
        return {}

    def prompt(self, *_args, **_kwargs):
        self.prompted = True
        raise AssertionError("an option probe must not send a model prompt")

    def close(self):
        self.closed = True


class ACPOptionCatalogTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances.clear()

    def test_model_change_refreshes_effort_options_and_closes_session(self):
        with tempfile.TemporaryDirectory() as directory:
            result = discover_acp_options(
                ["fake-agent"], cwd=Path(directory).resolve(), env={"PATH": "/safe"},
                model="haiku", client_factory=FakeClient,
            )
        self.assertEqual([item["value"] for item in result["models"]], ["sonnet", "haiku"])
        self.assertEqual(result["efforts"], [])
        self.assertEqual(result["current_model"], "haiku")
        self.assertEqual([item["value"] for item in result["modes"]], ["default", "plan"])
        self.assertNotIn("private@example.test", str(result))
        self.assertTrue(FakeClient.instances[0].closed)
        self.assertTrue(FakeClient.instances[0].session_closed)
        self.assertFalse(FakeClient.instances[0].prompted)

    def test_unknown_model_fails_before_prompt_and_still_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ACPError, "not advertised"):
                discover_acp_options(
                    ["fake-agent"], cwd=Path(directory).resolve(), env={},
                    model="invented", client_factory=FakeClient,
                )
        self.assertTrue(FakeClient.instances[0].closed)
        self.assertTrue(FakeClient.instances[0].session_closed)
        self.assertFalse(FakeClient.instances[0].prompted)

    def test_workspace_and_environment_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                discover_acp_options(["fake"], cwd=Path("relative"), env={},
                                     client_factory=FakeClient)
            with self.assertRaises(ValueError):
                discover_acp_options(["fake"], cwd=Path(directory).resolve(), env=None,
                                     client_factory=FakeClient)
        self.assertEqual(FakeClient.instances, [])

    def test_oversized_model_ids_are_not_truncated_into_false_choices(self):
        class LongOptionClient(FakeClient):
            def new_session(self, cwd, *, timeout):
                session = super().new_session(cwd, timeout=timeout)
                session["configOptions"][0]["options"].append({
                    "value": "x" * 129, "name": "Too long",
                })
                return session
        with tempfile.TemporaryDirectory() as directory:
            result = discover_acp_options(
                ["fake"], cwd=Path(directory).resolve(), env={},
                client_factory=LongOptionClient,
            )
        self.assertEqual([item["value"] for item in result["models"]], ["sonnet", "haiku"])

    def test_model_switch_uses_returned_model_list(self):
        class ChangingClient(FakeClient):
            def set_config_option(self, session_id, config_id, value, *, timeout):
                result = super().set_config_option(session_id, config_id, value, timeout=timeout)
                result["configOptions"][0]["options"] = [
                    {"value": "haiku", "name": "Haiku"},
                ]
                return result
        with tempfile.TemporaryDirectory() as directory:
            result = discover_acp_options(
                ["fake"], cwd=Path(directory).resolve(), env={}, model="haiku",
                client_factory=ChangingClient,
            )
        self.assertEqual([item["value"] for item in result["models"]], ["haiku"])


if __name__ == "__main__":
    unittest.main()
