#!/usr/bin/env python3
"""Fixed cross-asset first/last half-hour intraday momentum screen.

This screen adapts the published Bitcoin intraday time-series momentum idea to
our four-market, one-global-position constraint:

    one of three fixed eight-hour crypto sessions opens
    -> the completed first 30 minutes show an abnormal directional return plus
       high volume or volatility relative to that same session slot
    -> no trade is taken during the middle seven hours
    -> at the final 30-minute open, select the single strongest eligible market
       across BTC, ETH, SOL, and XRP
    -> trade in the first-half-hour direction and exit at the session close

The early observation and late execution are separated by seven hours, so there
is no same-bar confirmation or future leakage.  Selection is performed before
entry and exactly one symbol can be traded per session.  A protective stop is
fixed from the earlier opening range and prior ATR.  Same-bar stop ambiguity is
resolved against the strategy and a 20 bp round-trip hurdle is charged.

This is an economic screen, not a substitute for NautilusTrader.  A passing
family still requires actual fills, current-NAV 3% planned-loss sizing, funding,
and one continuous account.
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


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
SESSIONS = (
    ("ASIA_00_08", 0),
    ("EUROPE_08_16", 8),
    ("US_16_24", 16),
)
BAR_MINUTES = 30
SESSION_BARS = 16
SAME_SLOT_LOOKBACK = 20
ATR_BARS = 48
ROUND_TRIP_HURDLE_BPS = 20.0
MIN_FIRST_RETURN_BPS = 15.0
MIN_FIRST_RETURN_ATR = 0.75
MIN_VOLUME_BURST = 1.25
MIN_RANGE_BURST = 1.25
STOP_FIRST_RANGE_MULTIPLE = 1.0
STOP_ATR_MULTIPLE = 0.75


def _load_symbol(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    load_start = start - timedelta(days=SAME_SLOT_LOOKBACK + 3)
    minute_frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    day = load_start
    while day <= end:
        archive, _checksum, raw = download_checked(
            "klines", symbol, day, cache / symbol,
        )
        minute_frames.append(robust_read_kline(archive))
        evidence.append(asdict(raw))
        day += timedelta(days=1)
    minute = pd.concat(minute_frames, ignore_index=True).sort_values("open_time_dt")
    if minute["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate minute for {symbol}")
    minute = minute.set_index("open_time_dt")
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
    bars = bars[bars["minute_count"] == BAR_MINUTES].copy()
    prior_close = bars["close"].shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - prior_close).abs(),
            (bars["low"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["atr_price"] = true_range.shift(1).rolling(
        ATR_BARS, min_periods=ATR_BARS,
    ).median()
    bars["return_bps"] = np.log(bars["close"] / bars["open"]) * 10_000.0
    bars["range_price"] = bars["high"] - bars["low"]
    bars["range_atr"] = bars["range_price"] / bars["atr_price"].replace(0.0, np.nan)
    bars["return_atr"] = (
        (bars["close"] - bars["open"]) / bars["atr_price"].replace(0.0, np.nan)
    )
    return bars, evidence


def _first_slot_history(
    bars: pd.DataFrame,
    session_hour: int,
) -> pd.DataFrame:
    mask = (bars.index.hour == session_hour) & (bars.index.minute == 0)
    history = bars.loc[mask].copy()
    history["slot_volume_median"] = history["quote_volume"].shift(1).rolling(
        SAME_SLOT_LOOKBACK, min_periods=SAME_SLOT_LOOKBACK,
    ).median()
    history["slot_range_median"] = history["range_price"].shift(1).rolling(
        SAME_SLOT_LOOKBACK, min_periods=SAME_SLOT_LOOKBACK,
    ).median()
    history["volume_burst"] = (
        history["quote_volume"] / history["slot_volume_median"].replace(0.0, np.nan)
    )
    history["slot_range_burst"] = (
        history["range_price"] / history["slot_range_median"].replace(0.0, np.nan)
    )
    return history


def _candidate(
    symbol: str,
    bars: pd.DataFrame,
    first_history: pd.DataFrame,
    first_ts: pd.Timestamp,
    last_ts: pd.Timestamp,
) -> dict[str, Any] | None:
    if first_ts not in first_history.index or last_ts not in bars.index:
        return None
    first = first_history.loc[first_ts]
    last = bars.loc[last_ts]
    needed = (
        first["atr_price"],
        first["return_bps"],
        first["return_atr"],
        first["volume_burst"],
        first["slot_range_burst"],
    )
    if not all(math.isfinite(float(value)) for value in needed):
        return None
    if not (
        abs(float(first["return_bps"])) >= MIN_FIRST_RETURN_BPS
        and abs(float(first["return_atr"])) >= MIN_FIRST_RETURN_ATR
        and (
            float(first["volume_burst"]) >= MIN_VOLUME_BURST
            or float(first["slot_range_burst"]) >= MIN_RANGE_BURST
        )
    ):
        return None
    direction = 1 if float(first["return_bps"]) > 0.0 else -1
    score = (
        abs(float(first["return_atr"]))
        * max(float(first["volume_burst"]), float(first["slot_range_burst"]))
    )
    entry = float(last["open"])
    stop_distance = max(
        STOP_FIRST_RANGE_MULTIPLE * float(first["range_price"]),
        STOP_ATR_MULTIPLE * float(first["atr_price"]),
    )
    stop = entry - direction * stop_distance
    if stop <= 0.0:
        return None
    if direction > 0:
        stop_hit = float(last["low"]) <= stop
    else:
        stop_hit = float(last["high"]) >= stop
    exit_price = stop if stop_hit else float(last["close"])
    exit_reason = "STOP" if stop_hit else "SESSION_CLOSE"
    gross_bps = direction * math.log(exit_price / entry) * 10_000.0
    return {
        "symbol": symbol,
        "direction": direction,
        "first_timestamp": first_ts.isoformat(),
        "entry_timestamp": last_ts.isoformat(),
        "exit_timestamp": (last_ts + timedelta(minutes=BAR_MINUTES)).isoformat(),
        "score": score,
        "first_return_bps": float(first["return_bps"]),
        "first_return_atr": float(first["return_atr"]),
        "first_volume_burst": float(first["volume_burst"]),
        "first_range_burst": float(first["slot_range_burst"]),
        "entry": entry,
        "stop": stop,
        "planned_loss_bps": abs(math.log(entry / stop)) * 10_000.0 + ROUND_TRIP_HURDLE_BPS,
        "exit_reason": exit_reason,
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


def run_screen(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    history_by_symbol_session: dict[tuple[str, int], pd.DataFrame] = {}
    evidence: list[dict[str, Any]] = []
    bar_counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        bars, raw = _load_symbol(symbol, start, end, cache)
        bars_by_symbol[symbol] = bars
        evidence.extend(raw)
        bar_counts[symbol] = int(len(bars))
        for _name, hour in SESSIONS:
            history_by_symbol_session[(symbol, hour)] = _first_slot_history(bars, hour)

    selected: list[dict[str, Any]] = []
    eligible_count = 0
    day = start
    while day <= end:
        for session_name, hour in SESSIONS:
            first_ts = pd.Timestamp(day, tz="UTC") + timedelta(hours=hour)
            last_ts = first_ts + timedelta(hours=7, minutes=30)
            candidates: list[dict[str, Any]] = []
            for symbol in SYMBOLS:
                candidate = _candidate(
                    symbol,
                    bars_by_symbol[symbol],
                    history_by_symbol_session[(symbol, hour)],
                    first_ts,
                    last_ts,
                )
                if candidate is not None:
                    candidate["session"] = session_name
                    candidates.append(candidate)
            eligible_count += len(candidates)
            if candidates:
                candidates.sort(key=lambda item: (-item["score"], item["symbol"]))
                winner = candidates[0]
                winner["eligible_markets"] = len(candidates)
                selected.append(winner)
        day += timedelta(days=1)

    events = pd.DataFrame(selected)
    events.to_csv(output / "first_last_momentum_events.csv", index=False)
    overall = _summary(
        events["net_pnl_bps"] if not events.empty else pd.Series(dtype=float),
    )
    by_session = {
        name: _summary(events.loc[events["session"] == name, "net_pnl_bps"])
        for name, _hour in SESSIONS
    } if not events.empty else {
        name: _summary(pd.Series(dtype=float)) for name, _hour in SESSIONS
    }
    by_symbol = {
        symbol: _summary(events.loc[events["symbol"] == symbol, "net_pnl_bps"])
        for symbol in SYMBOLS
    } if not events.empty else {
        symbol: _summary(pd.Series(dtype=float)) for symbol in SYMBOLS
    }
    promising = []
    if (
        overall["count"] >= 60
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
        promising.append({"family": "FIRST_LAST_HALF_HOUR_MOMENTUM", **overall})

    report = {
        "schema": "external-session-first-last-momentum-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "sessions": [name for name, _hour in SESSIONS],
        "bar_minutes": BAR_MINUTES,
        "bar_counts": bar_counts,
        "entry_timing": "final half-hour open, seven hours after completed first half-hour",
        "global_arbitration": "single highest pre-entry score across four markets per session",
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "fixed_parameters": {
            "same_slot_lookback": SAME_SLOT_LOOKBACK,
            "min_first_return_bps": MIN_FIRST_RETURN_BPS,
            "min_first_return_atr": MIN_FIRST_RETURN_ATR,
            "min_volume_burst": MIN_VOLUME_BURST,
            "min_range_burst": MIN_RANGE_BURST,
            "stop_first_range_multiple": STOP_FIRST_RANGE_MULTIPLE,
            "stop_atr_multiple": STOP_ATR_MULTIPLE,
        },
        "eligible_market_signals": eligible_count,
        "selected_trade_count": int(len(events)),
        "selected_counts": (
            events.groupby(["session", "symbol"]).size().rename("count").reset_index().to_dict("records")
            if not events.empty else []
        ),
        "results": {
            "overall": overall,
            "stop_rate": (
                float((events["exit_reason"] == "STOP").mean()) if not events.empty else None
            ),
            "by_session": by_session,
            "by_symbol": by_symbol,
        },
        "promising_fixed_families": promising,
        "interpretation": (
            "A pass indicates that the early-session high-activity direction survives the actual one-global-position arbitration and 20 bp hurdle in the final half-hour. "
            "It is not a NautilusTrader result."
        ),
    }
    (output / "first_last_momentum_screen.json").write_text(
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
