import importlib
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pilferedparrot.gpu_inventory import snapshot_gpus


class GpuInventoryTests(unittest.TestCase):
    def test_two_cards_preserve_rows_and_use_uuid_identity(self):
        result = SimpleNamespace(
            returncode=0,
            stdout='GPU-a, "NVIDIA GeForce RTX 5070 Ti", 16384, 2048, 14336, 12\n'
                   'GPU-b, "NVIDIA GeForce RTX 3060 Ti", 8192, 1024, 7168, 0\n',
            stderr="",
        )
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return result

        snapshot = snapshot_gpus(runner)
        self.assertTrue(snapshot["available"])
        self.assertEqual([gpu["uuid"] for gpu in snapshot["gpus"]], ["GPU-a", "GPU-b"])
        self.assertEqual(snapshot["gpus"][0]["name"], "NVIDIA GeForce RTX 5070 Ti")
        self.assertEqual(snapshot["gpus"][0]["memory_free_mib"], 14336)
        self.assertEqual(snapshot["gpus"][1]["utilization_percent"], 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("uuid,name,memory.total,memory.used,memory.free,utilization.gpu", calls[0][0][1])
        self.assertEqual(calls[0][1]["timeout"], 5.0)

    def test_malformed_output_returns_error(self):
        result = SimpleNamespace(returncode=0, stdout="GPU-a, card, nope, 0, 1, 0\n", stderr="")
        snapshot = snapshot_gpus(lambda *args, **kwargs: result)
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["gpus"], [])
        self.assertIn("could not parse", snapshot["error"])

    def test_missing_command_returns_unavailable(self):
        def runner(*args, **kwargs):
            raise FileNotFoundError()
        snapshot = snapshot_gpus(runner)
        self.assertFalse(snapshot["available"])
        self.assertIn("not installed", snapshot["error"])

    def test_timeout_returns_unavailable(self):
        def runner(*args, **kwargs):
            raise subprocess.TimeoutExpired("nvidia-smi", 5)
        snapshot = snapshot_gpus(runner, timeout_seconds=5)
        self.assertFalse(snapshot["available"])
        self.assertIn("timed out", snapshot["error"])

    def test_import_does_not_call_nvidia_smi(self):
        with patch("subprocess.run", side_effect=AssertionError("unexpected probe")) as run:
            sys.modules.pop("pilferedparrot.gpu_inventory", None)
            importlib.import_module("pilferedparrot.gpu_inventory")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
