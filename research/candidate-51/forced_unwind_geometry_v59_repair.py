#!/usr/bin/env python3
"""Compatibility runner for zero-event v59 periods.

A frozen chronological period with no FORCED_UNWIND_ACCEPTED episode is valid
negative opportunity evidence, not a workflow error.  Non-empty periods and the
aggregator delegate unchanged to ``forced_unwind_geometry.py``.  Empty periods
emit an explicit zero-record shard so all ten frozen periods remain represented.
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
    spec = importlib.util.spec_from_file_location("candidate51_v59_target", TARGET_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {TARGET_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _arg(name: str) -> str:
    flag = f"--{name.replace('_', '-')}"
    try:
        return sys.argv[sys.argv.index(flag) + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"missing required argument {flag}") from exc


def main() -> None:
    target = _load_target()
    if len(sys.argv) < 2 or sys.argv[1] != "run":
        target.main()
        return
    events_path = Path(_arg("events"))
    period_label = _arg("period_label")
    events = pd.read_csv(events_path)
    subset = events[
        events["period_label"].eq(period_label)
        & events["state"].eq("FORCED_UNWIND_ACCEPTED")
    ]
    if not subset.empty:
        target.main()
        return
    output = Path(_arg("output"))
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
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
        "source": {},
        "opportunity_evidence": "zero accepted forced-unwind episodes in frozen period",
        "records": [],
    }
    (output / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"period": period_label, "episodes": 0, "records": 0}, indent=2))


if __name__ == "__main__":
    main()
