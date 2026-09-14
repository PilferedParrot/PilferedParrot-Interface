import json
import math
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.budgets import codex_budget_from_response, read_codex_budget
from pilferedparrot.config import load_config
from pilferedparrot.model import (
    AUTH_SIGNED_IN,
    REACHABLE,
    USAGE_UNAVAILABLE,
    USAGE_UNSUPPORTED,
)


class CodexAllowanceTests(unittest.TestCase):
    def test_malformed_values_do_not_create_or_crash_windows(self):
        budget = codex_budget_from_response({
            "rateLimits": {
                "primary": {
                    "usedPercent": 0,
                    "windowDurationMins": 300,
                    "resetsAt": float("inf"),
                },
                "secondary": {
                    "usedPercent": "NaN",
                    "windowDurationMins": True,
                    "resetsAt": "9999-99-99T00:00:00Z",
                },
            }
        })
        self.assertEqual(len(budget.windows), 1)
        self.assertEqual(budget.window.remaining_percent, 100)
        self.assertEqual(budget.window.window_minutes, 300)
        self.assertIsNone(budget.window.resets_at)

    def test_malformed_bucket_falls_back_to_legacy_rate_limits(self):
        budget = codex_budget_from_response({
            "rateLimitsByLimitId": {"broken": {"primary": {"usedPercent": math.nan}}},
            "rateLimits": {
                "primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 0},
            },
        })
        self.assertEqual(len(budget.windows), 1)
        self.assertEqual(budget.window.used_percent, 0)
        self.assertEqual(budget.window.resets_at, 0)

    @patch("pilferedparrot.budgets.subprocess.Popen")
    @patch("pilferedparrot.budgets.subprocess.run")
    @patch("pilferedparrot.budgets.resolve_command", return_value="/usr/bin/codex")
    def test_api_key_login_is_signed_in_but_has_no_chatgpt_allowance(
        self, _resolve, run, popen,
    ):
        run.return_value = subprocess.CompletedProcess(
            [], 0, "Logged in using API key", "",
        )
        budget = read_codex_budget(load_config(Path("/definitely/missing/config.json")))
        self.assertTrue(budget.available)
        self.assertEqual(budget.auth_status, AUTH_SIGNED_IN)
        self.assertEqual(budget.reachability, REACHABLE)
        self.assertEqual(budget.usage_status, USAGE_UNSUPPORTED)
        self.assertIn("API usage is billed separately; no ChatGPT plan allowance", budget.usage_note)
        popen.assert_not_called()

    @patch("pilferedparrot.budgets.subprocess.Popen", side_effect=OSError("down"))
    @patch("pilferedparrot.budgets.subprocess.run")
    @patch("pilferedparrot.budgets.resolve_command", return_value="/usr/bin/codex")
    def test_probe_failure_keeps_login_and_explains_unavailable_usage(
        self, _resolve, run, _popen,
    ):
        run.return_value = subprocess.CompletedProcess([], 0, "Logged in", "")
        budget = read_codex_budget(load_config(Path("/definitely/missing/config.json")))
        self.assertEqual(budget.auth_status, AUTH_SIGNED_IN)
        self.assertEqual(budget.usage_status, USAGE_UNAVAILABLE)
        self.assertIn("Live Codex allowance unavailable", budget.usage_note)


if __name__ == "__main__":
    unittest.main()
