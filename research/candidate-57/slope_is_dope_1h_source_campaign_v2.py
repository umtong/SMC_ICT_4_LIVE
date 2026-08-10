#!/usr/bin/env python3
"""Run the frozen Slope-is-Dope campaign after the ROI-order mechanical repair."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOURCE = HERE / "slope_is_dope_1h_source_campaign.py"
SPEC = importlib.util.spec_from_file_location("candidate57_slope_campaign_v1_reused", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import reusable campaign: {SOURCE}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)

BASE.WORK = ROOT / ".work" / "candidate-57-slope-is-dope-1h-roi-fix-v2"
BASE.ARTIFACTS = ROOT / "artifacts" / "candidate-57-slope-is-dope-1h-roi-fix-v2"
BASE.EVIDENCE = HERE / "evidence" / "slope-is-dope-1h-roi-fix-v2"
BASE.CACHE = ROOT / ".cache" / "candidate-57-slope-is-dope-1h-roi-fix-v2"
BASE.FREEZE = HERE / "SLOPE_IS_DOPE_1H_ROI_FIX_V2_FREEZE.md"


def main() -> int:
    status = int(BASE.main())
    comparison_path = BASE.EVIDENCE / "comparison.json"
    if comparison_path.is_file():
        payload = json.loads(comparison_path.read_text(encoding="utf-8"))
        payload["experiment"] = "candidate-57-slope-is-dope-1h-roi-fix-v2"
        payload["mechanical_repair"] = {
            "v1_bug": "ROI schedule sorted descending while lookup expects ascending",
            "v1_effect": "elapsed minute zero selected terminal zero ROI",
            "v2_change": "ascending time schedule only",
            "signals_changed": False,
            "risk_geometry_changed": False,
            "source_exit_changed": False,
        }
        comparison_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str)
            + "\n",
            encoding="utf-8",
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
