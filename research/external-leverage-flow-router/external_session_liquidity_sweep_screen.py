#!/usr/bin/env python3
"""Fixed London/New-York session liquidity sweep screen.

The scenario is a mechanized version of a common discretionary session model,
not a generic sweep rule:

    completed higher-timeframe directional context
    -> a fully formed session range becomes the visible liquidity objective
    -> the next major local-market open sweeps the counter-trend boundary with
       aggressive perpetual flow but weak/non-confirming spot flow
    -> a strictly later five-minute bar reclaims the old boundary with opposing
       spot flow
    -> entry at the next open, invalidation beyond the complete sweep episode,
       objective at the opposite session boundary

London and New-York opens are calculated in their local time zones, so DST is
handled rather than hard-coded to one UTC hour.  The Asian range begins at
00:00 UTC and ends at the London cash open.  The Europe range begins at the
London open and ends at the New-York cash open.  Only the first two hours after
an open can create a parent event, and only one parent is admitted per
symbol/session/day.

All observations are completed five-minute spot/perpetual bars.  Entry is at a
strictly later open.  A fixed 20 bp round-trip hurdle is charged and geometry
must provide net reward/risk >= 1 before admission.  Same-bar ambiguity is
resolved against the strategy.  This is an event screen; a passing family still
requires NautilusTrader actual-fill and continuous-account validation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

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
CONTEXT_BARS = 48
PREVIOUS_CONTEXT_BARS = 48
OPEN_WINDOW_BARS = 24
MAX_TRANSITION_BARS = 3
MAX_HOLD_BARS = 48
ROUND_TRIP_HURDLE_BPS = 20.0
MIN_NET_RR = 1.0
MIN_SWEEP_ATR = 0.10
MIN_RECLAIM_ATR = 0.05
STOP_BUFFER_ATR = 0.05
MIN_PERP_VOLUME_BURST = 1.50
MIN_SWEEP_FLOW = 0.10
MAX_SPOT_CONFIRMATION_FLOW = 0.05
MIN_RECLAIM_SPOT_FLOW = 0.10
MIN_RECLAIM_CLOSE_LOCATION = 0.60
MIN_SESSION_WIDTH_ATR = 2.0
LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _resample_5m(minute: pd.DataFrame) -> pd.DataFrame:
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

    perp = _resample_5m(perp_minute)
    spot = _resample_5m(spot_minute)
    frame = perp.join(spot, how="inner", lsuffix="_perp", rsuffix="_spot")
    frame = frame.sort_index()
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
        CONTEXT_BARS, min_periods=CONTEXT_BARS,
    ).median()
    past_volume = frame["quote_volume_perp"].shift(1).rolling(
        CONTEXT_BARS, min_periods=CONTEXT_BARS,
    ).median()
    frame["perp_volume_burst"] = (
        frame["quote_volume_perp"] / past_volume.replace(0.0, np.nan)
    )
    frame["last_context_low"] = frame["low_perp"].shift(1).rolling(
        CONTEXT_BARS, min_periods=CONTEXT_BARS,
    ).min()
    frame["last_context_high"] = frame["high_perp"].shift(1).rolling(
        CONTEXT_BARS, min_periods=CONTEXT_BARS,
    ).max()
    frame["previous_context_low"] = frame["low_perp"].shift(
        CONTEXT_BARS + 1,
    ).rolling(PREVIOUS_CONTEXT_BARS, min_periods=PREVIOUS_CONTEXT_BARS).min()
    frame["previous_context_high"] = frame["high_perp"].shift(
        CONTEXT_BARS + 1,
    ).rolling(PREVIOUS_CONTEXT_BARS, min_periods=PREVIOUS_CONTEXT_BARS).max()
    frame["prior_close"] = frame["close_perp"].shift(1)
    frame["context_open"] = frame["open_perp"].shift(CONTEXT_BARS)
    return frame, evidence


def _local_open(day: date, zone: ZoneInfo, local_time: time) -> pd.Timestamp:
    local = datetime.combine(day, local_time, tzinfo=zone)
    return pd.Timestamp(local.astimezone(UTC))


def _context_direction(row: pd.Series) -> int:
    long_context = bool(
        row["last_context_low"] > row["previous_context_low"]
        and row["prior_close"] > row["context_open"]
    )
    short_context = bool(
        row["last_context_high"] < row["previous_context_high"]
        and row["prior_close"] < row["context_open"]
    )
    if long_context == short_context:
        return 0
    return 1 if long_context else -1


def _session_specs(day: date) -> list[dict[str, Any]]:
    day_start = pd.Timestamp(datetime.combine(day, time(0, 0), tzinfo=UTC))
    london_open = _local_open(day, LONDON, time(8, 0))
    ny_open = _local_open(day, NEW_YORK, time(9, 30))
    return [
        {
            "name": "ASIA_TO_LONDON",
            "range_start": day_start,
            "range_end": london_open,
            "window_start": london_open,
            "window_end": london_open + timedelta(hours=2),
        },
        {
            "name": "EUROPE_TO_NEW_YORK",
            "range_start": london_open,
            "range_end": ny_open,
            "window_start": ny_open,
            "window_end": ny_open + timedelta(hours=2),
        },
    ]


def _geometry(
    frame: pd.DataFrame,
    sweep_index: int,
    transition_index: int,
    direction: int,
    range_low: float,
    range_high: float,
    atr: float,
) -> dict[str, float] | None:
    entry_index = transition_index + 1
    if entry_index >= len(frame):
        return None
    episode = frame.iloc[sweep_index : transition_index + 1]
    entry = float(frame.iloc[entry_index]["open_perp"])
    if direction > 0:
        stop = float(episode["low_perp"].min()) - STOP_BUFFER_ATR * atr
        target = range_high
        if not (0.0 < stop < entry < target):
            return None
        gross_reward = math.log(target / entry) * 10_000.0
        gross_risk = math.log(entry / stop) * 10_000.0
    else:
        stop = float(episode["high_perp"].max()) + STOP_BUFFER_ATR * atr
        target = range_low
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
        "entry": entry,
        "stop": stop,
        "target": target,
        "gross_reward_bps": gross_reward,
        "gross_risk_bps": gross_risk,
        "net_reward_bps": net_reward,
        "planned_loss_bps": planned_loss,
        "net_rr": net_rr,
    }


def _simulate(
    frame: pd.DataFrame,
    direction: int,
    geometry: dict[str, float],
    hard_exit_time: pd.Timestamp,
) -> dict[str, Any]:
    entry_index = int(geometry["entry_index"])
    entry = geometry["entry"]
    stop = geometry["stop"]
    target = geometry["target"]
    time_exit_candidates = np.flatnonzero(frame.index >= hard_exit_time)
    time_exit_index = (
        int(time_exit_candidates[0] - 1)
        if len(time_exit_candidates) and time_exit_candidates[0] > entry_index
        else entry_index + MAX_HOLD_BARS - 1
    )
    last_index = min(entry_index + MAX_HOLD_BARS - 1, time_exit_index, len(frame) - 1)
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
        "session_ranges": 0,
        "context_aligned_sessions": 0,
        "wide_enough_ranges": 0,
        "sweeps": 0,
        "transitions": 0,
        "geometry_accepted": 0,
    }
    index_locations = {timestamp: i for i, timestamp in enumerate(frame.index)}
    day = start
    while day <= end:
        for spec in _session_specs(day):
            range_window = frame[
                (frame.index >= spec["range_start"])
                & (frame.index < spec["range_end"])
            ]
            open_window = frame[
                (frame.index >= spec["window_start"])
                & (frame.index < spec["window_end"])
            ]
            expected_range_bars = int(
                (spec["range_end"] - spec["range_start"]).total_seconds()
                // (BAR_MINUTES * 60)
            )
            if len(range_window) < expected_range_bars * 0.95 or open_window.empty:
                continue
            diagnostics["session_ranges"] += 1
            first_timestamp = open_window.index[0]
            event_start_index = index_locations[first_timestamp]
            row_at_open = frame.iloc[event_start_index]
            direction = _context_direction(row_at_open)
            if direction == 0:
                continue
            diagnostics["context_aligned_sessions"] += 1
            atr = float(row_at_open["atr_price"])
            if not (math.isfinite(atr) and atr > 0.0):
                continue
            range_low = float(range_window["low_perp"].min())
            range_high = float(range_window["high_perp"].max())
            if range_high - range_low < MIN_SESSION_WIDTH_ATR * atr:
                continue
            diagnostics["wide_enough_ranges"] += 1

            sweep_index: int | None = None
            boundary = range_low if direction > 0 else range_high
            for timestamp, row in open_window.iterrows():
                index = index_locations[timestamp]
                if direction > 0:
                    swept = float(row["low_perp"]) <= boundary - MIN_SWEEP_ATR * atr
                    perp_aggression = float(row["flow_perp"]) <= -MIN_SWEEP_FLOW
                    spot_nonconfirmation = float(row["flow_spot"]) >= -MAX_SPOT_CONFIRMATION_FLOW
                else:
                    swept = float(row["high_perp"]) >= boundary + MIN_SWEEP_ATR * atr
                    perp_aggression = float(row["flow_perp"]) >= MIN_SWEEP_FLOW
                    spot_nonconfirmation = float(row["flow_spot"]) <= MAX_SPOT_CONFIRMATION_FLOW
                if (
                    swept
                    and perp_aggression
                    and spot_nonconfirmation
                    and float(row["perp_volume_burst"]) >= MIN_PERP_VOLUME_BURST
                ):
                    sweep_index = index
                    break
            if sweep_index is None:
                continue
            diagnostics["sweeps"] += 1

            transition_index: int | None = None
            for index in range(
                sweep_index + 1,
                min(sweep_index + MAX_TRANSITION_BARS + 1, len(frame) - 1),
            ):
                row = frame.iloc[index]
                if frame.index[index] >= spec["window_end"]:
                    break
                if direction > 0:
                    reclaimed = float(row["close_perp"]) >= boundary + MIN_RECLAIM_ATR * atr
                    spot_reversal = float(row["flow_spot"]) >= MIN_RECLAIM_SPOT_FLOW
                    close_quality = float(row["close_location_perp"]) >= MIN_RECLAIM_CLOSE_LOCATION
                else:
                    reclaimed = float(row["close_perp"]) <= boundary - MIN_RECLAIM_ATR * atr
                    spot_reversal = float(row["flow_spot"]) <= -MIN_RECLAIM_SPOT_FLOW
                    close_quality = float(row["close_location_perp"]) <= 1.0 - MIN_RECLAIM_CLOSE_LOCATION
                if reclaimed and spot_reversal and close_quality:
                    transition_index = index
                    break
            if transition_index is None:
                continue
            diagnostics["transitions"] += 1
            geometry = _geometry(
                frame,
                sweep_index,
                transition_index,
                direction,
                range_low,
                range_high,
                atr,
            )
            if geometry is None:
                continue
            diagnostics["geometry_accepted"] += 1
            entry_index = int(geometry["entry_index"])
            sweep_episode = frame.iloc[sweep_index : transition_index + 1]
            event = {
                "symbol": symbol,
                "session_family": spec["name"],
                "range_start": spec["range_start"].isoformat(),
                "range_end": spec["range_end"].isoformat(),
                "window_start": spec["window_start"].isoformat(),
                "direction": direction,
                "range_width_bps": (range_high - range_low) / float(row_at_open["close_perp"]) * 10_000.0,
                "range_width_atr": (range_high - range_low) / atr,
                "sweep_timestamp": frame.index[sweep_index].isoformat(),
                "transition_timestamp": frame.index[transition_index].isoformat(),
                "entry_timestamp": frame.index[entry_index].isoformat(),
                "sweep_flow_perp": float(frame.iloc[sweep_index]["flow_perp"]),
                "sweep_flow_spot": float(frame.iloc[sweep_index]["flow_spot"]),
                "reclaim_flow_spot": float(frame.iloc[transition_index]["flow_spot"]),
                "sweep_depth_atr": (
                    (range_low - float(sweep_episode["low_perp"].min())) / atr
                    if direction > 0
                    else (float(sweep_episode["high_perp"].max()) - range_high) / atr
                ),
                **{key: value for key, value in geometry.items() if key != "entry_index"},
            }
            hard_exit = min(
                spec["window_end"] + timedelta(hours=4),
                pd.Timestamp(datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=UTC)),
            )
            event.update(_simulate(frame, direction, geometry, hard_exit))
            events.append(event)
        day += timedelta(days=1)
    return events, diagnostics


def run_screen(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    all_events: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    bar_counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        frame, raw = _load_symbol(symbol, start, end, cache)
        bar_counts[symbol] = int(len(frame))
        evidence.extend(raw)
        symbol_events, symbol_diagnostics = _collect_symbol_events(
            symbol, frame, start, end,
        )
        all_events.extend(symbol_events)
        diagnostics[symbol] = symbol_diagnostics

    events = pd.DataFrame(all_events)
    events.to_csv(output / "session_sweep_events.csv", index=False)
    overall = _summary(
        events["net_pnl_bps"] if not events.empty else pd.Series(dtype=float),
    )
    by_session = {
        session: _summary(events.loc[events["session_family"] == session, "net_pnl_bps"])
        for session in ("ASIA_TO_LONDON", "EUROPE_TO_NEW_YORK")
    } if not events.empty else {
        session: _summary(pd.Series(dtype=float))
        for session in ("ASIA_TO_LONDON", "EUROPE_TO_NEW_YORK")
    }
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
        promising.append({"family": "SESSION_RANGE_SWEEP_RECLAIM", **overall})

    report = {
        "schema": "external-session-liquidity-sweep-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "bar_minutes": BAR_MINUTES,
        "bar_counts": bar_counts,
        "session_clock": "Europe/London 08:00 and America/New_York 09:30 with DST",
        "entry_timing": "strictly later five-minute open after completed reclaim",
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "max_holding_minutes": MAX_HOLD_BARS * BAR_MINUTES,
        "fixed_parameters": {
            "context_bars": CONTEXT_BARS,
            "open_window_bars": OPEN_WINDOW_BARS,
            "max_transition_bars": MAX_TRANSITION_BARS,
            "min_session_width_atr": MIN_SESSION_WIDTH_ATR,
            "min_sweep_atr": MIN_SWEEP_ATR,
            "min_perp_volume_burst": MIN_PERP_VOLUME_BURST,
            "min_sweep_flow": MIN_SWEEP_FLOW,
            "max_spot_confirmation_flow": MAX_SPOT_CONFIRMATION_FLOW,
            "min_reclaim_spot_flow": MIN_RECLAIM_SPOT_FLOW,
            "min_net_rr": MIN_NET_RR,
        },
        "diagnostics": diagnostics,
        "event_count": int(len(events)),
        "event_counts": (
            events.groupby(["session_family", "symbol"]).size().rename("count").reset_index().to_dict("records")
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
            "by_session": by_session,
            "by_symbol": by_symbol,
        },
        "promising_fixed_families": promising,
        "interpretation": (
            "A pass indicates economic space after a complete DST-aware session liquidity sequence. "
            "It is not a NautilusTrader result."
        ),
    }
    (output / "session_sweep_screen.json").write_text(
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
