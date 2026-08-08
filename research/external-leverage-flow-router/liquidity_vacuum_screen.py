#!/usr/bin/env python3
"""Fixed one-minute liquidity-vacuum continuation screen.

Reuse the project's checksum-verified Binance futures klines, aggTrades, and
bookDepth feature builder plus the causal spot-participation wrapper.  The only
new logic is the trading scenario:

    completed prior-hour balance
    -> price breaks the balance on persistent, price-efficient futures flow
    -> the opposing one-percent book is being cancelled while deeper book
       imbalance points in the same direction
    -> spot price and aggressive flow independently confirm
    -> enter at the next minute open
    -> invalidate beyond the completed parent minute
    -> target one prior-hour range width beyond the broken boundary

The parent has a one-hour cooldown.  Entry is strictly later.  Geometry must
clear a fixed 20 bp round-trip hurdle and net reward/risk >= 1.0.  Same-minute
stop/target ambiguity is resolved against the strategy.  This is an economic
screen; a passing family still requires NautilusTrader actual-fill and
continuous-account validation.
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
sys.path.insert(0, str(HERE))

import features
import spot_participation_contract
from cross_asset_transfer_screen_fixed import robust_read_kline


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BALANCE_BARS = 60
BASELINE_BARS = 120
MAX_HOLD_BARS = 60
PARENT_COOLDOWN_BARS = 60
ROUND_TRIP_HURDLE_BPS = 20.0
MIN_NET_RR = 1.0
MIN_BALANCE_WIDTH_ATR = 3.0
MIN_BREAK_ATR = 0.05
MIN_FLOW_60S = 0.15
MIN_FLOW_3M = 0.08
MIN_SPOT_FLOW_60S = 0.08
MIN_SPOT_FLOW_3M = 0.04
MIN_NOTIONAL_BURST = 1.50
MIN_EFFICIENCY = 0.45
MIN_SPOT_EFFICIENCY = 0.30
MIN_DEPTH_IMBALANCE_1 = 0.15
MIN_DEPTH_IMBALANCE_2 = 0.05
MIN_OPPOSING_CANCEL_1M = 0.10
MIN_OPPOSING_CANCEL_5M = 0.15
MAX_SAME_SIDE_CANCEL_1M = 0.10
MIN_SPOT_RETURN_BPS = 1.0
STOP_BUFFER_ATR = 0.05


def _datetime_ns(values: pd.Series | pd.Index) -> np.ndarray:
    return (
        pd.DatetimeIndex(pd.to_datetime(values, utc=True))
        .to_numpy(dtype="datetime64[ns]")
        .astype("int64")
    )


def load_frame(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> pd.DataFrame:
    # Pandas 3 requires explicitly numeric timestamps for unit-based parsing.
    features.read_kline = robust_read_kline
    spot_participation_contract.install()
    klines, feature_path, _raw_files, _evidence = features.load_range(
        symbol=symbol,
        start=start - timedelta(days=1),
        end=end,
        cache=cache,
        output=output / "feature_contract",
    )
    observations = pd.read_csv(feature_path, compression="gzip")
    observations["observed_time_ns"] = pd.to_numeric(
        observations["observed_time_ns"], errors="raise",
    ).astype("int64")
    bars = klines.copy()
    bars["observed_time_ns"] = _datetime_ns(bars["close_time_dt"])
    keep = [
        "observed_time_ns",
        "open_time_dt",
        "open",
        "high",
        "low",
        "close",
    ]
    frame = observations.merge(
        bars[keep], on="observed_time_ns", how="inner", validate="one_to_one",
    )
    frame = frame.sort_values("observed_time_ns").reset_index(drop=True)
    frame["open_time_dt"] = pd.to_datetime(frame["open_time_dt"], utc=True)
    prior_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prior_close).abs(),
            (frame["low"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr"] = true_range.shift(1).rolling(
        BASELINE_BARS, min_periods=BALANCE_BARS,
    ).median()
    frame["prior_high"] = frame["high"].shift(1).rolling(
        BALANCE_BARS, min_periods=BALANCE_BARS,
    ).max()
    frame["prior_low"] = frame["low"].shift(1).rolling(
        BALANCE_BARS, min_periods=BALANCE_BARS,
    ).min()
    return frame


def as_bool(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes"}


def parent_direction(row: pd.Series) -> int:
    if not as_bool(row["feature_ready"]):
        return 0
    atr = float(row["atr"])
    width = float(row["prior_high"] - row["prior_low"])
    if not (
        math.isfinite(atr)
        and atr > 0.0
        and width >= MIN_BALANCE_WIDTH_ATR * atr
        and row["notional_burst"] >= MIN_NOTIONAL_BURST
        and row["efficiency_60s"] >= MIN_EFFICIENCY
        and row["spot_efficiency_60s"] >= MIN_SPOT_EFFICIENCY
    ):
        return 0
    long_state = bool(
        row["close"] >= row["prior_high"] + MIN_BREAK_ATR * atr
        and row["flow_60s"] >= MIN_FLOW_60S
        and row["flow_3m"] >= MIN_FLOW_3M
        and row["spot_flow_60s"] >= MIN_SPOT_FLOW_60S
        and row["spot_flow_3m"] >= MIN_SPOT_FLOW_3M
        and row["spot_ret_1m_bps"] >= MIN_SPOT_RETURN_BPS
        and row["depth_imbalance_1"] >= MIN_DEPTH_IMBALANCE_1
        and row["depth_imbalance_2"] >= MIN_DEPTH_IMBALANCE_2
        and row["ask_depth_change_1_1m"] <= -MIN_OPPOSING_CANCEL_1M
        and row["ask_depth_change_1_5m"] <= -MIN_OPPOSING_CANCEL_5M
        and row["bid_depth_change_1_1m"] >= -MAX_SAME_SIDE_CANCEL_1M
    )
    short_state = bool(
        row["close"] <= row["prior_low"] - MIN_BREAK_ATR * atr
        and row["flow_60s"] <= -MIN_FLOW_60S
        and row["flow_3m"] <= -MIN_FLOW_3M
        and row["spot_flow_60s"] <= -MIN_SPOT_FLOW_60S
        and row["spot_flow_3m"] <= -MIN_SPOT_FLOW_3M
        and row["spot_ret_1m_bps"] <= -MIN_SPOT_RETURN_BPS
        and row["depth_imbalance_1"] <= -MIN_DEPTH_IMBALANCE_1
        and row["depth_imbalance_2"] <= -MIN_DEPTH_IMBALANCE_2
        and row["bid_depth_change_1_1m"] <= -MIN_OPPOSING_CANCEL_1M
        and row["bid_depth_change_1_5m"] <= -MIN_OPPOSING_CANCEL_5M
        and row["ask_depth_change_1_1m"] >= -MAX_SAME_SIDE_CANCEL_1M
    )
    if long_state == short_state:
        return 0
    return 1 if long_state else -1


def geometry(row: pd.Series, entry: float, direction: int) -> dict[str, float] | None:
    atr = float(row["atr"])
    width = float(row["prior_high"] - row["prior_low"])
    if direction > 0:
        stop = float(row["low"]) - STOP_BUFFER_ATR * atr
        target = float(row["prior_high"]) + width
        if not 0.0 < stop < entry < target:
            return None
        reward = math.log(target / entry) * 10_000.0
        risk = math.log(entry / stop) * 10_000.0
    else:
        stop = float(row["high"]) + STOP_BUFFER_ATR * atr
        target = float(row["prior_low"]) - width
        if not stop > entry > target > 0.0:
            return None
        reward = math.log(entry / target) * 10_000.0
        risk = math.log(stop / entry) * 10_000.0
    net_reward = reward - ROUND_TRIP_HURDLE_BPS
    planned_loss = risk + ROUND_TRIP_HURDLE_BPS
    if net_reward <= 0.0 or planned_loss <= 0.0:
        return None
    net_rr = net_reward / planned_loss
    if net_rr < MIN_NET_RR:
        return None
    return {
        "entry": entry,
        "stop": stop,
        "target": target,
        "gross_reward_bps": reward,
        "gross_risk_bps": risk,
        "net_rr": net_rr,
    }


def simulate(
    frame: pd.DataFrame,
    entry_index: int,
    direction: int,
    trade: dict[str, float],
) -> dict[str, Any]:
    entry, stop, target = trade["entry"], trade["stop"], trade["target"]
    last_index = min(entry_index + MAX_HOLD_BARS - 1, len(frame) - 1)
    exit_index, exit_price, reason = last_index, float(frame.iloc[last_index]["close"]), "TIME"
    for index in range(entry_index, last_index + 1):
        row = frame.iloc[index]
        stop_hit = row["low"] <= stop if direction > 0 else row["high"] >= stop
        target_hit = row["high"] >= target if direction > 0 else row["low"] <= target
        if stop_hit:
            exit_index, exit_price, reason = index, stop, "STOP"
            break
        if target_hit:
            exit_index, exit_price, reason = index, target, "TARGET"
            break
    gross = direction * math.log(exit_price / entry) * 10_000.0
    return {
        "exit_timestamp": frame.iloc[exit_index]["open_time_dt"].isoformat(),
        "exit_reason": reason,
        "holding_minutes": int(exit_index - entry_index + 1),
        "net_pnl_bps": gross - ROUND_TRIP_HURDLE_BPS,
    }


def summarize(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {"count": 0, "mean_net_bps": None, "median_net_bps": None, "win_rate": None, "profit_factor": None, "largest_winner_share": None}
    wins, losses = clean[clean > 0.0], clean[clean < 0.0]
    positive, negative = float(wins.sum()), float(-losses.sum())
    return {
        "count": int(len(clean)),
        "mean_net_bps": float(clean.mean()),
        "median_net_bps": float(clean.median()),
        "win_rate": float((clean > 0.0).mean()),
        "profit_factor": positive / negative if negative > 0.0 else None,
        "largest_winner_share": float(wins.max() / positive) if positive > 0.0 else None,
    }


def run(symbol: str, start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    frame = load_frame(symbol, start, end, cache, output)
    start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end + timedelta(days=1), tz="UTC")
    events: list[dict[str, Any]] = []
    ready_rows = parents = accepted = 0
    last_parent = -10**9
    for index in range(BASELINE_BARS, len(frame) - MAX_HOLD_BARS - 2):
        timestamp, row = frame.iloc[index]["open_time_dt"], frame.iloc[index]
        if timestamp < start_ts or timestamp >= end_ts:
            continue
        if as_bool(row["feature_ready"]):
            ready_rows += 1
        if index - last_parent < PARENT_COOLDOWN_BARS:
            continue
        direction = parent_direction(row)
        if direction == 0:
            continue
        parents += 1
        last_parent = index
        entry_index = index + 1
        trade = geometry(row, float(frame.iloc[entry_index]["open"]), direction)
        if trade is None:
            continue
        accepted += 1
        event = {
            "symbol": symbol,
            "parent_timestamp": timestamp.isoformat(),
            "entry_timestamp": frame.iloc[entry_index]["open_time_dt"].isoformat(),
            "direction": direction,
            "balance_width_atr": float(row["prior_high"] - row["prior_low"]) / float(row["atr"]),
            "notional_burst": float(row["notional_burst"]),
            "efficiency_60s": float(row["efficiency_60s"]),
            "flow_60s": float(row["flow_60s"]),
            "flow_3m": float(row["flow_3m"]),
            "spot_flow_60s": float(row["spot_flow_60s"]),
            "spot_flow_3m": float(row["spot_flow_3m"]),
            "depth_imbalance_1": float(row["depth_imbalance_1"]),
            "depth_imbalance_2": float(row["depth_imbalance_2"]),
            **trade,
        }
        event.update(simulate(frame, entry_index, direction, trade))
        events.append(event)
    event_frame = pd.DataFrame(events)
    event_frame.to_csv(output / "liquidity_vacuum_events.csv", index=False)
    stats = summarize(event_frame["net_pnl_bps"] if not event_frame.empty else pd.Series(dtype=float))
    promising = bool(stats["count"] >= 10 and stats["mean_net_bps"] and stats["mean_net_bps"] > 0.0 and stats["median_net_bps"] and stats["median_net_bps"] > 0.0 and stats["win_rate"] and stats["win_rate"] >= 0.55 and stats["profit_factor"] and stats["profit_factor"] >= 1.20 and (stats["largest_winner_share"] is None or stats["largest_winner_share"] <= 0.35))
    report = {
        "schema": "liquidity-vacuum-screen-v1",
        "symbol": symbol,
        "development_period": [start.isoformat(), end.isoformat()],
        "entry_timing": "next one-minute open after completed book-and-flow parent",
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "diagnostics": {"rows": int(len(frame)), "ready_rows": ready_rows, "vacuum_parents": parents, "geometry_accepted": accepted},
        "results": stats,
        "promising_fixed_family": promising,
    }
    (output / "liquidity_vacuum_screen.json").write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, choices=SYMBOLS)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.symbol, date.fromisoformat(args.start), date.fromisoformat(args.end), args.cache, args.output), indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
