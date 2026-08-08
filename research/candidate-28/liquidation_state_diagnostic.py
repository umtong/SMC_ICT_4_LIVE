#!/usr/bin/env python3
"""Causal diagnostic for heterogeneous liquidation-state transitions.

This module is an alpha-screening diagnostic, not a backtester.  It reuses the
checksum-verified Candidate 05 feature pipeline including delayed Binance
positioning metrics, premium-index observations and displayed-liquidity data.
It owns no matching, orders, positions, account state or PnL.

The diagnostic separates three economic roles:

1. prior state: leverage expansion, crowd-side premium and flow-variance
   compression, all observed strictly before the shock minute;
2. shock: causal extreme return plus abnormal traded notional, aligned
   aggressor flow and displayed liquidity withdrawal in the shock direction;
3. transition five completed minutes later:
   - REVERSAL only when OI clears, the defending book refills, price responds
     against the shock and shock-direction flow loses control;
   - CONTINUATION only when OI has not cleared, liquidity continues to withdraw,
     price extends and flow remains aligned;
   - otherwise UNRESOLVED / NO TRADE.

Only the first qualifying shock in each two-hour refractory window is counted.
Entry proxies begin on the next completed minute after the five-minute state
transition.  Fixed-horizon returns are diagnostic only and have configured
round-trip cost subtracted; a NautilusTrader strategy is built only if one
causal route has repeatable economic room.
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
CANDIDATE05 = HERE.parent / "candidate-05"
sys.path.insert(0, str(CANDIDATE05))

from timestamp_contract import install as install_timestamp_contract
from wrangler_contract import install as install_wrangler_contract
from positioning_contract import install as install_positioning_contract
from basis_contract import install as install_basis_contract
from book_depth_gap_contract import install as install_book_depth_gap_contract

install_timestamp_contract()
install_wrangler_contract()
install_positioning_contract()
install_basis_contract()
install_book_depth_gap_contract()

from features import load_range


HORIZONS = (15, 30, 60, 120)
EPISODE_COLUMNS = [
    "shock_time",
    "confirm_time",
    "entry_time",
    "shock_direction",
    "prior_state",
    "route",
    "trade_side",
    "shock_return_bps",
    "shock_notional_burst",
    "shock_flow_60s",
    "shock_directional_depth_change_1m",
    "prior_oi_change_30m",
    "prior_premium_index",
    "prior_flow_std_60m",
    "prior_flow_std_reference",
    "confirmation_return_bps",
    "confirmation_flow_3m",
    "confirmation_oi_change_15m",
    "confirmation_oi_clear_cut",
    "confirmation_defending_depth_change_5m",
    "confirmation_directional_depth_change_5m",
    "entry_price_proxy",
    "structural_stop_proxy",
    "structural_risk_bps",
] + [f"gross_{horizon}m_bps" for horizon in HORIZONS] + [
    f"net_{horizon}m_bps" for horizon in HORIZONS
]


def _as_ns(values: pd.Series) -> pd.Series:
    return pd.Series(
        (int(pd.Timestamp(value).value) for value in values),
        index=values.index,
        dtype="int64",
    )


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
        ["open_time_dt", "close_time_dt", "open", "high", "low", "close"]
    ].copy()
    price["observed_time_ns"] = _as_ns(price["close_time_dt"])
    frame = price.merge(features, on="observed_time_ns", how="inner", validate="one_to_one")
    frame = frame.sort_values("close_time_dt").reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("no aligned price and causal feature rows")
    frame["time"] = pd.to_datetime(frame["close_time_dt"], utc=True)
    frame["row_index"] = np.arange(len(frame), dtype=np.int64)
    if frame["time"].duplicated().any() or not frame["time"].is_monotonic_increasing:
        raise RuntimeError("completed-minute observations must be unique and monotonic")

    close = pd.to_numeric(frame["close"], errors="raise").astype(float)
    frame["ret_1m_bps"] = np.log(close / close.shift(1)) * 10_000.0
    frame["abs_ret_1m_bps"] = frame["ret_1m_bps"].abs()
    frame["shock_return_cut"] = (
        frame["abs_ret_1m_bps"].shift(1).rolling(2_880, min_periods=1_440).quantile(0.99)
    )
    notional = pd.to_numeric(frame["notional_burst"], errors="coerce")
    frame["shock_notional_cut"] = (
        notional.shift(1).rolling(2_880, min_periods=1_440).quantile(0.95)
    )

    flow = pd.to_numeric(frame["flow_60s"], errors="coerce")
    flow_std = flow.shift(1).rolling(60, min_periods=45).std()
    frame["prior_flow_std_60m"] = flow_std
    frame["prior_flow_std_reference"] = (
        flow_std.shift(1).rolling(2_880, min_periods=1_440).median()
    )

    oi15 = pd.to_numeric(frame["oi_change_15m"], errors="coerce")
    frame["oi_clear_cut"] = (
        oi15.shift(1).rolling(2_880, min_periods=1_440).quantile(0.10)
    )
    return frame


def _prior_state(row: pd.Series, shock_direction: int) -> str:
    oi_expanded = float(row["oi_change_30m"]) > 0.0
    # A downside shock liquidates crowded longs (positive premium); an upside
    # shock liquidates crowded shorts (negative premium).
    crowd_side = (-shock_direction) * float(row["premium_index"]) > 0.0
    compressed = float(row["prior_flow_std_60m"]) <= float(
        row["prior_flow_std_reference"],
    )
    score = int(oi_expanded) + int(crowd_side) + int(compressed)
    if score == 3:
        return "ENDOGENOUS_BUILDUP"
    if score == 0:
        return "EXOGENOUS_SHOCK"
    return "MIXED_BUILDUP"


def _route_transition(
    *,
    shock: pd.Series,
    confirm: pd.Series,
    direction: int,
) -> tuple[str, dict[str, float | bool]]:
    shock_close = float(shock["close"])
    confirm_close = float(confirm["close"])
    confirmation_return_bps = direction * math.log(confirm_close / shock_close) * 10_000.0
    flow_3m = float(confirm["flow_3m"])
    oi_change_15m = float(confirm["oi_change_15m"])
    oi_clear_cut = float(confirm["oi_clear_cut"])
    if direction < 0:
        defending_depth = float(confirm["bid_depth_change_1_5m"])
        directional_depth = float(confirm["bid_depth_change_1_5m"])
    else:
        defending_depth = float(confirm["ask_depth_change_1_5m"])
        directional_depth = float(confirm["ask_depth_change_1_5m"])

    oi_cleared = oi_change_15m <= oi_clear_cut and oi_change_15m < 0.0
    book_refilled = defending_depth > 0.0
    price_reversed = confirmation_return_bps < 0.0
    flow_exhausted = direction * flow_3m <= 0.0

    oi_not_cleared = oi_change_15m >= 0.0
    liquidity_withdrawing = directional_depth < 0.0
    price_extended = confirmation_return_bps > 0.0
    flow_persistent = direction * flow_3m > 0.0

    if oi_cleared and book_refilled and price_reversed and flow_exhausted:
        route = "REVERSAL"
    elif oi_not_cleared and liquidity_withdrawing and price_extended and flow_persistent:
        route = "CONTINUATION"
    else:
        route = "UNRESOLVED"
    return route, {
        "confirmation_return_bps": confirmation_return_bps,
        "confirmation_flow_3m": flow_3m,
        "confirmation_oi_change_15m": oi_change_15m,
        "confirmation_oi_clear_cut": oi_clear_cut,
        "confirmation_defending_depth_change_5m": defending_depth,
        "confirmation_directional_depth_change_5m": directional_depth,
        "oi_cleared": oi_cleared,
        "book_refilled": book_refilled,
        "price_reversed": price_reversed,
        "flow_exhausted": flow_exhausted,
        "oi_not_cleared": oi_not_cleared,
        "liquidity_withdrawing": liquidity_withdrawing,
        "price_extended": price_extended,
        "flow_persistent": flow_persistent,
    }


def run_diagnostic(
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
        raise ValueError("evaluation must be contained in build range")
    if build_end < evaluation_end + timedelta(days=1):
        raise ValueError("build must include a post-evaluation horizon day")
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

    required = [
        "ret_1m_bps",
        "shock_return_cut",
        "notional_burst",
        "shock_notional_cut",
        "flow_60s",
        "oi_change_30m",
        "premium_index",
        "prior_flow_std_60m",
        "prior_flow_std_reference",
        "oi_change_15m",
        "oi_clear_cut",
    ]
    candidate = frame[required].notna().all(axis=1)
    candidate &= frame["feature_ready"].astype(bool)
    candidate &= frame["metrics_ready"].astype(bool)
    candidate &= frame["basis_ready"].astype(bool)
    candidate &= frame["abs_ret_1m_bps"].ge(frame["shock_return_cut"])
    candidate &= pd.to_numeric(frame["notional_burst"], errors="coerce").ge(
        frame["shock_notional_cut"],
    )

    episodes: list[dict[str, Any]] = []
    blocked_shocks = 0
    available_index = 0
    for shock_index in frame.index[candidate].tolist():
        shock_index = int(shock_index)
        if shock_index < available_index:
            blocked_shocks += 1
            continue
        shock = frame.loc[shock_index]
        if not evaluation_open <= shock["time"] < evaluation_close:
            continue
        direction = 1 if float(shock["ret_1m_bps"]) > 0.0 else -1
        flow_aligned = direction * float(shock["flow_60s"]) > 0.0
        depth_change_1m = (
            float(shock["ask_depth_change_1_1m"])
            if direction > 0
            else float(shock["bid_depth_change_1_1m"])
        )
        if not flow_aligned or not math.isfinite(depth_change_1m) or depth_change_1m >= 0.0:
            continue
        confirm_index = shock_index + 5
        entry_index = shock_index + 6
        if entry_index + max(HORIZONS) >= len(frame):
            continue
        confirm = frame.loc[confirm_index]
        if not bool(confirm["metrics_ready"]):
            continue
        route, transition = _route_transition(
            shock=shock,
            confirm=confirm,
            direction=direction,
        )
        available_index = shock_index + 120
        trade_side = -direction if route == "REVERSAL" else direction if route == "CONTINUATION" else 0
        entry = frame.loc[entry_index]
        entry_price = float(entry["close"])
        if route == "REVERSAL":
            stop = float(shock["high"] if direction > 0 else shock["low"])
        elif route == "CONTINUATION":
            window = frame.loc[shock_index:confirm_index]
            stop = float(window["low"].min() if direction > 0 else window["high"].max())
        else:
            stop = float("nan")
        risk_bps = (
            abs(math.log(entry_price / stop)) * 10_000.0
            if trade_side != 0 and entry_price > 0.0 and stop > 0.0
            else float("nan")
        )
        episode: dict[str, Any] = {
            "shock_time": shock["time"].isoformat(),
            "confirm_time": confirm["time"].isoformat(),
            "entry_time": entry["time"].isoformat(),
            "shock_direction": direction,
            "prior_state": _prior_state(shock, direction),
            "route": route,
            "trade_side": trade_side,
            "shock_return_bps": float(shock["ret_1m_bps"]),
            "shock_notional_burst": float(shock["notional_burst"]),
            "shock_flow_60s": float(shock["flow_60s"]),
            "shock_directional_depth_change_1m": depth_change_1m,
            "prior_oi_change_30m": float(shock["oi_change_30m"]),
            "prior_premium_index": float(shock["premium_index"]),
            "prior_flow_std_60m": float(shock["prior_flow_std_60m"]),
            "prior_flow_std_reference": float(shock["prior_flow_std_reference"]),
            **{key: float(value) if isinstance(value, (int, float, np.floating)) else value for key, value in transition.items() if key.startswith("confirmation_")},
            "entry_price_proxy": entry_price,
            "structural_stop_proxy": stop,
            "structural_risk_bps": risk_bps,
        }
        for horizon in HORIZONS:
            future = frame.loc[entry_index + horizon]
            gross = (
                trade_side * math.log(float(future["close"]) / entry_price) * 10_000.0
                if trade_side != 0
                else float("nan")
            )
            episode[f"gross_{horizon}m_bps"] = gross
            episode[f"net_{horizon}m_bps"] = gross - round_trip_bps if trade_side != 0 else float("nan")
        episodes.append(episode)

    episode_frame = pd.DataFrame(episodes, columns=EPISODE_COLUMNS)
    episode_frame.to_csv(output / "liquidation_state_episodes.csv", index=False)
    groups: dict[str, Any] = {}
    if not episode_frame.empty:
        for (state, route), group in episode_frame.groupby(["prior_state", "route"], sort=True):
            record: dict[str, Any] = {"episodes": int(len(group))}
            for horizon in HORIZONS:
                values = pd.to_numeric(group[f"net_{horizon}m_bps"], errors="coerce").dropna()
                record[f"{horizon}m"] = {
                    "n": int(len(values)),
                    "mean_net_bps": None if values.empty else float(values.mean()),
                    "median_net_bps": None if values.empty else float(values.median()),
                    "net_hit_rate": None if values.empty else float((values > 0.0).mean()),
                }
            groups[f"{state}|{route}"] = record
    result = {
        "candidate": "candidate-28-liquidation-state-diagnostic",
        "symbol": symbol,
        "build_start": str(build_start),
        "build_end": str(build_end),
        "evaluation_start": str(evaluation_start),
        "evaluation_end": str(evaluation_end),
        "round_trip_bps": round_trip_bps,
        "refractory_minutes": 120,
        "blocked_overlapping_shocks": blocked_shocks,
        "episodes": int(len(episode_frame)),
        "groups": groups,
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
    result = run_diagnostic(
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
