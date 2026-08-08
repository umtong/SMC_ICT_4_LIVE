#!/usr/bin/env python3
"""Diagnose quarter-hour opening order flow with causal Binance observations.

This is an alpha-screening diagnostic, not a backtester.  It reuses Candidate
05's checksum-verified Binance data loader and measures whether the first-ten-
second order imbalance of each UTC quarter hour predicts later completed-bar
returns after the signal is actually observable at the minute close.

No realized future value enters a predictor.  The script evaluates a small set
of predeclared economic subsets:

* all quarter-hour openings;
* abnormal opening participation;
* causally large absolute imbalance;
* current imbalance aligned with the prior twelve quarter-hour imbalances;
* combinations of those conditions;
* the same combinations after an observed first-30-minute counter-move, which
  is a diagnostic for a later reset/re-entry policy rather than a signal known
  at the boundary.

Configured round-trip costs are subtracted only in the report.  A trading
candidate is implemented later only if the gross medium-horizon effect has
sufficient economic room.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"
sys.path.insert(0, str(CANDIDATE16))
sys.path.insert(1, str(CANDIDATE05))

from timestamp_contract import install as install_timestamp_contract
from wrangler_contract import install as install_wrangler_contract

install_timestamp_contract()
install_wrangler_contract()

from features import load_range


HORIZONS = (30, 60, 120, 240, 480, 720)


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _summarize(values: pd.Series, *, round_trip_bps: float) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {
            "n": 0,
            "mean_signed_bps": None,
            "median_signed_bps": None,
            "hit_rate": None,
            "mean_after_configured_round_trip_bps": None,
            "p10_signed_bps": None,
            "p90_signed_bps": None,
        }
    mean = float(clean.mean())
    return {
        "n": int(clean.size),
        "mean_signed_bps": mean,
        "median_signed_bps": float(clean.median()),
        "hit_rate": float((clean > 0.0).mean()),
        "mean_after_configured_round_trip_bps": mean - round_trip_bps,
        "p10_signed_bps": float(clean.quantile(0.10)),
        "p90_signed_bps": float(clean.quantile(0.90)),
    }


def build_diagnostic(
    *,
    symbol: str,
    build_start: date,
    build_end: date,
    evaluation_start: date,
    evaluation_end: date,
    cache: Path,
    output: Path,
    round_trip_bps: float,
) -> dict[str, Any]:
    if not build_start <= evaluation_start <= evaluation_end <= build_end:
        raise ValueError("evaluation range must be contained in build range")
    if build_end < evaluation_end + timedelta(days=1):
        raise ValueError("build_end must include at least one post-evaluation day")

    output.mkdir(parents=True, exist_ok=True)
    klines, feature_path, _, _ = load_range(
        symbol=symbol,
        start=build_start,
        end=build_end,
        cache=cache,
        output=output,
    )
    features = pd.read_csv(feature_path, compression="infer")
    price = klines[
        [
            "open_time_dt",
            "close_time_dt",
            "open",
            "high",
            "low",
            "close",
        ]
    ].copy()
    close_times = pd.to_datetime(price["close_time_dt"], utc=True)
    price["observed_time_ns"] = close_times.map(lambda stamp: int(stamp.value))
    frame = price.merge(features, on="observed_time_ns", how="inner", validate="one_to_one")
    frame = frame.sort_values("close_time_dt").reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("no aligned price/feature observations")

    close = pd.to_numeric(frame["close"], errors="raise").astype(float)
    for horizon in HORIZONS:
        frame[f"ret_{horizon}m_bps"] = np.log(close.shift(-horizon) / close) * 10_000.0
    for horizon in (240, 480, 720):
        frame[f"ret_30_to_{horizon}m_bps"] = (
            frame[f"ret_{horizon}m_bps"] - frame["ret_30m_bps"]
        )

    open_time = pd.to_datetime(frame["open_time_dt"], utc=True)
    frame["quarter_hour"] = open_time.dt.minute.mod(15).eq(0)
    qh = frame.loc[frame["quarter_hour"]].copy()
    qh["flow"] = pd.to_numeric(qh["flow_open_10s"], errors="coerce")
    qh["side"] = np.sign(qh["flow"])
    qh["abs_flow_reference"] = (
        qh["flow"].abs().shift(1).rolling(96, min_periods=32).median()
    )
    qh["prior12_pressure"] = qh["flow"].shift(1).rolling(12, min_periods=12).sum()
    qh["prior12_side"] = np.sign(qh["prior12_pressure"])
    qh["opening_participation"] = pd.to_numeric(
        qh["notional_open_10s_burst"],
        errors="coerce",
    )

    valid_signal = qh["side"].ne(0.0) & qh["feature_ready"].astype(bool)
    abnormal = qh["opening_participation"].gt(1.0)
    extreme = qh["flow"].abs().ge(qh["abs_flow_reference"])
    persistent = qh["prior12_side"].ne(0.0) & qh["side"].eq(qh["prior12_side"])

    qh["selector_all"] = valid_signal
    qh["selector_abnormal"] = valid_signal & abnormal
    qh["selector_extreme"] = valid_signal & extreme
    qh["selector_persistent"] = valid_signal & persistent
    qh["selector_persistent_abnormal"] = valid_signal & persistent & abnormal
    qh["selector_persistent_extreme_abnormal"] = (
        valid_signal & persistent & extreme & abnormal
    )

    for horizon in HORIZONS:
        qh[f"signed_{horizon}m_bps"] = qh["side"] * qh[f"ret_{horizon}m_bps"]
    for horizon in (240, 480, 720):
        qh[f"signed_30_to_{horizon}m_bps"] = (
            qh["side"] * qh[f"ret_30_to_{horizon}m_bps"]
        )
    qh["countermove_30m"] = qh["signed_30m_bps"].lt(0.0)

    evaluation_open = pd.to_datetime(evaluation_start, utc=True)
    evaluation_close = pd.to_datetime(evaluation_end + timedelta(days=1), utc=True)
    observed = pd.to_datetime(qh["close_time_dt"], utc=True)
    qh = qh.loc[(observed >= evaluation_open) & (observed < evaluation_close)].copy()

    selectors = [
        "all",
        "abnormal",
        "extreme",
        "persistent",
        "persistent_abnormal",
        "persistent_extreme_abnormal",
    ]
    report: dict[str, Any] = {}
    for selector in selectors:
        mask = qh[f"selector_{selector}"].fillna(False)
        subset = qh.loc[mask]
        horizons: dict[str, Any] = {}
        for horizon in HORIZONS:
            horizons[f"{horizon}m"] = _summarize(
                subset[f"signed_{horizon}m_bps"],
                round_trip_bps=round_trip_bps,
            )
        reset = subset.loc[subset["countermove_30m"]]
        reset_horizons = {
            f"30_to_{horizon}m": _summarize(
                reset[f"signed_30_to_{horizon}m_bps"],
                round_trip_bps=round_trip_bps,
            )
            for horizon in (240, 480, 720)
        }
        report[selector] = {
            "signals": int(subset.shape[0]),
            "countermove_30m_signals": int(reset.shape[0]),
            "countermove_30m_share": (
                None if subset.empty else float(reset.shape[0] / subset.shape[0])
            ),
            "from_signal_close": horizons,
            "after_observed_30m_countermove": reset_horizons,
        }

    columns = [
        "open_time_dt",
        "close_time_dt",
        "flow",
        "side",
        "opening_participation",
        "abs_flow_reference",
        "prior12_pressure",
        "prior12_side",
        "countermove_30m",
    ] + [f"selector_{name}" for name in selectors]
    columns += [f"signed_{horizon}m_bps" for horizon in HORIZONS]
    columns += [f"signed_30_to_{horizon}m_bps" for horizon in (240, 480, 720)]
    qh[columns].to_csv(output / "quarter_hour_observations.csv", index=False)

    result = {
        "candidate": "candidate-23-quarter-hour-flow-diagnostic",
        "symbol": symbol,
        "build_start": str(build_start),
        "build_end": str(build_end),
        "evaluation_start": str(evaluation_start),
        "evaluation_end": str(evaluation_end),
        "signal_observation": (
            "first-ten-second imbalance is acted on only after the containing "
            "one-minute bar closes"
        ),
        "round_trip_bps": round_trip_bps,
        "quarter_hour_rows": int(qh.shape[0]),
        "selectors": report,
    }
    (output / "diagnostic.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--round-trip-bps", type=float, default=20.0)
    args = parser.parse_args()
    result = build_diagnostic(
        symbol=args.symbol,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache=args.cache.resolve(),
        output=args.output.resolve(),
        round_trip_bps=args.round_trip_bps,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
