#!/usr/bin/env python3
"""Opportunity-density scanner for the exact public NASOSv5 defaults.

This is not a backtest engine and never creates fills, positions or PnL.  It
reuses Candidate 47's checksum-verified Binance kline-only adapter, aggregates
completed 5m/15m candles, applies the public source's exact default entry
conditions, and reports causal rising-edge episodes plus structural
stop/objective geometry.  Only candidates with adequate opportunity density
are worth a NautilusTrader implementation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from e0v1e_reversion_strategy import _ema, _wilder_rsi
from kline_only_inputs import load_range

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

# Exact defaults from TheoBrigitte/freqtrade NASOSv5.py @ b9feaaa...
BASE_NB_CANDLES_BUY = 8
BASE_NB_CANDLES_SELL = 16
LOW_OFFSET = 0.981
LOW_OFFSET_2 = 0.942
HIGH_OFFSET = 1.097
EWO_HIGH = 3.553
EWO_HIGH_2 = -5.585
EWO_LOW = -14.378
RSI_BUY = 78.0
RSI_FAST_BUY = 37.0
LOOKBACK_15M = 32
PROFIT_THRESHOLD = 1.037


@dataclass(frozen=True, slots=True)
class ScanSummary:
    symbol: str
    rows_5m: int
    ready_states: int
    raw_signal_bars: int
    rising_edge_episodes: int
    valid_geometry_episodes: int
    ewo1_episodes: int
    ewo2_episodes: int
    ewolow_episodes: int
    mean_reward_risk: float | None
    median_reward_risk: float | None
    minimum_reward_risk: float | None
    mean_profit_space: float | None
    suppressed_no_profit_space: int


def _aggregate(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if minutes not in (5, 15):
        raise ValueError("only 5m and 15m aggregation are supported")
    work = frame.copy()
    work["open_time_dt"] = pd.to_datetime(work["open_time_dt"], utc=True)
    work["close_time_dt"] = pd.to_datetime(work["close_time_dt"], utc=True)
    work["bucket"] = work["open_time_dt"].dt.floor(f"{minutes}min")
    grouped = work.groupby("bucket", sort=True)
    result = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        close_time=("close_time_dt", "last"),
        source_rows=("close", "size"),
    )
    result = result[result["source_rows"].eq(minutes)].copy()
    result = result.drop(columns=["source_rows"]).reset_index(drop=True)
    if result.empty:
        raise RuntimeError(f"no complete {minutes}m candles")
    if result["close_time"].duplicated().any() or not result["close_time"].is_monotonic_increasing:
        raise RuntimeError(f"invalid {minutes}m close-time sequence")
    return result


def _series(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def scan_symbol(frame: pd.DataFrame, symbol: str) -> tuple[ScanSummary, list[dict[str, Any]]]:
    five = _aggregate(frame, 5)
    fifteen = _aggregate(frame, 15)
    fifteen["objective_15m_high"] = (
        fifteen["close"].rolling(LOOKBACK_15M, min_periods=LOOKBACK_15M).max()
    )
    aligned = pd.merge_asof(
        five.sort_values("close_time"),
        fifteen[["close_time", "objective_15m_high"]].sort_values("close_time"),
        on="close_time",
        direction="backward",
        allow_exact_matches=True,
    )

    closes = aligned["close"].astype(float).tolist()
    lows = aligned["low"].astype(float).tolist()
    aligned["ema_buy"] = _series(_ema(closes, BASE_NB_CANDLES_BUY))
    aligned["ema_sell"] = _series(_ema(closes, BASE_NB_CANDLES_SELL))
    aligned["ema_fast_ewo"] = _series(_ema(closes, 50))
    aligned["ema_slow_ewo"] = _series(_ema(closes, 200))
    aligned["rsi_fast"] = _series(_wilder_rsi(closes, 4))
    aligned["rsi"] = _series(_wilder_rsi(closes, 14))
    aligned["ewo"] = (
        (aligned["ema_fast_ewo"] - aligned["ema_slow_ewo"])
        / aligned["low"].replace(0.0, np.nan)
        * 100.0
    )
    aligned["support_low_20"] = aligned["low"].rolling(20, min_periods=20).min()
    aligned["profit_space"] = aligned["objective_15m_high"] / aligned["close"] - 1.0

    ready_columns = [
        "ema_buy",
        "ema_sell",
        "ema_fast_ewo",
        "ema_slow_ewo",
        "rsi_fast",
        "rsi",
        "ewo",
        "objective_15m_high",
        "support_low_20",
    ]
    ready = aligned[ready_columns].notna().all(axis=1) & aligned["volume"].gt(0.0)
    common = (
        ready
        & aligned["rsi_fast"].lt(RSI_FAST_BUY)
        & aligned["volume"].gt(0.0)
        & aligned["close"].lt(aligned["ema_sell"] * HIGH_OFFSET)
    )
    ewo1 = (
        common
        & aligned["close"].lt(aligned["ema_buy"] * LOW_OFFSET)
        & aligned["ewo"].gt(EWO_HIGH)
        & aligned["rsi"].lt(RSI_BUY)
    )
    ewo2 = (
        common
        & aligned["close"].lt(aligned["ema_buy"] * LOW_OFFSET_2)
        & aligned["ewo"].gt(EWO_HIGH_2)
        & aligned["rsi"].lt(RSI_BUY)
        & aligned["rsi"].lt(25.0)
    )
    ewolow = (
        common
        & aligned["close"].lt(aligned["ema_buy"] * LOW_OFFSET)
        & aligned["ewo"].lt(EWO_LOW)
    )
    raw = ewo1 | ewo2 | ewolow
    no_space = aligned["objective_15m_high"].lt(aligned["close"] * PROFIT_THRESHOLD)
    signal = raw & ~no_space.fillna(False)
    previous = signal.shift(1, fill_value=False)
    rising = signal & ~previous

    stop = np.maximum(
        aligned["close"].to_numpy(dtype=float) * 0.90,
        aligned["support_low_20"].to_numpy(dtype=float),
    )
    aligned["structural_stop"] = stop
    aligned["reward_risk"] = (
        (aligned["objective_15m_high"] - aligned["close"])
        / (aligned["close"] - aligned["structural_stop"])
    )
    valid_geometry = (
        rising
        & aligned["structural_stop"].lt(aligned["close"])
        & aligned["close"].lt(aligned["objective_15m_high"])
        & aligned["reward_risk"].gt(0.0)
        & np.isfinite(aligned["reward_risk"])
    )

    events: list[dict[str, Any]] = []
    for index in aligned.index[rising]:
        branches = [
            name
            for name, condition in (
                ("EWO1", bool(ewo1.iloc[index])),
                ("EWO2", bool(ewo2.iloc[index])),
                ("EWOLOW", bool(ewolow.iloc[index])),
            )
            if condition
        ]
        events.append(
            {
                "symbol": symbol,
                "close_time": aligned.at[index, "close_time"].isoformat(),
                "branches": branches,
                "close": float(aligned.at[index, "close"]),
                "structural_stop": float(aligned.at[index, "structural_stop"]),
                "objective_15m_high": float(aligned.at[index, "objective_15m_high"]),
                "reward_risk": float(aligned.at[index, "reward_risk"]),
                "profit_space": float(aligned.at[index, "profit_space"]),
                "rsi_fast": float(aligned.at[index, "rsi_fast"]),
                "rsi": float(aligned.at[index, "rsi"]),
                "ewo": float(aligned.at[index, "ewo"]),
                "valid_geometry": bool(valid_geometry.iloc[index]),
            }
        )

    valid_rr = aligned.loc[valid_geometry, "reward_risk"].astype(float)
    valid_space = aligned.loc[valid_geometry, "profit_space"].astype(float)
    rising_indices = aligned.index[rising]
    summary = ScanSummary(
        symbol=symbol,
        rows_5m=len(aligned),
        ready_states=int(ready.sum()),
        raw_signal_bars=int(raw.sum()),
        rising_edge_episodes=int(rising.sum()),
        valid_geometry_episodes=int(valid_geometry.sum()),
        ewo1_episodes=int(ewo1.loc[rising_indices].sum()) if len(rising_indices) else 0,
        ewo2_episodes=int(ewo2.loc[rising_indices].sum()) if len(rising_indices) else 0,
        ewolow_episodes=int(ewolow.loc[rising_indices].sum()) if len(rising_indices) else 0,
        mean_reward_risk=float(valid_rr.mean()) if not valid_rr.empty else None,
        median_reward_risk=float(valid_rr.median()) if not valid_rr.empty else None,
        minimum_reward_risk=float(valid_rr.min()) if not valid_rr.empty else None,
        mean_profit_space=float(valid_space.mean()) if not valid_space.empty else None,
        suppressed_no_profit_space=int((raw & no_space.fillna(False)).sum()),
    )
    return summary, events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    summaries: list[ScanSummary] = []
    events: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frame, _, _, _ = load_range(
            symbol=symbol,
            start=args.start,
            end=args.end,
            cache=args.cache / symbol,
            output=args.output / "source" / symbol,
        )
        summary, symbol_events = scan_symbol(frame, symbol)
        summaries.append(summary)
        events.extend(symbol_events)

    valid_events = [event for event in events if event["valid_geometry"]]
    timestamp_counts = Counter(event["close_time"] for event in valid_events)
    branch_counts = Counter(
        branch for event in valid_events for branch in event["branches"]
    )
    calendar_days = (args.end - args.start).days + 1
    report = {
        "source": {
            "repository": "TheoBrigitte/freqtrade",
            "commit": "b9feaaa2f845aed5612b3c7726a0590ee233c846",
            "path": "strategies/nasos/NASOSv5.py",
            "defaults": {
                "base_nb_candles_buy": BASE_NB_CANDLES_BUY,
                "base_nb_candles_sell": BASE_NB_CANDLES_SELL,
                "low_offset": LOW_OFFSET,
                "low_offset_2": LOW_OFFSET_2,
                "high_offset": HIGH_OFFSET,
                "ewo_high": EWO_HIGH,
                "ewo_high_2": EWO_HIGH_2,
                "ewo_low": EWO_LOW,
                "rsi_buy": RSI_BUY,
                "rsi_fast_buy": RSI_FAST_BUY,
                "lookback_15m": LOOKBACK_15M,
                "profit_threshold": PROFIT_THRESHOLD,
            },
        },
        "evaluation_start": args.start.isoformat(),
        "evaluation_end": args.end.isoformat(),
        "calendar_days": calendar_days,
        "summaries": [asdict(item) for item in summaries],
        "aggregate": {
            "ready_states": sum(item.ready_states for item in summaries),
            "raw_signal_bars": sum(item.raw_signal_bars for item in summaries),
            "rising_edge_episodes": len(events),
            "valid_geometry_episodes": len(valid_events),
            "valid_episodes_per_calendar_day": len(valid_events) / calendar_days,
            "branch_counts": dict(sorted(branch_counts.items())),
            "timestamps_with_multi_symbol_collision": sum(
                count > 1 for count in timestamp_counts.values()
            ),
            "maximum_same_timestamp_candidates": max(timestamp_counts.values(), default=0),
            "symbols_with_valid_events": sorted({event["symbol"] for event in valid_events}),
        },
        "events": events,
    }
    (args.output / "nasosv5_scan.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["aggregate"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
