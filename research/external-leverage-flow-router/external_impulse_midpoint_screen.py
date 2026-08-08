#!/usr/bin/env python3
"""Fixed high-volume impulse midpoint state-router screen.

The scenario is mined from Korean discretionary trader descriptions which treat
large 30-minute-or-higher candles and their midpoint as an inventory-transfer
reference, not as a stand-alone entry pattern:

    prior four-hour balance
    -> wide, high-volume spot-confirmed impulse breaks the balance
    -> first later interaction with the impulse midpoint
       -> midpoint defended with renewed spot/perpetual flow: continuation
       -> midpoint lost with opposing spot/perpetual flow: failed impulse
    -> entry at the next 30-minute open
    -> invalidation and objective belong to the new post-interaction leg

The parent candle only defines context and the midpoint.  A strictly later bar
must define the state transition.  Parents are cooled down for four hours so a
single expansion episode cannot inflate the event count.  Geometry must clear a
20 bp round-trip hurdle and net reward/risk >= 1.0 before admission.  Same-bar
stop/target ambiguity is resolved against the strategy.

This is an economic-space event screen.  A passing family still requires a
NautilusTrader implementation with actual fills, one global position, current
NAV based 3% planned-loss sizing, funding, and continuous account NAV.
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
BAR_MINUTES = 30
PRIOR_BALANCE_BARS = 8
ATR_BARS = 16
MAX_TRANSITION_BARS = 4
MAX_HOLD_BARS = 12
PARENT_COOLDOWN_BARS = 8
ROUND_TRIP_HURDLE_BPS = 20.0
MIN_NET_RR = 1.0
MIN_PARENT_RANGE_ATR = 1.75
MIN_PARENT_BREAK_ATR = 0.10
MIN_PARENT_CLOSE_LOCATION = 0.75
MIN_PARENT_SPOT_VOLUME_BURST = 1.50
MIN_PARENT_PERP_VOLUME_BURST = 1.50
MIN_PARENT_SPOT_FLOW = 0.10
MIN_PARENT_PERP_FLOW = 0.10
MIN_SPOT_RETURN_RATIO = 0.70
MIDPOINT_TOUCH_TOLERANCE_ATR = 0.10
MIDPOINT_CLOSE_MARGIN_ATR = 0.05
MIN_TRANSITION_CLOSE_LOCATION = 0.55
MIN_TRANSITION_SPOT_FLOW = 0.05
MIN_TRANSITION_PERP_FLOW = 0.05
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
    load_start = start - timedelta(days=2)
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
    expected_days = (end - start).days + 1
    if len(frame) < (expected_days + 2) * 48 * 0.95:
        raise RuntimeError(f"spot/perpetual 30-minute join lost too many rows for {symbol}")

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
    frame["prior_high"] = frame["high_perp"].shift(1).rolling(
        PRIOR_BALANCE_BARS, min_periods=PRIOR_BALANCE_BARS,
    ).max()
    frame["prior_low"] = frame["low_perp"].shift(1).rolling(
        PRIOR_BALANCE_BARS, min_periods=PRIOR_BALANCE_BARS,
    ).min()
    for venue in ("perp", "spot"):
        past_volume = frame[f"quote_volume_{venue}"].shift(1).rolling(
            ATR_BARS * 2, min_periods=ATR_BARS,
        ).median()
        frame[f"volume_burst_{venue}"] = (
            frame[f"quote_volume_{venue}"] / past_volume.replace(0.0, np.nan)
        )
    frame["return_perp_bps"] = (
        np.log(frame["close_perp"] / frame["open_perp"]) * 10_000.0
    )
    frame["return_spot_bps"] = (
        np.log(frame["close_spot"] / frame["open_spot"]) * 10_000.0
    )
    frame["ready"] = frame[
        [
            "atr_price",
            "prior_high",
            "prior_low",
            "volume_burst_perp",
            "volume_burst_spot",
            "flow_perp",
            "flow_spot",
            "close_location_perp",
            "close_location_spot",
            "return_perp_bps",
            "return_spot_bps",
        ]
    ].notna().all(axis=1)
    return frame, evidence


def _parent_direction(row: pd.Series) -> int:
    if not bool(row["ready"]):
        return 0
    atr = float(row["atr_price"])
    parent_range = float(row["high_perp"] - row["low_perp"])
    if not (
        atr > 0.0
        and parent_range >= MIN_PARENT_RANGE_ATR * atr
        and row["volume_burst_perp"] >= MIN_PARENT_PERP_VOLUME_BURST
        and row["volume_burst_spot"] >= MIN_PARENT_SPOT_VOLUME_BURST
    ):
        return 0
    if float(row["return_perp_bps"]) == 0.0:
        return 0
    spot_return_ratio = abs(float(row["return_spot_bps"])) / abs(
        float(row["return_perp_bps"]),
    )
    if spot_return_ratio < MIN_SPOT_RETURN_RATIO:
        return 0
    up = bool(
        row["close_perp"] >= row["prior_high"] + MIN_PARENT_BREAK_ATR * atr
        and row["close_location_perp"] >= MIN_PARENT_CLOSE_LOCATION
        and row["close_location_spot"] >= MIN_PARENT_CLOSE_LOCATION
        and row["flow_perp"] >= MIN_PARENT_PERP_FLOW
        and row["flow_spot"] >= MIN_PARENT_SPOT_FLOW
        and row["return_perp_bps"] > 0.0
        and row["return_spot_bps"] > 0.0
    )
    down = bool(
        row["close_perp"] <= row["prior_low"] - MIN_PARENT_BREAK_ATR * atr
        and row["close_location_perp"] <= 1.0 - MIN_PARENT_CLOSE_LOCATION
        and row["close_location_spot"] <= 1.0 - MIN_PARENT_CLOSE_LOCATION
        and row["flow_perp"] <= -MIN_PARENT_PERP_FLOW
        and row["flow_spot"] <= -MIN_PARENT_SPOT_FLOW
        and row["return_perp_bps"] < 0.0
        and row["return_spot_bps"] < 0.0
    )
    if up == down:
        return 0
    return 1 if up else -1


def _transition(
    frame: pd.DataFrame,
    parent_index: int,
    direction: int,
    midpoint: float,
    atr: float,
) -> tuple[str, int] | None:
    for index in range(
        parent_index + 1,
        min(parent_index + MAX_TRANSITION_BARS + 1, len(frame) - 1),
    ):
        row = frame.iloc[index]
        if direction > 0:
            touched = float(row["low_perp"]) <= midpoint + MIDPOINT_TOUCH_TOLERANCE_ATR * atr
            defense = bool(
                touched
                and row["close_perp"] >= midpoint + MIDPOINT_CLOSE_MARGIN_ATR * atr
                and row["close_location_perp"] >= MIN_TRANSITION_CLOSE_LOCATION
                and row["flow_perp"] >= MIN_TRANSITION_PERP_FLOW
                and row["flow_spot"] >= MIN_TRANSITION_SPOT_FLOW
            )
            failure = bool(
                row["close_perp"] <= midpoint - MIDPOINT_CLOSE_MARGIN_ATR * atr
                and row["close_location_perp"] <= 1.0 - MIN_TRANSITION_CLOSE_LOCATION
                and row["flow_perp"] <= -MIN_TRANSITION_PERP_FLOW
                and row["flow_spot"] <= -MIN_TRANSITION_SPOT_FLOW
            )
        else:
            touched = float(row["high_perp"]) >= midpoint - MIDPOINT_TOUCH_TOLERANCE_ATR * atr
            defense = bool(
                touched
                and row["close_perp"] <= midpoint - MIDPOINT_CLOSE_MARGIN_ATR * atr
                and row["close_location_perp"] <= 1.0 - MIN_TRANSITION_CLOSE_LOCATION
                and row["flow_perp"] <= -MIN_TRANSITION_PERP_FLOW
                and row["flow_spot"] <= -MIN_TRANSITION_SPOT_FLOW
            )
            failure = bool(
                row["close_perp"] >= midpoint + MIDPOINT_CLOSE_MARGIN_ATR * atr
                and row["close_location_perp"] >= MIN_TRANSITION_CLOSE_LOCATION
                and row["flow_perp"] >= MIN_TRANSITION_PERP_FLOW
                and row["flow_spot"] >= MIN_TRANSITION_SPOT_FLOW
            )
        if defense and not failure:
            return "IMPULSE_MIDPOINT_DEFENSE", index
        if failure and not defense:
            return "IMPULSE_MIDPOINT_FAILURE", index
    return None


def _geometry(
    frame: pd.DataFrame,
    parent_index: int,
    transition_index: int,
    original_direction: int,
    family: str,
    atr: float,
) -> dict[str, float] | None:
    entry_index = transition_index + 1
    if entry_index >= len(frame):
        return None
    parent = frame.iloc[parent_index]
    transition = frame.iloc[transition_index]
    parent_range = float(parent["high_perp"] - parent["low_perp"])
    entry = float(frame.iloc[entry_index]["open_perp"])
    if family == "IMPULSE_MIDPOINT_DEFENSE":
        direction = original_direction
        if direction > 0:
            stop = float(transition["low_perp"]) - STOP_BUFFER_ATR * atr
            target = float(parent["high_perp"]) + parent_range
            if not (0.0 < stop < entry < target):
                return None
            gross_reward = math.log(target / entry) * 10_000.0
            gross_risk = math.log(entry / stop) * 10_000.0
        else:
            stop = float(transition["high_perp"]) + STOP_BUFFER_ATR * atr
            target = float(parent["low_perp"]) - parent_range
            if not (stop > entry > target > 0.0):
                return None
            gross_reward = math.log(entry / target) * 10_000.0
            gross_risk = math.log(stop / entry) * 10_000.0
    else:
        direction = -original_direction
        episode = frame.iloc[parent_index : transition_index + 1]
        if direction > 0:
            stop = float(episode["low_perp"].min()) - STOP_BUFFER_ATR * atr
            target = float(parent["high_perp"])
            if not (0.0 < stop < entry < target):
                return None
            gross_reward = math.log(target / entry) * 10_000.0
            gross_risk = math.log(entry / stop) * 10_000.0
        else:
            stop = float(episode["high_perp"].max()) + STOP_BUFFER_ATR * atr
            target = float(parent["low_perp"])
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
        "trade_direction": float(direction),
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
        "parent_candidates": 0,
        "midpoint_defenses": 0,
        "midpoint_failures": 0,
        "defense_geometry": 0,
        "failure_geometry": 0,
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
        original_direction = _parent_direction(parent)
        if original_direction == 0:
            continue
        diagnostics["parent_candidates"] += 1
        last_parent = parent_index
        atr = float(parent["atr_price"])
        midpoint = (
            float(parent["high_perp"]) + float(parent["low_perp"])
        ) / 2.0
        transition = _transition(
            frame, parent_index, original_direction, midpoint, atr,
        )
        if transition is None:
            continue
        family, transition_index = transition
        diagnostics[
            "midpoint_defenses" if family == "IMPULSE_MIDPOINT_DEFENSE" else "midpoint_failures"
        ] += 1
        geometry = _geometry(
            frame,
            parent_index,
            transition_index,
            original_direction,
            family,
            atr,
        )
        if geometry is None:
            continue
        diagnostics[
            "defense_geometry" if family == "IMPULSE_MIDPOINT_DEFENSE" else "failure_geometry"
        ] += 1
        entry_index = int(geometry["entry_index"])
        parent_range = float(parent["high_perp"] - parent["low_perp"])
        event = {
            "symbol": symbol,
            "family": family,
            "parent_timestamp": timestamp.isoformat(),
            "transition_timestamp": frame.index[transition_index].isoformat(),
            "entry_timestamp": frame.index[entry_index].isoformat(),
            "original_direction": original_direction,
            "trade_direction": int(geometry["trade_direction"]),
            "parent_range_atr": parent_range / atr,
            "parent_range_bps": parent_range / float(parent["close_perp"]) * 10_000.0,
            "parent_spot_return_ratio": abs(float(parent["return_spot_bps"])) / abs(float(parent["return_perp_bps"])),
            "parent_spot_volume_burst": float(parent["volume_burst_spot"]),
            "parent_perp_volume_burst": float(parent["volume_burst_perp"]),
            "parent_spot_flow": float(parent["flow_spot"]),
            "parent_perp_flow": float(parent["flow_perp"]),
            "midpoint": midpoint,
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
    events.to_csv(output / "impulse_midpoint_events.csv", index=False)
    families = ("IMPULSE_MIDPOINT_DEFENSE", "IMPULSE_MIDPOINT_FAILURE")
    results: dict[str, Any] = {}
    promising: list[dict[str, Any]] = []
    for family in families:
        subset = events[events["family"] == family] if not events.empty else pd.DataFrame()
        overall = _summary(
            subset["net_pnl_bps"] if not subset.empty else pd.Series(dtype=float),
        )
        results[family] = {
            "overall": overall,
            "target_rate": (
                float((subset["exit_reason"] == "TARGET").mean()) if not subset.empty else None
            ),
            "stop_rate": (
                float((subset["exit_reason"] == "STOP").mean()) if not subset.empty else None
            ),
            "symbol_counts": (
                subset["symbol"].value_counts().sort_index().to_dict()
                if not subset.empty else {}
            ),
            "by_symbol": {
                symbol: _summary(subset.loc[subset["symbol"] == symbol, "net_pnl_bps"])
                for symbol in SYMBOLS
            } if not subset.empty else {
                symbol: _summary(pd.Series(dtype=float)) for symbol in SYMBOLS
            },
        }
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
            promising.append({"family": family, **overall})

    report = {
        "schema": "external-impulse-midpoint-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "bar_minutes": BAR_MINUTES,
        "bar_counts": bar_counts,
        "entry_timing": "strictly later 30-minute open after completed midpoint transition",
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "max_holding_minutes": MAX_HOLD_BARS * BAR_MINUTES,
        "parent_cooldown_minutes": PARENT_COOLDOWN_BARS * BAR_MINUTES,
        "fixed_parameters": {
            "prior_balance_bars": PRIOR_BALANCE_BARS,
            "min_parent_range_atr": MIN_PARENT_RANGE_ATR,
            "min_parent_break_atr": MIN_PARENT_BREAK_ATR,
            "min_parent_spot_volume_burst": MIN_PARENT_SPOT_VOLUME_BURST,
            "min_parent_perp_volume_burst": MIN_PARENT_PERP_VOLUME_BURST,
            "min_parent_spot_flow": MIN_PARENT_SPOT_FLOW,
            "min_parent_perp_flow": MIN_PARENT_PERP_FLOW,
            "min_spot_return_ratio": MIN_SPOT_RETURN_RATIO,
            "max_transition_bars": MAX_TRANSITION_BARS,
            "min_net_rr": MIN_NET_RR,
        },
        "diagnostics": diagnostics,
        "event_count": int(len(events)),
        "event_counts": (
            events.groupby(["family", "symbol"]).size().rename("count").reset_index().to_dict("records")
            if not events.empty else []
        ),
        "results": results,
        "promising_fixed_families": promising,
        "interpretation": (
            "A pass indicates economic space after a complete high-volume impulse midpoint state transition. "
            "It is not a NautilusTrader result."
        ),
    }
    (output / "impulse_midpoint_screen.json").write_text(
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
