#!/usr/bin/env python3
"""Fixed spot/perpetual absorption-at-liquidity reclaim screen.

The screen mechanizes an order-flow concept rather than another candle-pattern
variant:

    completed prior-hour balance creates an external liquidity boundary
    -> perpetual market orders aggressively sweep the boundary on abnormal
       volume, but price impact is poor and spot does not confirm the sweep
    -> a strictly later five-minute bar re-enters the old balance while both
       spot and perpetual flow turn against the failed sweep
    -> entry at the next open, stop beyond the complete absorption episode,
       target at the opposite prior-hour boundary

The parent bar defines absorption using effort versus result.  Reclaim and entry
use later completed bars, so the same observation is not reused as its own
confirmation.  Parents are cooled down for one hour.  Geometry must clear a
fixed 20 bp round-trip hurdle and net reward/risk >= 1.0.  Same-bar stop/target
ambiguity is resolved against the strategy.

This remains an event/geometry screen.  A passing family must be implemented in
NautilusTrader with actual fills, one global position, current-NAV 3% planned
loss sizing, funding, and continuous account NAV.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
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

from features import download_checked
from spot_participation_contract import _download_spot_checked
from vision_derivatives_contract import read_full_kline


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BAR_MINUTES = 5
BALANCE_BARS = 12
ATR_BARS = 24
MAX_TRANSITION_BARS = 2
MAX_HOLD_BARS = 24
PARENT_COOLDOWN_BARS = 12
ROUND_TRIP_HURDLE_BPS = 20.0
MIN_NET_RR = 1.0
MIN_BALANCE_WIDTH_ATR = 2.0
MIN_SWEEP_ATR = 0.10
MIN_PERP_VOLUME_BURST = 1.50
MIN_PARENT_PERP_FLOW = 0.20
MAX_PARENT_SPOT_CONFIRMATION_FLOW = 0.05
MAX_PARENT_CLOSE_PROGRESS_ATR = 0.50
MAX_PARENT_CLOSE_LOCATION_UP = 0.70
MIN_PARENT_CLOSE_LOCATION_DOWN = 0.30
MIN_RECLAIM_ATR = 0.05
MIN_RECLAIM_SPOT_FLOW = 0.10
MIN_RECLAIM_PERP_FLOW = 0.05
MIN_RECLAIM_CLOSE_LOCATION = 0.60
STOP_BUFFER_ATR = 0.05


def _resample(minute: pd.DataFrame) -> pd.DataFrame:
    minute = minute.sort_values("open_time_dt").set_index("open_time_dt")
    bars = minute.resample(
        f"{BAR_MINUTES}min", label="left", closed="left",
    ).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        quote_volume=("quote_volume", "sum"),
        taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
        minute_count=("close", "count"),
    )
    bars = bars[bars["minute_count"] == BAR_MINUTES].copy()
    bars["signed_taker_quote"] = (
        2.0 * bars["taker_buy_quote_volume"] - bars["quote_volume"]
    )
    bars["flow"] = bars["signed_taker_quote"] / bars["quote_volume"].replace(0.0, np.nan)
    bar_range = (bars["high"] - bars["low"]).replace(0.0, np.nan)
    bars["close_location"] = (bars["close"] - bars["low"]) / bar_range
    return bars


def _load_symbol(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    load_start = start - timedelta(days=1)
    perp_frames: list[pd.DataFrame] = []
    spot_frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    day = load_start
    while day <= end:
        perp_path, _checksum, perp_raw = download_checked(
            "klines", symbol, day, cache / symbol / "perpetual",
        )
        spot_path, _spot_checksum, spot_raw = _download_spot_checked(
            "klines", symbol, day, cache / symbol,
        )
        perp_frames.append(read_full_kline(perp_path))
        spot_frames.append(read_full_kline(spot_path))
        evidence.extend([asdict(perp_raw), asdict(spot_raw)])
        day += timedelta(days=1)

    perp_minute = pd.concat(perp_frames, ignore_index=True)
    spot_minute = pd.concat(spot_frames, ignore_index=True)
    for label, minute in (("perpetual", perp_minute), ("spot", spot_minute)):
        if minute["open_time_dt"].duplicated().any():
            raise RuntimeError(f"duplicate {label} minute for {symbol}")

    perp = _resample(perp_minute)
    spot = _resample(spot_minute)
    frame = perp.join(spot, how="inner", lsuffix="_perp", rsuffix="_spot")
    frame = frame.sort_index()
    expected_days = (end - start).days + 2
    if len(frame) < expected_days * 288 * 0.95:
        raise RuntimeError(f"spot/perpetual five-minute join lost too many rows for {symbol}")

    prior_close = frame["close_perp"].shift(1)
    true_range = pd.concat(
        [
            frame["high_perp"] - frame["low_perp"],
            (frame["high_perp"] - prior_close).abs(),
            (frame["low_perp"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr_price"] = true_range.shift(1).rolling(
        ATR_BARS, min_periods=ATR_BARS,
    ).median()
    frame["prior_close"] = prior_close
    frame["prior_high"] = frame["high_perp"].shift(1).rolling(
        BALANCE_BARS, min_periods=BALANCE_BARS,
    ).max()
    frame["prior_low"] = frame["low_perp"].shift(1).rolling(
        BALANCE_BARS, min_periods=BALANCE_BARS,
    ).min()
    frame["spot_prior_high"] = frame["high_spot"].shift(1).rolling(
        BALANCE_BARS, min_periods=BALANCE_BARS,
    ).max()
    frame["spot_prior_low"] = frame["low_spot"].shift(1).rolling(
        BALANCE_BARS, min_periods=BALANCE_BARS,
    ).min()
    past_volume = frame["quote_volume_perp"].shift(1).rolling(
        ATR_BARS * 2, min_periods=ATR_BARS,
    ).median()
    frame["perp_volume_burst"] = (
        frame["quote_volume_perp"] / past_volume.replace(0.0, np.nan)
    )
    frame["ready"] = frame[
        [
            "atr_price",
            "prior_close",
            "prior_high",
            "prior_low",
            "spot_prior_high",
            "spot_prior_low",
            "perp_volume_burst",
            "flow_perp",
            "flow_spot",
            "close_location_perp",
        ]
    ].notna().all(axis=1)
    return frame, evidence


def _absorption_direction(row: pd.Series) -> int:
    if not bool(row["ready"]):
        return 0
    atr = float(row["atr_price"])
    balance_width = float(row["prior_high"] - row["prior_low"])
    if not (
        atr > 0.0
        and balance_width >= MIN_BALANCE_WIDTH_ATR * atr
        and row["perp_volume_burst"] >= MIN_PERP_VOLUME_BURST
    ):
        return 0
    upward = bool(
        row["high_perp"] >= row["prior_high"] + MIN_SWEEP_ATR * atr
        and row["flow_perp"] >= MIN_PARENT_PERP_FLOW
        and row["flow_spot"] <= MAX_PARENT_SPOT_CONFIRMATION_FLOW
        and row["high_spot"] < row["spot_prior_high"] + MIN_SWEEP_ATR * atr
        and row["close_perp"] - row["prior_close"] <= MAX_PARENT_CLOSE_PROGRESS_ATR * atr
        and row["close_location_perp"] <= MAX_PARENT_CLOSE_LOCATION_UP
    )
    downward = bool(
        row["low_perp"] <= row["prior_low"] - MIN_SWEEP_ATR * atr
        and row["flow_perp"] <= -MIN_PARENT_PERP_FLOW
        and row["flow_spot"] >= -MAX_PARENT_SPOT_CONFIRMATION_FLOW
        and row["low_spot"] > row["spot_prior_low"] - MIN_SWEEP_ATR * atr
        and row["prior_close"] - row["close_perp"] <= MAX_PARENT_CLOSE_PROGRESS_ATR * atr
        and row["close_location_perp"] >= MIN_PARENT_CLOSE_LOCATION_DOWN
    )
    if upward == downward:
        return 0
    return 1 if upward else -1


def _find_reclaim(
    frame: pd.DataFrame,
    parent_index: int,
    sweep_direction: int,
    atr: float,
    boundary: float,
) -> int | None:
    for index in range(
        parent_index + 1,
        min(parent_index + MAX_TRANSITION_BARS + 1, len(frame) - 1),
    ):
        row = frame.iloc[index]
        if sweep_direction > 0:
            transition = bool(
                row["close_perp"] <= boundary - MIN_RECLAIM_ATR * atr
                and row["flow_perp"] <= -MIN_RECLAIM_PERP_FLOW
                and row["flow_spot"] <= -MIN_RECLAIM_SPOT_FLOW
                and row["close_location_perp"] <= 1.0 - MIN_RECLAIM_CLOSE_LOCATION
            )
        else:
            transition = bool(
                row["close_perp"] >= boundary + MIN_RECLAIM_ATR * atr
                and row["flow_perp"] >= MIN_RECLAIM_PERP_FLOW
                and row["flow_spot"] >= MIN_RECLAIM_SPOT_FLOW
                and row["close_location_perp"] >= MIN_RECLAIM_CLOSE_LOCATION
            )
        if transition:
            return index
    return None


def _geometry(
    frame: pd.DataFrame,
    parent_index: int,
    transition_index: int,
    sweep_direction: int,
    atr: float,
    target: float,
) -> dict[str, float] | None:
    entry_index = transition_index + 1
    if entry_index >= len(frame):
        return None
    episode = frame.iloc[parent_index : transition_index + 1]
    trade_direction = -sweep_direction
    entry = float(frame.iloc[entry_index]["open_perp"])
    if trade_direction > 0:
        stop = float(episode["low_perp"].min()) - STOP_BUFFER_ATR * atr
        if not (0.0 < stop < entry < target):
            return None
        gross_reward = math.log(target / entry) * 10_000.0
        gross_risk = math.log(entry / stop) * 10_000.0
    else:
        stop = float(episode["high_perp"].max()) + STOP_BUFFER_ATR * atr
        if not (stop > entry > target > 0.0):
            return None
        gross_reward = math.log(entry / target) * 10_000.0
        gross_risk = math.log(stop / entry) * 10_000.0
    net_reward = gross_reward - ROUND_TRIP_HURDLE_BPS
    planned_loss = gross_risk + ROUND_TRIP_HURDLE_BPS
    if net_reward <= 0.0 or planned_loss <= 0.0:
        return None
    net_rr = net_reward / planned_loss
    if net_rr < MIN_NET_RR:
        return None
    return {
        "entry_index": float(entry_index),
        "trade_direction": float(trade_direction),
        "entry": entry,
        "stop": stop,
        "target": target,
        "gross_reward_bps": gross_reward,
        "gross_risk_bps": gross_risk,
        "net_reward_bps": net_reward,
        "planned_loss_bps": planned_loss,
        "net_rr": net_rr,
    }


def _simulate(frame: pd.DataFrame, geometry: dict[str, float]) -> dict[str, Any]:
    entry_index = int(geometry["entry_index"])
    direction = int(geometry["trade_direction"])
    entry = geometry["entry"]
    stop = geometry["stop"]
    target = geometry["target"]
    last_index = min(entry_index + MAX_HOLD_BARS - 1, len(frame) - 1)
    exit_index = last_index
    exit_price = float(frame.iloc[last_index]["close_perp"])
    exit_reason = "TIME"
    for index in range(entry_index, last_index + 1):
        row = frame.iloc[index]
        if direction > 0:
            stop_hit = float(row["low_perp"]) <= stop
            target_hit = float(row["high_perp"]) >= target
        else:
            stop_hit = float(row["high_perp"]) >= stop
            target_hit = float(row["low_perp"]) <= target
        if stop_hit:
            exit_index = index
            exit_price = stop
            exit_reason = "STOP"
            break
        if target_hit:
            exit_index = index
            exit_price = target
            exit_reason = "TARGET"
            break
    gross = direction * math.log(exit_price / entry) * 10_000.0
    return {
        "exit_timestamp": frame.index[exit_index].isoformat(),
        "exit_reason": exit_reason,
        "holding_bars": int(exit_index - entry_index + 1),
        "net_pnl_bps": gross - ROUND_TRIP_HURDLE_BPS,
    }


def _summary(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return {
            "count": 0,
            "mean_net_bps": None,
            "median_net_bps": None,
            "win_rate": None,
            "profit_factor": None,
            "largest_winner_share": None,
            "p10_net_bps": None,
            "p90_net_bps": None,
        }
    wins = clean[clean > 0.0]
    losses = clean[clean < 0.0]
    positive_sum = float(wins.sum())
    negative_sum = float(-losses.sum())
    return {
        "count": int(len(clean)),
        "mean_net_bps": float(clean.mean()),
        "median_net_bps": float(clean.median()),
        "win_rate": float((clean > 0.0).mean()),
        "profit_factor": positive_sum / negative_sum if negative_sum > 0.0 else None,
        "largest_winner_share": (
            float(wins.max() / positive_sum) if positive_sum > 0.0 else None
        ),
        "p10_net_bps": float(clean.quantile(0.10)),
        "p90_net_bps": float(clean.quantile(0.90)),
    }


def _collect_symbol_events(
    symbol: str,
    frame: pd.DataFrame,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events: list[dict[str, Any]] = []
    diagnostics = {
        "absorption_parents": 0,
        "reclaims": 0,
        "geometry_accepted": 0,
    }
    last_parent = -10**9
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    max_future = MAX_TRANSITION_BARS + MAX_HOLD_BARS + 2
    for parent_index in range(ATR_BARS * 2, len(frame) - max_future):
        timestamp = frame.index[parent_index]
        if timestamp < start_ts or timestamp >= end_ts:
            continue
        if parent_index - last_parent < PARENT_COOLDOWN_BARS:
            continue
        parent = frame.iloc[parent_index]
        sweep_direction = _absorption_direction(parent)
        if sweep_direction == 0:
            continue
        diagnostics["absorption_parents"] += 1
        last_parent = parent_index
        atr = float(parent["atr_price"])
        boundary = (
            float(parent["prior_high"])
            if sweep_direction > 0
            else float(parent["prior_low"])
        )
        target = (
            float(parent["prior_low"])
            if sweep_direction > 0
            else float(parent["prior_high"])
        )
        transition_index = _find_reclaim(
            frame, parent_index, sweep_direction, atr, boundary,
        )
        if transition_index is None:
            continue
        diagnostics["reclaims"] += 1
        geometry = _geometry(
            frame,
            parent_index,
            transition_index,
            sweep_direction,
            atr,
            target,
        )
        if geometry is None:
            continue
        diagnostics["geometry_accepted"] += 1
        entry_index = int(geometry["entry_index"])
        episode = frame.iloc[parent_index : transition_index + 1]
        event = {
            "symbol": symbol,
            "family": "ABSORPTION_RECLAIM_REVERSAL",
            "parent_timestamp": timestamp.isoformat(),
            "transition_timestamp": frame.index[transition_index].isoformat(),
            "entry_timestamp": frame.index[entry_index].isoformat(),
            "sweep_direction": sweep_direction,
            "trade_direction": int(geometry["trade_direction"]),
            "balance_width_atr": (
                float(parent["prior_high"] - parent["prior_low"]) / atr
            ),
            "sweep_depth_atr": (
                (float(episode["high_perp"].max()) - float(parent["prior_high"])) / atr
                if sweep_direction > 0
                else (float(parent["prior_low"]) - float(episode["low_perp"].min())) / atr
            ),
            "parent_perp_volume_burst": float(parent["perp_volume_burst"]),
            "parent_perp_flow": float(parent["flow_perp"]),
            "parent_spot_flow": float(parent["flow_spot"]),
            "parent_close_progress_atr": (
                sweep_direction * (float(parent["close_perp"]) - float(parent["prior_close"])) / atr
            ),
            "transition_perp_flow": float(frame.iloc[transition_index]["flow_perp"]),
            "transition_spot_flow": float(frame.iloc[transition_index]["flow_spot"]),
            **{
                key: value
                for key, value in geometry.items()
                if key not in {"entry_index", "trade_direction"}
            },
        }
        event.update(_simulate(frame, geometry))
        events.append(event)
    return events, diagnostics


def run_screen(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    all_events: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    bar_counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        frame, raw = _load_symbol(symbol, start, end, cache)
        evidence.extend(raw)
        bar_counts[symbol] = int(len(frame))
        symbol_events, symbol_diagnostics = _collect_symbol_events(
            symbol, frame, start, end,
        )
        all_events.extend(symbol_events)
        diagnostics[symbol] = symbol_diagnostics

    events = pd.DataFrame(all_events)
    events.to_csv(output / "absorption_reclaim_events.csv", index=False)
    overall = _summary(
        events["net_pnl_bps"] if not events.empty else pd.Series(dtype=float),
    )
    by_symbol = {
        symbol: _summary(events.loc[events["symbol"] == symbol, "net_pnl_bps"])
        for symbol in SYMBOLS
    } if not events.empty else {
        symbol: _summary(pd.Series(dtype=float)) for symbol in SYMBOLS
    }
    promising = []
    if (
        overall["count"] >= 30
        and overall["mean_net_bps"] is not None
        and overall["mean_net_bps"] > 0.0
        and overall["median_net_bps"] is not None
        and overall["median_net_bps"] > 0.0
        and overall["win_rate"] is not None
        and overall["win_rate"] >= 0.55
        and overall["profit_factor"] is not None
        and overall["profit_factor"] >= 1.20
        and (
            overall["largest_winner_share"] is None
            or overall["largest_winner_share"] <= 0.35
        )
    ):
        promising.append({"family": "ABSORPTION_RECLAIM_REVERSAL", **overall})

    report = {
        "schema": "external-absorption-reclaim-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "bar_minutes": BAR_MINUTES,
        "bar_counts": bar_counts,
        "entry_timing": "strictly later five-minute open after completed reclaim",
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "max_holding_minutes": MAX_HOLD_BARS * BAR_MINUTES,
        "parent_cooldown_minutes": PARENT_COOLDOWN_BARS * BAR_MINUTES,
        "fixed_parameters": {
            "balance_bars": BALANCE_BARS,
            "min_balance_width_atr": MIN_BALANCE_WIDTH_ATR,
            "min_sweep_atr": MIN_SWEEP_ATR,
            "min_perp_volume_burst": MIN_PERP_VOLUME_BURST,
            "min_parent_perp_flow": MIN_PARENT_PERP_FLOW,
            "max_parent_spot_confirmation_flow": MAX_PARENT_SPOT_CONFIRMATION_FLOW,
            "max_parent_close_progress_atr": MAX_PARENT_CLOSE_PROGRESS_ATR,
            "max_transition_bars": MAX_TRANSITION_BARS,
            "min_reclaim_spot_flow": MIN_RECLAIM_SPOT_FLOW,
            "min_reclaim_perp_flow": MIN_RECLAIM_PERP_FLOW,
            "min_net_rr": MIN_NET_RR,
        },
        "diagnostics": diagnostics,
        "event_count": int(len(events)),
        "event_counts": (
            events["symbol"].value_counts().sort_index().rename("count").reset_index(names="symbol").to_dict("records")
            if not events.empty else []
        ),
        "results": {
            "overall": overall,
            "target_rate": (
                float((events["exit_reason"] == "TARGET").mean()) if not events.empty else None
            ),
            "stop_rate": (
                float((events["exit_reason"] == "STOP").mean()) if not events.empty else None
            ),
            "by_symbol": by_symbol,
        },
        "promising_fixed_families": promising,
        "interpretation": (
            "A pass indicates economic space after a complete effort-versus-result absorption sequence. "
            "It is not a NautilusTrader result."
        ),
    }
    (output / "absorption_reclaim_screen.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "raw_evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_screen(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        args.cache,
        args.output,
    )
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
