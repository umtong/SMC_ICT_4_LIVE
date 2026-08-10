#!/usr/bin/env python3
"""Run v59 with resolution-independent clock continuity and zero-event shards.

pandas 3 preserves Binance UTC timestamps at microsecond resolution in these
archives.  The frozen v59 logic incorrectly compared ``DatetimeIndex.asi8`` to
a nanosecond constant, so every genuine 60-second path was rejected.  This
wrapper changes only that implementation check: adjacent timestamps must differ
by exactly one pandas Timedelta minute, regardless of storage resolution.
Zero-event periods remain explicit negative opportunity evidence.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
TARGET_PATH = HERE / "forced_unwind_geometry.py"


def _load_target():
    spec = importlib.util.spec_from_file_location("candidate51_v59_fixed_target", TARGET_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {TARGET_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _contiguous(times: pd.DatetimeIndex) -> bool:
    if len(times) <= 1:
        return True
    return bool(((times[1:] - times[:-1]) == pd.Timedelta(minutes=1)).all())


def _arg(name: str, default: str | None = None) -> str:
    flag = f"--{name.replace('_', '-')}"
    try:
        return sys.argv[sys.argv.index(flag) + 1]
    except (ValueError, IndexError):
        if default is not None:
            return default
        raise RuntimeError(f"missing required argument {flag}")


def _emit_zero_period(target) -> bool:
    if len(sys.argv) < 2 or sys.argv[1] != "run":
        return False
    events_path = Path(_arg("events", "research/candidate-51/evidence/derivatives-impulse-v57/EVENTS.csv"))
    period_label = _arg("period_label")
    events = pd.read_csv(events_path)
    subset = events[
        events["period_label"].eq(period_label)
        & events["state"].eq("FORCED_UNWIND_ACCEPTED")
    ]
    if not subset.empty:
        return False
    output = Path(_arg("output"))
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "period_label": period_label,
        "split": _arg("split"),
        "start": _arg("start"),
        "end": _arg("end"),
        "round_trip_cost_fraction": target.ROUND_TRIP_COST,
        "state": "FORCED_UNWIND_ACCEPTED",
        "frozen_geometry": {
            "entry_modes": list(target.ENTRY_MODES),
            "stop_modes": list(target.STOP_MODES),
            "target_modes": list(target.TARGET_MODES),
            "holds_min": list(target.HOLDS_MIN),
            "same_bar_ambiguity": "stop_first",
        },
        "clock_fix": "resolution-independent exact one-minute Timedelta comparison",
        "opportunity_evidence": "zero accepted forced-unwind episodes in frozen period",
        "source": {},
        "records": [],
    }
    (output / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"period": period_label, "episodes": 0, "records": 0}, indent=2))
    return True


def main() -> None:
    target = _load_target()
    target._contiguous = _contiguous
    if _emit_zero_period(target):
        return
    target.main()


if __name__ == "__main__":
    main()
