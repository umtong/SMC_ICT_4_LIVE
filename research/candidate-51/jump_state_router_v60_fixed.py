#!/usr/bin/env python3
"""Run v60 with resolution-independent contiguous forward paths.

The v60 event/state router produced valid events but inherited v57's diagnostic
path check that compared ``DatetimeIndex.asi8`` to a nanosecond constant.
With pandas 3 microsecond-resolution archives this set every horizon return to
None.  This wrapper changes only the continuity representation; signal, state,
entry, cost, horizon, routing and arbitration contracts remain frozen.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
TARGET_PATH = HERE / "jump_state_router_anatomy.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _contiguous(times: pd.DatetimeIndex) -> bool:
    return len(times) <= 1 or bool(((times[1:] - times[:-1]) == pd.Timedelta(minutes=1)).all())


def _fixed_path_returns(target: Any, minute: pd.DataFrame, entry_time: pd.Timestamp, side: int):
    times = pd.DatetimeIndex(pd.to_datetime(minute["close_time_dt"], utc=True))
    start = int(times.searchsorted(entry_time, side="right"))
    if start >= len(minute) or times[start] - entry_time != pd.Timedelta(minutes=1):
        return None
    entry = float(minute.iloc[start]["open"])
    if not math.isfinite(entry) or entry <= 0.0:
        return None
    result: dict[str, Any] = {"entry_time": times[start], "entry_price": entry}
    for horizon in target.HORIZONS_MIN:
        expected_exit = times[start] + pd.Timedelta(minutes=horizon)
        end = int(times.searchsorted(expected_exit, side="left"))
        if end >= len(minute) or times[end] != expected_exit:
            result[f"cont_{horizon}m"] = None
            result[f"rev_{horizon}m"] = None
            continue
        segment = times[start:end + 1]
        if len(segment) != horizon + 1 or not _contiguous(segment):
            result[f"cont_{horizon}m"] = None
            result[f"rev_{horizon}m"] = None
            continue
        exit_price = float(minute.iloc[end]["open"])
        gross = side * (exit_price / entry - 1.0)
        result[f"cont_{horizon}m"] = gross - target.COST_BPS / 10_000.0
        result[f"rev_{horizon}m"] = -gross - target.COST_BPS / 10_000.0
    return result


def main() -> None:
    router = _load(TARGET_PATH, "candidate51_v60_fixed_target")
    original_load = router._load

    def patched_load(path: Path, name: str):
        module = original_load(path, name)
        if path.name == "derivatives_impulse_v57_repair.py":
            target = module._TARGET
            target._path_returns = lambda minute, entry_time, side: _fixed_path_returns(
                target, minute, entry_time, side
            )
        return module

    router._load = patched_load
    router.main()


if __name__ == "__main__":
    main()
