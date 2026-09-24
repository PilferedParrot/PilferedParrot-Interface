from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pilferedparrot.skills import discover, discovery_status


class SkillDiscoveryTests(unittest.TestCase):
    def test_disabled_discovery_does_not_scan_configured_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SKILL.md").write_text("---\nname: Example\ndescription: Example skill\n---\nsecret", encoding="utf-8")
            self.assertEqual(discovery_status({"skills": {"roots": [str(root)]}}),
                             {"enabled": False, "roots": 1})
            self.assertEqual(discover({"skills": {"roots": [str(root)]}}), [])

    def test_preview_returns_frontmatter_only_and_one_level(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "SKILL.md").write_text(
                "---\nname: Root Skill\ndescription: 'Root description'\n---\nsecret instructions\n",
                encoding="utf-8",
            )
            child = root / "child"
            child.mkdir()
            (child / "SKILL.md").write_text(
                "---\nname: Child Skill\ndescription: Helps with tests\n---\nprivate body",
                encoding="utf-8",
            )
            nested = child / "nested"
            nested.mkdir()
            (nested / "SKILL.md").write_text(
                "---\nname: Too Deep\ndescription: Not discovered\n---\n",
                encoding="utf-8",
            )
            result = discover({"skills": {"enabled": True, "roots": [str(root)]}})
            self.assertEqual(result, [
                {"name": "Root Skill", "description": "Root description", "source": "Root 1"},
                {"name": "Child Skill", "description": "Helps with tests", "source": "Root 1 / child"},
            ])
            self.assertNotIn("secret instructions", str(result))

    def test_skips_symlink_children_and_oversized_or_invalid_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            root.mkdir()
            linked = base / "linked"
            linked.mkdir()
            (linked / "SKILL.md").write_text(
                "---\nname: Linked\ndescription: Must be skipped\n---\n", encoding="utf-8",
            )
            try:
                (root / "link").symlink_to(linked, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks unavailable")
            oversized = root / "large"
            oversized.mkdir()
            (oversized / "SKILL.md").write_bytes(
                b"---\nname: Large\ndescription: Too large\n---\n" + b"x" * 32768,
            )
            invalid = root / "invalid"
            invalid.mkdir()
            (invalid / "SKILL.md").write_bytes(b"\xff\xfe")
            result = discover({"skills": {"enabled": True, "roots": [str(root)]}})
            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
