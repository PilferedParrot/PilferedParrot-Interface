"""Read-only, task-time inventory of NVIDIA GPUs.

This module deliberately does not probe hardware during import. Call
``snapshot_gpus`` when a caller needs a point-in-time view.
"""

from __future__ import annotations

import csv
import io
import subprocess
from typing import Any, Callable


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "gpus": [], "error": reason}


def snapshot_gpus(
    runner: Callable[..., Any] = subprocess.run,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Return a single read-only ``nvidia-smi`` snapshot keyed by GPU UUID.

    ``available`` is false when the command cannot be run or its output cannot
    be parsed. GPU list order follows the command output; callers should use
    each GPU's UUID as its identity, never its list position or CUDA index.
    Memory values are MiB and utilization is a percentage (or ``None`` when
    NVIDIA reports it as unsupported).
    """
    command = [
        "nvidia-smi",
        "--query-gpu=uuid,name,memory.total,memory.used,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = runner(
            command, capture_output=True, text=True, check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return _unavailable("nvidia-smi is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return _unavailable(f"nvidia-smi timed out after {timeout_seconds:g} seconds")
    except OSError as exc:
        return _unavailable(f"could not run nvidia-smi: {exc}")

    if completed.returncode != 0:
        detail = (getattr(completed, "stderr", "") or "").strip()
        return _unavailable(detail or f"nvidia-smi exited with status {completed.returncode}")

    output = getattr(completed, "stdout", "")
    if not isinstance(output, str):
        return _unavailable("nvidia-smi returned non-text output")
    gpus: list[dict[str, Any]] = []
    try:
        for row in csv.reader(io.StringIO(output), skipinitialspace=True):
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) != 6:
                raise ValueError("expected six CSV fields per GPU")
            uuid, name, total, used, free, utilization = (value.strip() for value in row)
            if not uuid or not name:
                raise ValueError("GPU UUID and name must be present")
            memory_total = int(total)
            memory_used = int(used)
            memory_free = int(free)
            if min(memory_total, memory_used, memory_free) < 0:
                raise ValueError("memory values must be non-negative")
            if memory_used + memory_free > memory_total:
                raise ValueError("used plus free memory exceeds total memory")
            utilization_value = None if utilization in {"", "N/A", "[Not Supported]"} else int(utilization)
            if utilization_value is not None and not 0 <= utilization_value <= 100:
                raise ValueError("GPU utilization must be between 0 and 100")
            gpus.append({
                "uuid": uuid,
                "name": name,
                "memory_total_mib": memory_total,
                "memory_used_mib": memory_used,
                "memory_free_mib": memory_free,
                "utilization_percent": utilization_value,
            })
    except (ValueError, csv.Error) as exc:
        return _unavailable(f"could not parse nvidia-smi output: {exc}")
    return {"available": True, "gpus": gpus, "error": None}
