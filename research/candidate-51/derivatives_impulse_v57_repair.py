#!/usr/bin/env python3
"""Gap-aware runner for the frozen v57 derivatives impulse anatomy.

Two development shards contain 19 exchange-archive minute gaps.  The shared
price adapter correctly rejects those ranges for executable backtests.  For
this mechanism diagnostic we retain only observed bars, record every missing
minute, discard incomplete hourly bars, suppress signals until the full
indicator lookback is contiguous again, and reject every forward path that
crosses a gap.  No price is synthesized and no v57 state threshold changes.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def _load_target():
    import importlib.util

    path = HERE / "derivatives_impulse_state.py"
    spec = importlib.util.spec_from_file_location("candidate51_v57_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _relaxed_price_loader(module: Any):
    def load_range(*, symbol, start, end, cache, output):
        if end < start:
            raise ValueError("end precedes start")
        output.mkdir(parents=True, exist_ok=True)
        frames, manifest_files, evidence = [], [], []
        day = start
        while day <= end:
            archive, checksum, item = module._base.download_checked(
                "klines", symbol, day, cache
            )
            frames.append(module._read_kline(archive))
            manifest_files.extend([archive, checksum])
            evidence.append(item)
            day += timedelta(days=1)

        klines = pd.concat(frames, ignore_index=True).sort_values("close_time_dt")
        if klines["close_time_dt"].duplicated().any():
            raise RuntimeError("duplicate klines across daily files")
        close_times = pd.DatetimeIndex(pd.to_datetime(klines["close_time_dt"], utc=True))
        expected_first = (
            pd.Timestamp(start, tz="UTC")
            + pd.Timedelta(minutes=1)
            - pd.Timedelta(milliseconds=1)
        )
        expected_last = (
            pd.Timestamp(end + timedelta(days=1), tz="UTC")
            - pd.Timedelta(milliseconds=1)
        )
        if close_times[0] != expected_first or close_times[-1] != expected_last:
            raise RuntimeError(
                f"unexpected boundary clock for {symbol}: "
                f"{close_times[0]}..{close_times[-1]}"
            )
        expected = pd.date_range(expected_first, expected_last, freq="min", tz="UTC")
        missing = expected.difference(close_times)
        observed_time_ns = pd.Series(close_times.asi8, dtype="int64")
        feature_path = output / "features.csv.gz"
        pd.DataFrame(
            {"observed_time_ns": observed_time_ns, "feature_ready": True}
        ).to_csv(feature_path, index=False, compression="gzip")
        (output / "raw_evidence.json").write_text(
            json.dumps([asdict(item) for item in evidence], indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        (output / "input_mode.json").write_text(
            json.dumps(
                {
                    "mode": "checksum-verified-observed-bars-gap-aware-diagnostic",
                    "symbol": symbol,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "rows": len(klines),
                    "expected_rows": len(expected),
                    "missing_count": len(missing),
                    "missing_close_times": [value.isoformat() for value in missing],
                    "gap_policy": [
                        "no price synthesis",
                        "incomplete hourly bars discarded",
                        "signals suppressed until contiguous indicator recovery",
                        "forward paths crossing gaps rejected",
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return (
            module.WritableWranglerFrame(klines.copy(deep=True)),
            feature_path,
            manifest_files,
            evidence,
        )

    return load_range


def _strict_path_returns(minute: pd.DataFrame, entry_time: pd.Timestamp, side: int):
    target = _TARGET
    times = pd.DatetimeIndex(pd.to_datetime(minute["close_time_dt"], utc=True))
    start = int(times.searchsorted(entry_time, side="right"))
    if start >= len(minute) or times[start] - entry_time != pd.Timedelta(minutes=1):
        return None
    entry = float(minute.iloc[start]["open"])
    if not np.isfinite(entry) or entry <= 0.0:
        return None
    result = {"entry_time": times[start], "entry_price": entry}
    for horizon in target.HORIZONS_MIN:
        expected_exit = times[start] + pd.Timedelta(minutes=horizon)
        end = int(times.searchsorted(expected_exit, side="left"))
        if end >= len(minute) or times[end] != expected_exit:
            result[f"cont_{horizon}m"] = None
            result[f"rev_{horizon}m"] = None
            continue
        segment = times[start : end + 1]
        if len(segment) != horizon + 1 or not np.all(np.diff(segment.asi8) == 60_000_000_000):
            result[f"cont_{horizon}m"] = None
            result[f"rev_{horizon}m"] = None
            continue
        exit_price = float(minute.iloc[end]["open"])
        gross = side * (exit_price / entry - 1.0)
        result[f"cont_{horizon}m"] = gross - target.COST_BPS / 10000.0
        result[f"rev_{horizon}m"] = -gross - target.COST_BPS / 10000.0

    expected_end = times[start] + pd.Timedelta(minutes=720)
    end = int(times.searchsorted(expected_end, side="right"))
    segment = times[start:end]
    if (
        len(segment) == 721
        and segment[-1] == expected_end
        and np.all(np.diff(segment.asi8) == 60_000_000_000)
    ):
        path = minute.iloc[start:end]
        favourable = (
            path["high"] / entry - 1.0
            if side > 0
            else 1.0 - path["low"] / entry
        )
        adverse = (
            path["low"] / entry - 1.0
            if side > 0
            else 1.0 - path["high"] / entry
        )
        result["mfe_12h"] = float(favourable.max())
        result["mae_12h"] = float(adverse.min())
    else:
        result["mfe_12h"] = None
        result["mae_12h"] = None
    return result


def _patch_loaded_module(path: Path, name: str):
    module = _ORIGINAL_LOAD_MODULE(path, name)
    if path.name == "kline_only_inputs.py":
        module.load_range = _relaxed_price_loader(module)
    elif path.name == "utbot_impulse_anatomy.py":
        original_signals = module._signals

        def gap_safe_signals(frame, params):
            signals = original_signals(frame, params)
            clocks = pd.DatetimeIndex(pd.to_datetime(frame["close_time"], utc=True))
            valid = np.ones(len(frame), dtype=bool)
            gap_positions = np.flatnonzero(
                np.r_[False, np.diff(clocks.asi8) != 3_600_000_000_000]
            )
            # Longest v57 source lookback is 63 hours; 72 is a conservative
            # recovery buffer and remains fixed before observing outcomes.
            for position in gap_positions:
                valid[position : min(len(valid), position + 72)] = False
            for column in signals.columns:
                if column.startswith("side__"):
                    signals.loc[~valid, column] = 0
            signals["contiguous_clock_ready"] = valid
            return signals

        module._signals = gap_safe_signals
    return module


_TARGET = _load_target()
_ORIGINAL_LOAD_MODULE = _TARGET._load_module
_TARGET._load_module = _patch_loaded_module
_TARGET._path_returns = _strict_path_returns

if __name__ == "__main__":
    _TARGET.main()
