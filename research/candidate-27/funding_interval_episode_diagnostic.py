#!/usr/bin/env python3
"""Causal independent-episode diagnostic for state-conditioned QH flow.

This is not a backtester.  It reuses the checksum-verified Candidate 05 data
pipeline and asks one high-information question before a Nautilus strategy is
created:

    Does the apparent quarter-hour effect survive when overlapping signals are
    reduced to at most one position opportunity per eight-hour funding window?

For each UTC funding interval (00:00-08:00, 08:00-16:00, 16:00-24:00), the
first qualifying quarter-hour event is selected.  Qualification uses only
information observable by the event minute close:

* above-baseline first-ten-second participation;
* current imbalance aligned with the prior twelve quarter-hour imbalances;
* a predeclared prior-minute L2 liquidity state, discretized against strictly
  prior rolling terciles.

Exactly thirty later completed bars must first close against the seed side.
The executable episode begins on the following completed minute, matching the
shared bar/latency clock, and ends on the minute after the inherited 07:45,
15:45 or 23:45 funding-flatten decision.  No episode crosses funding and no
second signal from the same interval is counted.

The report measures time-exit return, MFE/MAE and two causal invalidation
geometries without simulating account PnL:

* prior interval extreme;
* one additional full pre-entry interval-range extension.

A full NautilusTrader candidate is built only if the independent episodes
retain material cost-after room.
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


FUNDING_HOURS = (0, 8, 16, 24)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _funding_interval_start(timestamp: pd.Timestamp) -> pd.Timestamp:
    day = timestamp.normalize()
    hour = int(timestamp.hour)
    start_hour = 16 if hour >= 16 else 8 if hour >= 8 else 0
    return day + pd.Timedelta(hours=start_hour)


def _next_funding(timestamp: pd.Timestamp) -> pd.Timestamp:
    start = _funding_interval_start(timestamp)
    return start + pd.Timedelta(hours=8)


def _build_frame(
    *,
    symbol: str,
    build_start: date,
    build_end: date,
    cache: Path,
    output: Path,
) -> pd.DataFrame:
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
    frame = frame.sort_values("open_time_dt").reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("no aligned price and feature observations")

    frame["minute"] = pd.to_datetime(frame["open_time_dt"], utc=True).dt.floor("min")
    if frame["minute"].duplicated().any() or not frame["minute"].is_monotonic_increasing:
        raise RuntimeError("minute observations must be unique and monotonic")
    frame["row_index"] = np.arange(len(frame), dtype=np.int64)

    withdrawal = -(
        pd.to_numeric(frame["bid_depth_change_1_5m"], errors="coerce")
        + pd.to_numeric(frame["ask_depth_change_1_5m"], errors="coerce")
    ) / 2.0
    abs_imbalance = pd.to_numeric(frame["depth_imbalance_1"], errors="coerce").abs()
    frame["prior_withdrawal"] = withdrawal.shift(1)
    frame["prior_abs_imbalance"] = abs_imbalance.shift(1)
    frame["withdrawal_cut"] = (
        withdrawal.shift(2).rolling(2_879, min_periods=1_440).quantile(2.0 / 3.0)
    )
    frame["abs_imbalance_cut"] = (
        abs_imbalance.shift(2).rolling(2_879, min_periods=1_440).quantile(2.0 / 3.0)
    )
    unavailable = frame["withdrawal_cut"].isna() | frame["abs_imbalance_cut"].isna()
    severe = (
        frame["prior_withdrawal"].gt(frame["withdrawal_cut"]).astype(int)
        + frame["prior_abs_imbalance"].gt(frame["abs_imbalance_cut"]).astype(int)
    )
    frame["liquidity_state"] = np.where(
        unavailable,
        "UNAVAILABLE",
        np.where(severe.eq(0), "CALM", np.where(severe.eq(1), "MIXED", "STRESSED")),
    )

    is_qh = frame["minute"].dt.minute.mod(15).eq(0)
    qh_index = frame.index[is_qh]
    qh_flow = pd.to_numeric(frame.loc[qh_index, "flow_open_10s"], errors="coerce")
    prior_pressure = qh_flow.shift(1).rolling(12, min_periods=12).sum()
    frame["qh_side"] = 0
    frame.loc[qh_index, "qh_side"] = np.sign(qh_flow).astype(int)
    frame["qh_prior12_side"] = 0
    frame.loc[qh_index, "qh_prior12_side"] = np.sign(prior_pressure).fillna(0).astype(int)
    frame["qh_persistent"] = False
    frame.loc[qh_index, "qh_persistent"] = (
        frame.loc[qh_index, "qh_side"].ne(0)
        & frame.loc[qh_index, "qh_side"].eq(frame.loc[qh_index, "qh_prior12_side"])
    )
    frame["qh_abnormal"] = False
    frame.loc[qh_index, "qh_abnormal"] = pd.to_numeric(
        frame.loc[qh_index, "notional_open_10s_burst"],
        errors="coerce",
    ).gt(1.0)
    frame["is_qh"] = is_qh
    return frame


def _episode_from_seed(
    *,
    frame: pd.DataFrame,
    seed_index: int,
    interval_start: pd.Timestamp,
    next_funding: pd.Timestamp,
    configured_round_trip_bps: float,
) -> dict[str, Any] | None:
    reset_index = seed_index + 30
    entry_index = seed_index + 31
    exit_minute = next_funding - pd.Timedelta(minutes=14)
    exit_matches = frame.index[frame["minute"].eq(exit_minute)]
    if reset_index >= len(frame) or entry_index >= len(frame) or len(exit_matches) != 1:
        return None
    exit_index = int(exit_matches[0])
    if entry_index >= exit_index:
        return None

    seed = frame.iloc[seed_index]
    reset = frame.iloc[reset_index]
    entry = frame.iloc[entry_index]
    exit_row = frame.iloc[exit_index]
    side = int(seed["qh_side"])
    if side not in (-1, 1):
        return None
    seed_close = float(seed["close"])
    reset_close = float(reset["close"])
    if side * (reset_close - seed_close) >= 0.0:
        return None

    entry_price = float(entry["close"])
    exit_price = float(exit_row["close"])
    path = frame.iloc[entry_index : exit_index + 1]
    interval_path = frame[
        frame["minute"].between(interval_start, frame.iloc[entry_index]["minute"], inclusive="both")
    ]
    if path.empty or interval_path.empty or entry_price <= 0.0 or exit_price <= 0.0:
        return None

    gross_bps = side * math.log(exit_price / entry_price) * 10_000.0
    if side > 0:
        mfe_bps = math.log(float(path["high"].max()) / entry_price) * 10_000.0
        mae_bps = math.log(float(path["low"].min()) / entry_price) * 10_000.0
        interval_opposite = float(interval_path["low"].min())
        interval_width = float(interval_path["high"].max() - interval_opposite)
        extreme_stop = interval_opposite
        extension_stop = interval_opposite - interval_width
        extreme_hit = float(path["low"].min()) <= extreme_stop
        extension_hit = float(path["low"].min()) <= extension_stop
        extreme_risk_bps = math.log(entry_price / extreme_stop) * 10_000.0
        extension_risk_bps = math.log(entry_price / extension_stop) * 10_000.0
    else:
        mfe_bps = math.log(entry_price / float(path["low"].min())) * 10_000.0
        mae_bps = math.log(entry_price / float(path["high"].max())) * 10_000.0
        interval_opposite = float(interval_path["high"].max())
        interval_width = float(interval_opposite - interval_path["low"].min())
        extreme_stop = interval_opposite
        extension_stop = interval_opposite + interval_width
        extreme_hit = float(path["high"].max()) >= extreme_stop
        extension_hit = float(path["high"].max()) >= extension_stop
        extreme_risk_bps = math.log(extreme_stop / entry_price) * 10_000.0
        extension_risk_bps = math.log(extension_stop / entry_price) * 10_000.0

    return {
        "interval_start": interval_start.isoformat(),
        "next_funding": next_funding.isoformat(),
        "seed_time": seed["minute"].isoformat(),
        "reset_time": reset["minute"].isoformat(),
        "entry_time": entry["minute"].isoformat(),
        "exit_time": exit_row["minute"].isoformat(),
        "side": side,
        "seed_flow_open_10s": float(seed["flow_open_10s"]),
        "seed_opening_participation_burst": float(seed["notional_open_10s_burst"]),
        "seed_prior12_side": int(seed["qh_prior12_side"]),
        "liquidity_state": str(seed["liquidity_state"]),
        "seed_close": seed_close,
        "reset_close": reset_close,
        "entry_price_proxy": entry_price,
        "exit_price_proxy": exit_price,
        "signed_reset_bps": side * math.log(reset_close / seed_close) * 10_000.0,
        "gross_time_exit_bps": gross_bps,
        "net_time_exit_bps": gross_bps - configured_round_trip_bps,
        "mfe_bps": mfe_bps,
        "mae_bps": mae_bps,
        "prior_interval_extreme_stop": extreme_stop,
        "prior_interval_extreme_risk_bps": extreme_risk_bps,
        "prior_interval_extreme_hit": bool(extreme_hit),
        "full_interval_extension_stop": extension_stop,
        "full_interval_extension_risk_bps": extension_risk_bps,
        "full_interval_extension_hit": bool(extension_hit),
        "path_minutes": int(exit_index - entry_index + 1),
    }


def run_diagnostic(
    *,
    symbol: str,
    required_state: str,
    build_start: date,
    build_end: date,
    evaluation_start: date,
    evaluation_end: date,
    cache: Path,
    output: Path,
    configured_round_trip_bps: float,
) -> dict[str, Any]:
    if required_state not in {"CALM", "MIXED", "STRESSED"}:
        raise ValueError("required_state must be CALM, MIXED or STRESSED")
    if not build_start <= evaluation_start <= evaluation_end <= build_end:
        raise ValueError("evaluation must be contained in build range")
    if build_end < evaluation_end + timedelta(days=1):
        raise ValueError("build must include the post-evaluation exit day")

    output.mkdir(parents=True, exist_ok=True)
    frame = _build_frame(
        symbol=symbol,
        build_start=build_start,
        build_end=build_end,
        cache=cache,
        output=output,
    )
    evaluation_open = pd.Timestamp(evaluation_start, tz="UTC")
    evaluation_close = pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")

    episodes: list[dict[str, Any]] = []
    interval = evaluation_open.floor("D")
    if interval.hour not in (0, 8, 16):
        interval = _funding_interval_start(interval)
    while interval < evaluation_close:
        next_funding = interval + pd.Timedelta(hours=8)
        candidates = frame[
            frame["minute"].between(
                interval + pd.Timedelta(minutes=15),
                next_funding - pd.Timedelta(minutes=60),
                inclusive="both",
            )
            & frame["is_qh"]
            & frame["feature_ready"].astype(bool)
            & frame["qh_abnormal"].astype(bool)
            & frame["qh_persistent"].astype(bool)
            & frame["liquidity_state"].eq(required_state)
        ]
        for seed_index in candidates.index:
            episode = _episode_from_seed(
                frame=frame,
                seed_index=int(seed_index),
                interval_start=interval,
                next_funding=next_funding,
                configured_round_trip_bps=configured_round_trip_bps,
            )
            if episode is not None:
                episodes.append(episode)
                break
        interval = next_funding

    episode_frame = pd.DataFrame(episodes)
    episode_frame.to_csv(output / "funding_interval_episodes.csv", index=False)
    if episode_frame.empty:
        summary = {
            "episodes": 0,
            "mean_gross_bps": None,
            "mean_net_bps": None,
            "median_net_bps": None,
            "net_hit_rate": None,
            "mean_mfe_bps": None,
            "mean_mae_bps": None,
            "prior_interval_extreme_hit_rate": None,
            "full_interval_extension_hit_rate": None,
        }
    else:
        summary = {
            "episodes": int(len(episode_frame)),
            "mean_gross_bps": float(episode_frame["gross_time_exit_bps"].mean()),
            "mean_net_bps": float(episode_frame["net_time_exit_bps"].mean()),
            "median_net_bps": float(episode_frame["net_time_exit_bps"].median()),
            "net_hit_rate": float((episode_frame["net_time_exit_bps"] > 0.0).mean()),
            "mean_mfe_bps": float(episode_frame["mfe_bps"].mean()),
            "mean_mae_bps": float(episode_frame["mae_bps"].mean()),
            "prior_interval_extreme_hit_rate": float(
                episode_frame["prior_interval_extreme_hit"].astype(bool).mean()
            ),
            "full_interval_extension_hit_rate": float(
                episode_frame["full_interval_extension_hit"].astype(bool).mean()
            ),
            "mean_prior_interval_extreme_risk_bps": float(
                episode_frame["prior_interval_extreme_risk_bps"].mean()
            ),
            "mean_full_interval_extension_risk_bps": float(
                episode_frame["full_interval_extension_risk_bps"].mean()
            ),
        }
    result = {
        "candidate": "candidate-27-independent-funding-interval-diagnostic",
        "symbol": symbol,
        "required_state": required_state,
        "build_start": str(build_start),
        "build_end": str(build_end),
        "evaluation_start": str(evaluation_start),
        "evaluation_end": str(evaluation_end),
        "configured_round_trip_bps": configured_round_trip_bps,
        "episode_policy": (
            "first persistent abnormal quarter-hour event in required prior L2 "
            "state, after a strictly adverse thirty-minute reset, at most one "
            "episode per eight-hour funding interval"
        ),
        "summary": summary,
    }
    (output / "diagnostic.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--required-state", required=True)
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--round-trip-bps", type=float, default=20.0)
    args = parser.parse_args()
    result = run_diagnostic(
        symbol=args.symbol,
        required_state=args.required_state,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache=args.cache.resolve(),
        output=args.output.resolve(),
        configured_round_trip_bps=args.round_trip_bps,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
