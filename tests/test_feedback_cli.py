import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from pilferedparrot import cli


class FeedbackCliTests(unittest.TestCase):
    def setUp(self):
        self.config = {"ledger": str(Path.cwd() / "feedback-cli-ledger.jsonl"), "web": {}}

    def test_feedback_defaults_to_status_and_prints_json(self):
        store = Mock()
        store.status.return_value = {
            "policy_version": 1, "consent": {}, "disabled": True, "retention_days": 30,
        }
        with patch.object(cli, "load_config", return_value=self.config), patch.object(
            cli, "_feedback_store", return_value=store
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            result = cli.main(["--cwd", str(Path.cwd()), "feedback"])
        self.assertEqual(result, 0)
        self.assertIn('"disabled": true', output.getvalue())
        store.status.assert_called_once_with()
        store.close.assert_called_once_with()

    def test_enable_requires_explicit_policy_acceptance(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["feedback", "enable", "usage"])

    def test_enable_disable_clear_off_and_report_dispatch_to_store(self):
        for argv, method, args in (
            (["feedback", "enable", "usage", "--accept-policy"], "set_consent", ({"policy_version": 1, "category": "usage", "enabled": True},)),
            (["feedback", "enable", "local_changes", "--accept-policy"], "set_consent", ({"policy_version": 1, "category": "local_changes", "enabled": True},)),
            (["feedback", "disable", "problems"], "set_consent", ({"policy_version": 1, "category": "problems", "enabled": False},)),
            (["feedback", "clear"], "clear", ()),
            (["feedback", "off"], "clear", ()),
            (["feedback", "report"], "report", ()),
        ):
            with self.subTest(argv=argv):
                store = Mock()
                getattr(store, method).return_value = {"ok": True}
                with patch.object(cli, "load_config", return_value=self.config), patch.object(
                    cli, "_feedback_store", return_value=store
                ), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["--cwd", str(Path.cwd()), *argv]), 0)
                if argv[-1] == "off":
                    store.clear.assert_called_once_with(reset=True)
                else:
                    getattr(store, method).assert_called_once_with(*args)
                store.close.assert_called_once_with()

    def test_prompt_records_aggregate_events_without_changing_result(self):
        store = Mock()
        conversation = Mock()
        with patch.object(cli, "_feedback_store", return_value=store), patch.object(
            cli, "dispatch", return_value=7
        ), patch.object(cli, "append_run"):
            result = cli.run_prompt("secret prompt", "codex", Path.cwd(), conversation, self.config)
        self.assertEqual(result, 7)
        self.assertEqual(store.record.call_args_list, [
            call("usage", "message_sent", "cli"), call("problems", "provider_failed", "cli"),
        ])
        store.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
