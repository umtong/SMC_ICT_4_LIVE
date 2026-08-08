#!/usr/bin/env python3
"""Fixed higher-timeframe trend stop-run screen with spot volume validation.

The scenario is mined from practical trader descriptions rather than from the
previous candidate lineage:

    completed higher-timeframe directional structure
    -> repeated support/resistance creates a visible stop pool
    -> perpetual price sweeps the level on unusually high activity
    -> spot participation validates the reclaim
    -> immediate entry or one strictly later hold transition
    -> prior four-hour opposite extreme is the objective

This differs from an unconditional failed-auction screen.  The stop run is only
traded back into the already established higher-timeframe direction, at a
repeatedly defended level, with independent spot participation.  Geometry is
known before entry and must clear a 20 bp round-trip hurdle with net reward/risk
of at least 1.0.

The script is an event/geometry screen.  Any passing family still requires a
NautilusTrader implementation with actual fills, one global position, 3% risk
sizing, funding, and continuous account NAV.
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
from cross_asset_transfer_screen_fixed import robust_read_kline
from spot_participation_contract import _download_spot_checked


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BAR_MINUTES = 15
LIQUIDITY_LOOKBACK_BARS = 16
CONTEXT_BLOCK_BARS = 16
MAX_HOLD_BARS = 16
PARENT_COOLDOWN_BARS = 16
ROUND_TRIP_HURDLE_BPS = 20.0
MIN_NET_RR = 1.0
MIN_TOUCHES = 2
MIN_TOUCH_SEPARATION_BARS = 2
LEVEL_TOLERANCE_ATR = 0.15
MIN_BOUNCE_ATR = 0.60
MIN_SWEEP_ATR = 0.10
MIN_RECLAIM_ATR = 0.05
STOP_BUFFER_ATR = 0.05
MIN_PERP_VOLUME_BURST = 2.0
MIN_SPOT_VOLUME_BURST = 1.5
MIN_RANGE_BURST = 1.25
MIN_RECLAIM_CLOSE_LOCATION = 0.65
MAX_RECLAIM_CLOSE_LOCATION_SHORT = 0.35
MAX_HOLD_PENETRATION_ATR = 0.10


def _resample_15m(minute: pd.DataFrame) -> pd.DataFrame:
    minute = minute.sort_values("open_time_dt").set_index("open_time_dt")
    bars = minute.resample(
        f"{BAR_MINUTES}min", label="left", closed="left",
    ).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        quote_volume=("quote_volume", "sum"),
        minute_count=("close", "count"),
    )
    return bars[bars["minute_count"] == BAR_MINUTES].copy()


def _load_symbol(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    perp_frames: list[pd.DataFrame] = []
    spot_frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    day = start
    while day <= end:
        perp_path, _perp_checksum, perp_raw = download_checked(
            "klines", symbol, day, cache / symbol / "perpetual",
        )
        spot_path, _spot_checksum, spot_raw = _download_spot_checked(
            "klines", symbol, day, cache / symbol,
        )
        perp_frames.append(robust_read_kline(perp_path))
        spot_frames.append(robust_read_kline(spot_path))
        evidence.extend([asdict(perp_raw), asdict(spot_raw)])
        day += timedelta(days=1)

    expected_days = (end - start).days + 1
    perp_minute = pd.concat(perp_frames, ignore_index=True)
    spot_minute = pd.concat(spot_frames, ignore_index=True)
    for label, minute in (("perpetual", perp_minute), ("spot", spot_minute)):
        if minute["open_time_dt"].duplicated().any():
            raise RuntimeError(f"duplicate {label} minute for {symbol}")
        if len(minute) < expected_days * 1_430:
            raise RuntimeError(
                f"incomplete {label} {symbol}: {len(minute)} rows for {expected_days} days",
            )

    perp = _resample_15m(perp_minute)
    spot = _resample_15m(spot_minute)
    frame = perp.join(spot, how="inner", lsuffix="_perp", rsuffix="_spot")
    if len(frame) < expected_days * 96 * 0.97:
        raise RuntimeError(f"perpetual/spot 15-minute join lost too many rows for {symbol}")

    prior_close = frame["close_perp"].shift(1)
    true_range = pd.concat(
        [
            frame["high_perp"] - frame["low_perp"],
            (frame["high_perp"] - prior_close).abs(),
            (frame["low_perp"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr_price"] = true_range.shift(1).rolling(32, min_periods=32).median()
    past_range = true_range.shift(1).rolling(32, min_periods=32).median()
    frame["range_burst"] = (
        (frame["high_perp"] - frame["low_perp"])
        / past_range.replace(0.0, np.nan)
    )
    for venue in ("perp", "spot"):
        past_volume = frame[f"quote_volume_{venue}"].shift(1).rolling(
            32, min_periods=32,
        ).median()
        frame[f"volume_burst_{venue}"] = (
            frame[f"quote_volume_{venue}"] / past_volume.replace(0.0, np.nan)
        )
        bar_range = (
            frame[f"high_{venue}"] - frame[f"low_{venue}"]
        ).replace(0.0, np.nan)
        frame[f"close_location_{venue}"] = (
            frame[f"close_{venue}"] - frame[f"low_{venue}"]
        ) / bar_range

    # Completed prior four-hour block versus the preceding four-hour block.
    for field, reducer in (("low", "min"), ("high", "max")):
        series = frame[f"{field}_perp"]
        last = series.shift(1).rolling(CONTEXT_BLOCK_BARS, min_periods=CONTEXT_BLOCK_BARS)
        previous = series.shift(CONTEXT_BLOCK_BARS + 1).rolling(
            CONTEXT_BLOCK_BARS, min_periods=CONTEXT_BLOCK_BARS,
        )
        frame[f"last_4h_{field}"] = getattr(last, reducer)()
        frame[f"previous_4h_{field}"] = getattr(previous, reducer)()
    frame["prior_close"] = frame["close_perp"].shift(1)
    frame["close_4h_ago"] = frame["close_perp"].shift(CONTEXT_BLOCK_BARS + 1)
    frame["last_4h_mid"] = (
        frame["last_4h_high"] + frame["last_4h_low"]
    ) / 2.0
    frame["ready"] = frame[
        [
            "atr_price",
            "range_burst",
            "volume_burst_perp",
            "volume_burst_spot",
            "close_location_perp",
            "close_location_spot",
            "last_4h_high",
            "last_4h_low",
            "previous_4h_high",
            "previous_4h_low",
            "prior_close",
            "close_4h_ago",
        ]
    ].notna().all(axis=1)
    return frame, evidence


def _repeated_level(
    frame: pd.DataFrame,
    event_index: int,
    direction: int,
    atr: float,
) -> tuple[float, int, float] | None:
    start = event_index - LIQUIDITY_LOOKBACK_BARS
    if start < 0:
        return None
    window = frame.iloc[start:event_index]
    if direction > 0:
        boundary = float(window["low_perp"].min())
        touches = np.flatnonzero(
            window["low_perp"].to_numpy(dtype=float)
            <= boundary + LEVEL_TOLERANCE_ATR * atr
        )
    else:
        boundary = float(window["high_perp"].max())
        touches = np.flatnonzero(
            window["high_perp"].to_numpy(dtype=float)
            >= boundary - LEVEL_TOLERANCE_ATR * atr
        )
    if len(touches) < MIN_TOUCHES:
        return None
    separated: list[int] = [int(touches[0])]
    for touch in touches[1:]:
        if int(touch) - separated[-1] >= MIN_TOUCH_SEPARATION_BARS:
            separated.append(int(touch))
    if len(separated) < MIN_TOUCHES:
        return None
    first, last = separated[0], separated[-1]
    between = window.iloc[first : last + 1]
    if direction > 0:
        bounce = float(between["high_perp"].max()) - boundary
    else:
        bounce = boundary - float(between["low_perp"].min())
    if bounce < MIN_BOUNCE_ATR * atr:
        return None
    return boundary, len(separated), bounce / atr


def _context_direction(row: pd.Series) -> int:
    long_context = bool(
        row["last_4h_low"] > row["previous_4h_low"]
        and row["prior_close"] > row["close_4h_ago"]
        and row["prior_close"] > row["last_4h_mid"]
    )
    short_context = bool(
        row["last_4h_high"] < row["previous_4h_high"]
        and row["prior_close"] < row["close_4h_ago"]
        and row["prior_close"] < row["last_4h_mid"]
    )
    if long_context == short_context:
        return 0
    return 1 if long_context else -1


def _interaction(
    row: pd.Series,
    direction: int,
    boundary: float,
    atr: float,
) -> bool:
    common = bool(
        row["range_burst"] >= MIN_RANGE_BURST
        and row["volume_burst_perp"] >= MIN_PERP_VOLUME_BURST
        and row["volume_burst_spot"] >= MIN_SPOT_VOLUME_BURST
    )
    if not common:
        return False
    if direction > 0:
        return bool(
            row["low_perp"] <= boundary - MIN_SWEEP_ATR * atr
            and row["close_perp"] >= boundary + MIN_RECLAIM_ATR * atr
            and row["close_location_perp"] >= MIN_RECLAIM_CLOSE_LOCATION
            and row["close_spot"] > row["open_spot"]
            and row["close_location_spot"] >= MIN_RECLAIM_CLOSE_LOCATION
        )
    return bool(
        row["high_perp"] >= boundary + MIN_SWEEP_ATR * atr
        and row["close_perp"] <= boundary - MIN_RECLAIM_ATR * atr
        and row["close_location_perp"] <= MAX_RECLAIM_CLOSE_LOCATION_SHORT
        and row["close_spot"] < row["open_spot"]
        and row["close_location_spot"] <= MAX_RECLAIM_CLOSE_LOCATION_SHORT
    )


def _hold_transition(
    frame: pd.DataFrame,
    event_index: int,
    direction: int,
    boundary: float,
    atr: float,
) -> bool:
    if event_index + 1 >= len(frame):
        return False
    row = frame.iloc[event_index + 1]
    if direction > 0:
        return bool(
            row["low_perp"] >= boundary - MAX_HOLD_PENETRATION_ATR * atr
            and row["close_perp"] > boundary
            and row["close_location_perp"] >= 0.50
            and row["close_spot"] >= row["open_spot"]
        )
    return bool(
        row["high_perp"] <= boundary + MAX_HOLD_PENETRATION_ATR * atr
        and row["close_perp"] < boundary
        and row["close_location_perp"] <= 0.50
        and row["close_spot"] <= row["open_spot"]
    )


def _geometry(
    frame: pd.DataFrame,
    event_index: int,
    entry_index: int,
    direction: int,
    atr: float,
) -> dict[str, float] | None:
    if entry_index >= len(frame):
        return None
    event = frame.iloc[event_index]
    entry = float(frame.iloc[entry_index]["open_perp"])
    if direction > 0:
        stop = float(event["low_perp"]) - STOP_BUFFER_ATR * atr
        if entry_index > event_index + 1:
            stop = min(stop, float(frame.iloc[event_index + 1]["low_perp"]) - STOP_BUFFER_ATR * atr)
        target = float(event["last_4h_high"])
        gross_reward_bps = math.log(target / entry) * 10_000.0
        gross_risk_bps = math.log(entry / stop) * 10_000.0
    else:
        stop = float(event["high_perp"]) + STOP_BUFFER_ATR * atr
        if entry_index > event_index + 1:
            stop = max(stop, float(frame.iloc[event_index + 1]["high_perp"]) + STOP_BUFFER_ATR * atr)
        target = float(event["last_4h_low"])
        gross_reward_bps = math.log(entry / target) * 10_000.0
        gross_risk_bps = math.log(stop / entry) * 10_000.0
    if not (target > 0.0 and stop > 0.0 and entry > 0.0):
        return None
    net_reward_bps = gross_reward_bps - ROUND_TRIP_HURDLE_BPS
    planned_loss_bps = gross_risk_bps + ROUND_TRIP_HURDLE_BPS
    if net_reward_bps <= 0.0 or planned_loss_bps <= 0.0:
        return None
    net_rr = net_reward_bps / planned_loss_bps
    if net_rr < MIN_NET_RR:
        return None
    return {
        "entry": entry,
        "stop": stop,
        "target": target,
        "gross_reward_bps": gross_reward_bps,
        "gross_risk_bps": gross_risk_bps,
        "net_reward_bps": net_reward_bps,
        "planned_loss_bps": planned_loss_bps,
        "net_rr": net_rr,
    }


def _simulate(
    frame: pd.DataFrame,
    entry_index: int,
    direction: int,
    geometry: dict[str, float],
) -> dict[str, Any]:
    entry = geometry["entry"]
    stop = geometry["stop"]
    target = geometry["target"]
    last_index = min(entry_index + MAX_HOLD_BARS - 1, len(frame) - 1)
    exit_price = float(frame.iloc[last_index]["close_perp"])
    exit_reason = "TIME"
    exit_index = last_index
    for index in range(entry_index, last_index + 1):
        row = frame.iloc[index]
        if direction > 0:
            stop_hit = float(row["low_perp"]) <= stop
            target_hit = float(row["high_perp"]) >= target
        else:
            stop_hit = float(row["high_perp"]) >= stop
            target_hit = float(row["low_perp"]) <= target
        # Conservative ordering when both are inside the same 15-minute bar.
        if stop_hit:
            exit_price = stop
            exit_reason = "STOP"
            exit_index = index
            break
        if target_hit:
            exit_price = target
            exit_reason = "TARGET"
            exit_index = index
            break
    gross_bps = direction * math.log(exit_price / entry) * 10_000.0
    return {
        "exit_timestamp": frame.index[exit_index].isoformat(),
        "exit_reason": exit_reason,
        "holding_bars": int(exit_index - entry_index + 1),
        "net_pnl_bps": gross_bps - ROUND_TRIP_HURDLE_BPS,
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
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events: list[dict[str, Any]] = []
    diagnostics = {
        "context_rows": 0,
        "repeated_level_rows": 0,
        "interactions": 0,
        "immediate_geometry_accepted": 0,
        "hold_transitions": 0,
        "hold_geometry_accepted": 0,
    }
    last_parent = -10**9
    start_index = CONTEXT_BLOCK_BARS * 2 + LIQUIDITY_LOOKBACK_BARS + 2
    for index in range(start_index, len(frame) - MAX_HOLD_BARS - 3):
        row = frame.iloc[index]
        if not bool(row["ready"]):
            continue
        direction = _context_direction(row)
        if direction == 0:
            continue
        diagnostics["context_rows"] += 1
        atr = float(row["atr_price"])
        repeated = _repeated_level(frame, index, direction, atr)
        if repeated is None:
            continue
        boundary, touches, bounce_atr = repeated
        diagnostics["repeated_level_rows"] += 1
        if index - last_parent < PARENT_COOLDOWN_BARS:
            continue
        if not _interaction(row, direction, boundary, atr):
            continue
        last_parent = index
        diagnostics["interactions"] += 1
        common = {
            "symbol": symbol,
            "parent_timestamp": frame.index[index].isoformat(),
            "direction": direction,
            "boundary": boundary,
            "touches": touches,
            "bounce_atr": bounce_atr,
            "atr_bps": atr / float(row["close_perp"]) * 10_000.0,
            "range_burst": float(row["range_burst"]),
            "perp_volume_burst": float(row["volume_burst_perp"]),
            "spot_volume_burst": float(row["volume_burst_spot"]),
            "perp_close_location": float(row["close_location_perp"]),
            "spot_close_location": float(row["close_location_spot"]),
        }

        immediate_entry = index + 1
        immediate_geometry = _geometry(
            frame, index, immediate_entry, direction, atr,
        )
        if immediate_geometry is not None:
            diagnostics["immediate_geometry_accepted"] += 1
            event = {
                **common,
                "family": "HTF_STOP_RUN_IMMEDIATE",
                "transition_timestamp": frame.index[index].isoformat(),
                "entry_timestamp": frame.index[immediate_entry].isoformat(),
                **immediate_geometry,
            }
            event.update(_simulate(frame, immediate_entry, direction, immediate_geometry))
            events.append(event)

        if _hold_transition(frame, index, direction, boundary, atr):
            diagnostics["hold_transitions"] += 1
            hold_entry = index + 2
            hold_geometry = _geometry(frame, index, hold_entry, direction, atr)
            if hold_geometry is not None:
                diagnostics["hold_geometry_accepted"] += 1
                event = {
                    **common,
                    "family": "HTF_STOP_RUN_HOLD",
                    "transition_timestamp": frame.index[index + 1].isoformat(),
                    "entry_timestamp": frame.index[hold_entry].isoformat(),
                    **hold_geometry,
                }
                event.update(_simulate(frame, hold_entry, direction, hold_geometry))
                events.append(event)
    return events, diagnostics


def run_screen(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    all_events: list[dict[str, Any]] = []
    raw_evidence: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    bar_counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        frame, evidence = _load_symbol(symbol, start, end, cache)
        bar_counts[symbol] = int(len(frame))
        raw_evidence.extend(evidence)
        events, symbol_diagnostics = _collect_symbol_events(symbol, frame)
        all_events.extend(events)
        diagnostics[symbol] = symbol_diagnostics

    events = pd.DataFrame(all_events)
    events.to_csv(output / "htf_stop_run_events.csv", index=False)
    families = ("HTF_STOP_RUN_IMMEDIATE", "HTF_STOP_RUN_HOLD")
    results: dict[str, Any] = {}
    promising: list[dict[str, Any]] = []
    for family in families:
        subset = events[events["family"] == family] if not events.empty else pd.DataFrame()
        overall = _summary(
            subset["net_pnl_bps"] if not subset.empty else pd.Series(dtype=float),
        )
        target_rate = (
            float((subset["exit_reason"] == "TARGET").mean()) if not subset.empty else None
        )
        stop_rate = (
            float((subset["exit_reason"] == "STOP").mean()) if not subset.empty else None
        )
        by_symbol = {
            symbol: _summary(subset.loc[subset["symbol"] == symbol, "net_pnl_bps"])
            for symbol in SYMBOLS
        } if not subset.empty else {symbol: _summary(pd.Series(dtype=float)) for symbol in SYMBOLS}
        results[family] = {
            "overall": overall,
            "target_rate": target_rate,
            "stop_rate": stop_rate,
            "symbol_counts": (
                subset["symbol"].value_counts().sort_index().to_dict()
                if not subset.empty else {}
            ),
            "by_symbol": by_symbol,
        }
        if (
            overall["count"] >= 20
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
        "schema": "external-htf-stop-run-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "bar_minutes": BAR_MINUTES,
        "bar_counts": bar_counts,
        "entry_timing": (
            "next 15-minute open after reclaim, or next open after one strictly later hold bar"
        ),
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "max_holding_minutes": MAX_HOLD_BARS * BAR_MINUTES,
        "parent_cooldown_minutes": PARENT_COOLDOWN_BARS * BAR_MINUTES,
        "fixed_parameters": {
            "liquidity_lookback_bars": LIQUIDITY_LOOKBACK_BARS,
            "context_block_bars": CONTEXT_BLOCK_BARS,
            "min_touches": MIN_TOUCHES,
            "min_touch_separation_bars": MIN_TOUCH_SEPARATION_BARS,
            "level_tolerance_atr": LEVEL_TOLERANCE_ATR,
            "min_bounce_atr": MIN_BOUNCE_ATR,
            "min_sweep_atr": MIN_SWEEP_ATR,
            "min_reclaim_atr": MIN_RECLAIM_ATR,
            "min_perp_volume_burst": MIN_PERP_VOLUME_BURST,
            "min_spot_volume_burst": MIN_SPOT_VOLUME_BURST,
            "min_range_burst": MIN_RANGE_BURST,
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
            "A pass indicates pre-entry geometry and post-cost movement in the higher-timeframe "
            "stop-run scenario.  It is not a NautilusTrader result."
        ),
    }
    (output / "htf_stop_run_screen.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "raw_evidence.json").write_text(
        json.dumps(raw_evidence, indent=2, sort_keys=True) + "\n",
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
