#!/usr/bin/env python3
"""Run the frozen private ichi5m reconstruction with finite history state."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOURCE = HERE / "ichi5m_private_recon_v1_campaign.py"
SPEC = importlib.util.spec_from_file_location("candidate57_ichi5m_private_v1_reused", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable campaign: {SOURCE}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)

BASE.WORK = ROOT / ".work" / "candidate-57-ichi5m-private-fast-v2"
BASE.ARTIFACTS = ROOT / "artifacts" / "candidate-57-ichi5m-private-fast-v2"
BASE.EVIDENCE = HERE / "evidence" / "ichi5m-private-fast-v2"
BASE.CACHE = ROOT / ".cache" / "candidate-57-ichi5m-private-fast-v2"


def main() -> int:
    status = int(BASE.main())
    path = BASE.EVIDENCE / "comparison.json"
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["experiment"] = "candidate-57-ichi5m-private-fast-v2"
        payload["execution_optimization"] = {
            "history_bars": 512,
            "source_timeframe_minutes": 1,
            "longest_ema_period": 96,
            "exact_rolling_state_preserved": True,
            "decision_rules_changed": False,
        }
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str)
            + "\n",
            encoding="utf-8",
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
