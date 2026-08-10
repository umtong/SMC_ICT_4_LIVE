#!/usr/bin/env python3
"""Reuse the frozen OI-lifecycle diagnostic on the consumed June jump interval."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOURCE = HERE / "jump_oi_lifecycle_development.py"
SPEC = importlib.util.spec_from_file_location(
    "candidate57_jump_oi_lifecycle_june_development", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable OI diagnostic: {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MODULE.ROWS_PATH = (
    HERE
    / "evidence"
    / "jump-state-arbitration-fresh-v1"
    / "source_max_z__no_taker"
    / "episode_rows.json"
)
MODULE.WORK = ROOT / ".work" / "candidate-57-jump-oi-lifecycle-june-development-v1"
MODULE.CACHE = ROOT / ".cache" / "candidate-57-jump-oi-lifecycle-june-development-v1"
MODULE.METRICS = MODULE.WORK / "binance_metrics_2026-06-11_2026-06-28.json"
MODULE.OUT = HERE / "evidence" / "jump-oi-lifecycle-june-development-v1"


def download() -> int:
    command = [
        sys.executable,
        str(HERE / "download_binance_metrics_sidecar.py"),
        "--start",
        "2026-06-11",
        "--end",
        "2026-06-28",
        "--output",
        str(MODULE.METRICS),
        "--cache",
        str(MODULE.CACHE / "metrics"),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


MODULE.download = download

if __name__ == "__main__":
    raise SystemExit(MODULE.main())
