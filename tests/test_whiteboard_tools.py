import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.config import DEFAULTS
from pilferedparrot.qwen_tools import QwenToolbox, TOOL_DEFINITIONS


class WhiteboardToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        config = deepcopy(DEFAULTS)
        config["_whiteboard_directory"] = str(root / "board")
        self.toolbox = QwenToolbox(root, config)

    def test_empty_board_with_tiny_output_budget_returns_valid_error(self):
        self.toolbox.output_limit = 40
        result = json.loads(self.toolbox.execute("whiteboard_read", {}))
        self.assertIn("output budget", result["error"])
        self.assertGreater(result["required_chars"], self.toolbox.output_limit)

    def test_structured_roundtrip_search_reply_and_status_update(self):
        first = json.loads(self.toolbox.execute("whiteboard_post", {
            "text": "The parser emits stable IDs.",
            "author": "worker-a",
            "kind": "finding",
            "title": "Stable parser IDs",
            "project": "parser",
            "topics": ["ids", "parser"],
            "evidence": "tests/test_parser.py",
            "basis": "independent",
        }))
        first_id = first["id"]
        request = json.loads(self.toolbox.execute("whiteboard_post", {
            "text": "Please verify the parser behavior.",
            "author": "worker-b",
            "kind": "request",
            "project": "parser",
            "status": "open",
            "reply_to": first_id,
        }))

        found = json.loads(self.toolbox.execute("whiteboard_read", {
            "query": "stable IDs", "project": "parser", "kind": "finding",
        }))
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["messages"][0]["id"], first_id)

        updated = json.loads(self.toolbox.execute("whiteboard_post", {
            "text": "Verification complete.",
            "author": "worker-b",
            "kind": "update",
            "reply_to": request["id"],
            "status": "resolved",
        }))
        thread = json.loads(self.toolbox.execute("whiteboard_read", {"thread": request["id"]}))
        self.assertEqual({message["id"] for message in thread["messages"]}, {request["id"], updated["id"]})

    def test_schema_exposes_structured_fields_and_read_only_still_blocks_post(self):
        definitions = {item["function"]["name"]: item["function"] for item in TOOL_DEFINITIONS}
        read = definitions["whiteboard_read"]["parameters"]["properties"]
        post = definitions["whiteboard_post"]["parameters"]["properties"]
        self.assertTrue({"query", "project", "topic", "kind", "status", "thread", "before"} <= read.keys())
        self.assertTrue({"title", "project", "topics", "evidence", "applies_to", "status", "reply_to", "expires_at", "basis"} <= post.keys())
        self.assertIn("expired", read["status"]["enum"])
        self.assertNotIn("expired", post["status"]["enum"])
        self.assertIn("older", definitions["whiteboard_read"]["description"])
        self.assertIn("untrusted", definitions["whiteboard_read"]["description"])
        self.assertIn("independent", definitions["whiteboard_post"]["description"])

        self.toolbox.config["read_only"] = True
        with patch("pilferedparrot.whiteboard.Whiteboard.read", return_value={"messages": []}):
            self.assertIn("messages", self.toolbox.execute("whiteboard_read", {}))
        with self.assertRaises(PermissionError):
            self.toolbox.execute("whiteboard_post", {"text": "blocked", "author": "chat"})

    def test_small_output_budget_keeps_whiteboard_json_valid(self):
        self.toolbox.output_limit = 180
        result = {"messages": [{"id": "n-1", "text": "x" * 200}], "count": 1,
                  "has_more": False, "next_before": None}
        with patch("pilferedparrot.whiteboard.Whiteboard.read", return_value=result):
            rendered = self.toolbox.execute("whiteboard_read", {})
        parsed = json.loads(rendered)
        self.assertIsInstance(parsed, dict)
        self.assertIn("error", parsed)
        self.assertIn("required_chars", parsed)
        self.assertIsNone(parsed["next_before"])

    def test_small_budget_keeps_newest_whole_notes_and_cursor(self):
        result = {"messages": [
            {"id": "old", "text": "a" * 20},
            {"id": "middle", "text": "b" * 20},
            {"id": "new", "text": "c" * 20},
        ], "count": 3, "has_more": False, "next_before": None}
        self.toolbox.output_limit = 170
        with patch("pilferedparrot.whiteboard.Whiteboard.read", return_value=result):
            parsed = json.loads(self.toolbox.execute("whiteboard_read", {}))
        self.assertEqual([message["id"] for message in parsed["messages"]], ["middle", "new"])
        self.assertEqual(parsed["next_before"], "middle")

    def test_truncated_post_receipt_marks_success_and_preserves_id(self):
        self.toolbox.output_limit = 40
        posted = {"id": "posted-note", "text": "x" * 200}
        with patch("pilferedparrot.whiteboard.Whiteboard.post", return_value=posted):
            parsed = json.loads(self.toolbox.execute("whiteboard_post", {"text": "x", "author": "worker"}))
        self.assertTrue(parsed["posted"])
        self.assertEqual(parsed["id"], "posted-note")


if __name__ == "__main__":
    unittest.main()
