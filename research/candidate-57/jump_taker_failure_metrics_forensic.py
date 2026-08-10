#!/usr/bin/env python3
"""Reuse the Binance metrics forensic on the fresh taker-filter boundaries."""
from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "jump_binance_metrics_forensic.py"
SPEC = importlib.util.spec_from_file_location(
    "candidate57_jump_taker_failure_metrics", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import metrics forensic: {SOURCE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MODULE.AUDIT = (
    HERE
    / "evidence"
    / "jump-taker-alignment-fresh-v1"
    / "source_without_taker_filter"
    / "episode_rows.json"
)
MODULE.OUT = HERE / "evidence" / "jump-taker-failure-metrics-forensic-v1"
MODULE.CACHE = Path(".cache/candidate-57-jump-taker-failure-metrics-forensic-v1")
MODULE.START = date(2026, 4, 1)
MODULE.END = date(2026, 4, 14)

if __name__ == "__main__":
    raise SystemExit(MODULE.main())
