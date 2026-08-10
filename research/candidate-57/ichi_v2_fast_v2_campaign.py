#!/usr/bin/env python3
"""Run the frozen public ichiV2 tournament with verified finite history."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOURCE = HERE / "ichi_v2_5m_campaign.py"
SPEC = importlib.util.spec_from_file_location("candidate57_ichi_v2_fast_base", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {SOURCE}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)

BASE.WORK = ROOT / ".work" / "candidate-57-ichi-v2-fast-v2"
BASE.ARTIFACTS = ROOT / "artifacts" / "candidate-57-ichi-v2-fast-v2"
BASE.EVIDENCE = HERE / "evidence" / "ichi-v2-fast-v2"
BASE.CACHE = ROOT / ".cache" / "candidate-57-ichi-v2-fast-v2"


def main() -> int:
    status = int(BASE.main())
    comparison = BASE.EVIDENCE / "comparison.json"
    if comparison.is_file():
        payload = json.loads(comparison.read_text(encoding="utf-8"))
        payload["experiment"] = "candidate-57-ichi-v2-fast-v2"
        payload["execution_optimization"] = {
            "history_minutes": 1000,
            "source_timeframe_minutes": 5,
            "longest_ema_source_candles": 96,
            "exact_rolling_ichimoku_state_preserved": True,
            "full_vs_finite_identity_required_before_campaign": True,
            "decision_rules_changed": False,
        }
        comparison.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str)
            + "\n",
            encoding="utf-8",
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
