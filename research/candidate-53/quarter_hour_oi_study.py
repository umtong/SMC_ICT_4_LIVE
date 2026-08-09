#!/usr/bin/env python3
"""Candidate 53 external quarter-hour order-imbalance mechanism screen.

This study is a direct adaptation of Kim & Hansen (2026), "The Quarter-Hour
Effect: Periodic Algorithmic Trading and Return Predictability in Cryptocurrency
Futures".  It deliberately tests the paper's medium-horizon mechanism before a
new strategy is built:

    quarter-hour boundary -> first 10s aggressive order imbalance ->
    delayed price continuation over 4-12h.

The project already has checksum-verified Binance USD-M kline/aggTrades parsing
in Candidate 05.  We reuse it rather than implementing another market-data
layer.  Candidate 05's opening-window signed notional is used as a close proxy
for the paper's signed-volume imbalance; over ten seconds the two have nearly
identical sign and very similar normalized intensity.

Causality is stricter than the paper's ideal 10-second execution: the decision
is delayed until the entire boundary minute has completed.  That intentionally
sacrifices about 50 seconds so this diagnostic cannot benefit from information
which Candidate 53's current minute-bar research environment could not yet have
published.  Medium-horizon 4-12h effects should survive that delay if they are
large enough to matter.

No fills, account, portfolio, leverage or NAV are simulated here.  Outcomes are
forward-return mechanism evidence only.  The project's current 21 bp round-trip
fee/slippage budget is subtracted as an explicit economic hurdle.  If an
extreme, causally defined imbalance tail cannot clear that hurdle, the family is
not promoted to NautilusTrader.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from features import aggregate_agg_trades, download_checked, read_kline

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
HORIZONS = (30, 60, 120, 240, 480, 720)
TAIL_QUANTILES = (0.50, 0.75, 0.90, 0.95, 0.975, 0.99)
ROUND_TRIP_COST_BPS = 21.0
WARMUP_DAYS = 7
QH_EVENTS_PER_DAY = 96
TRAILING_EVENTS = WARMUP_DAYS * QH_EVENTS_PER_DAY
MIN_TRAILING_EVENTS = 3 * QH_EVENTS_PER_DAY


class StudyError(RuntimeError):
    pass


def _days(start: date, end: date) -> list[date]:
    return [item.date() for item in pd.date_range(start, end, freq="D")]


def _fetch_one(symbol: str, day: date, endpoint: str, cache: Path):
    path, checksum, evidence = download_checked(endpoint, symbol, day, cache / symbol)
    if endpoint == "klines":
        frame = read_kline(path)
    elif endpoint == "aggTrades":
        frame = aggregate_agg_trades(path)
    else:
        raise ValueError(endpoint)
    return day, endpoint, frame, path, checksum, evidence


def load_symbol(symbol: str, start: date, end: date, cache: Path) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    work: dict[tuple[date, str], pd.DataFrame] = {}
    evidence: list[dict[str, object]] = []
    futures = []
    days = _days(start, end)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for day in days:
            for endpoint in ("klines", "aggTrades"):
                futures.append(pool.submit(_fetch_one, symbol, day, endpoint, cache))
        for future in as_completed(futures):
            day, endpoint, frame, path, checksum, record = future.result()
            work[(day, endpoint)] = frame
            item = asdict(record)
            item["archive"] = str(path)
            item["checksum"] = str(checksum)
            evidence.append(item)

    kline_frames = [work[(day, "klines")] for day in days]
    agg_frames = [work[(day, "aggTrades")] for day in days]
    klines = pd.concat(kline_frames, ignore_index=True).sort_values("open_time_dt", kind="stable")
    if klines["open_time_dt"].duplicated().any():
        raise StudyError(f"duplicate kline minutes: {symbol}")
    expected = len(days) * 1_440
    if len(klines) < expected - len(days):
        raise StudyError(f"incomplete kline panel {symbol}: {len(klines)} < {expected}")

    agg = pd.concat(agg_frames).sort_index()
    if agg.index.duplicated().any():
        # Daily files should not overlap, but use deterministic aggregation if
        # Binance ever repeats a boundary row.
        aggregations = {
            "trade_open": "first",
            "trade_high": "max",
            "trade_low": "min",
            "trade_close": "last",
            "quantity_60s": "sum",
            "notional_60s": "sum",
            "signed_notional_60s": "sum",
            "buy_notional_60s": "sum",
            "sell_notional_60s": "sum",
            "trade_count_60s": "sum",
            "path_60s_bps": "sum",
            "notional_15s": "sum",
            "signed_notional_15s": "sum",
            "trade_count_15s": "sum",
            "path_15s_bps": "sum",
            "notional_open_10s": "sum",
            "signed_notional_open_10s": "sum",
            "trade_count_open_10s": "sum",
        }
        agg = agg.groupby(level=0, sort=True).agg(aggregations)

    panel = klines.set_index("open_time_dt").join(agg, how="left")
    panel.index = pd.DatetimeIndex(panel.index).tz_convert("UTC")
    if panel.index.has_duplicates or not panel.index.is_monotonic_increasing:
        raise StudyError(f"invalid minute clock: {symbol}")
    for name in ("notional_open_10s", "signed_notional_open_10s", "trade_count_open_10s"):
        panel[name] = pd.to_numeric(panel[name], errors="coerce").fillna(0.0)
    denominator = panel["notional_open_10s"].replace(0.0, np.nan)
    panel["oi_open_10s"] = panel["signed_notional_open_10s"] / denominator
    panel["oi_open_10s"] = panel["oi_open_10s"].clip(-1.0, 1.0)
    return panel, sorted(evidence, key=lambda item: (str(item["day"]), str(item["endpoint"])))


def build_events(symbol: str, panel: pd.DataFrame, core_start: date, core_end: date) -> pd.DataFrame:
    qh = panel[(panel.index.minute % 15 == 0) & panel["oi_open_10s"].notna()].copy()
    qh["abs_oi"] = qh["oi_open_10s"].abs()
    history_abs = qh["abs_oi"].shift(1).rolling(TRAILING_EVENTS, min_periods=MIN_TRAILING_EVENTS)
    for quantile in TAIL_QUANTILES:
        qh[f"abs_oi_q{int(quantile * 1000):03d}"] = history_abs.quantile(quantile)
    history_notional = qh["notional_open_10s"].shift(1).rolling(
        TRAILING_EVENTS,
        min_periods=MIN_TRAILING_EVENTS,
    )
    qh["open10_notional_median"] = history_notional.median()
    qh["open10_burst"] = qh["notional_open_10s"] / qh["open10_notional_median"].replace(0.0, np.nan)
    qh["side"] = np.sign(qh["oi_open_10s"]).astype(int)
    qh["entry_price"] = pd.to_numeric(qh["close"], errors="coerce")
    qh["decision_ts"] = pd.to_datetime(qh["close_time_dt"], utc=True, errors="raise")

    close = pd.to_numeric(panel["close"], errors="coerce")
    for minutes in HORIZONS:
        future = close.shift(-minutes)
        aligned = future.reindex(qh.index)
        gross = qh["side"] * np.log(aligned / qh["entry_price"]) * 10_000.0
        qh[f"gross_bps_{minutes}"] = gross
        qh[f"net_bps_{minutes}"] = gross - ROUND_TRIP_COST_BPS

    core_open = pd.Timestamp(core_start, tz="UTC")
    core_close = pd.Timestamp(core_end + timedelta(days=1), tz="UTC")
    qh = qh[(qh.index >= core_open) & (qh.index < core_close)].copy()
    qh = qh[qh["side"].ne(0)].copy()
    qh["symbol"] = symbol
    qh["boundary_ts"] = qh.index
    keep = [
        "symbol",
        "boundary_ts",
        "decision_ts",
        "side",
        "entry_price",
        "oi_open_10s",
        "abs_oi",
        "notional_open_10s",
        "trade_count_open_10s",
        "open10_burst",
        *[f"abs_oi_q{int(q * 1000):03d}" for q in TAIL_QUANTILES],
        *[item for minutes in HORIZONS for item in (f"gross_bps_{minutes}", f"net_bps_{minutes}")],
    ]
    return qh[keep].reset_index(drop=True)


def global_strongest(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    ordered = events.sort_values(
        ["boundary_ts", "abs_oi", "notional_open_10s", "symbol"],
        ascending=[True, False, False, True],
        kind="stable",
    )
    return ordered.groupby("boundary_ts", sort=True, as_index=False).first()


def _tail_mask(frame: pd.DataFrame, quantile: float) -> pd.Series:
    threshold = frame[f"abs_oi_q{int(quantile * 1000):03d}"]
    return threshold.notna() & frame["abs_oi"].ge(threshold)


def _summary(frame: pd.DataFrame, horizon: int) -> dict[str, object]:
    values = pd.to_numeric(frame[f"gross_bps_{horizon}"], errors="coerce").dropna().to_numpy(dtype=float)
    if values.size == 0:
        return {
            "trades": 0,
            "mean_gross_bps": 0.0,
            "median_gross_bps": 0.0,
            "mean_net_bps": 0.0,
            "cost_clear_rate": 0.0,
            "direction_hit_rate": 0.0,
            "gross_profit_factor": 0.0,
        }
    gains = values[values > 0.0].sum()
    losses = -values[values < 0.0].sum()
    return {
        "trades": int(values.size),
        "mean_gross_bps": float(values.mean()),
        "median_gross_bps": float(np.median(values)),
        "mean_net_bps": float(values.mean() - ROUND_TRIP_COST_BPS),
        "cost_clear_rate": float((values > ROUND_TRIP_COST_BPS).mean()),
        "direction_hit_rate": float((values > 0.0).mean()),
        "gross_profit_factor": float(gains / losses) if losses > 0.0 else math.inf,
    }


def nonoverlap(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Descriptive independent-event subset under one active horizon at a time."""
    if frame.empty:
        return frame.copy()
    rows: list[int] = []
    free_at: pd.Timestamp | None = None
    ordered = frame.sort_values("decision_ts", kind="stable")
    for index, row in ordered.iterrows():
        ts = pd.Timestamp(row["decision_ts"])
        if free_at is not None and ts < free_at:
            continue
        rows.append(index)
        free_at = ts + pd.Timedelta(minutes=horizon)
    return ordered.loc[rows].copy()


def summarize(events: pd.DataFrame, start: date, end: date) -> dict[str, object]:
    global_events = global_strongest(events)
    calendar_days = (end - start).days + 1
    output: dict[str, object] = {
        "calendar_days": calendar_days,
        "all_symbol_events": len(events),
        "global_boundaries": len(global_events),
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "horizons_minutes": list(HORIZONS),
        "tail_quantiles": list(TAIL_QUANTILES),
        "all_symbols": {},
        "global_strongest": {},
    }
    for label, frame in (("all_symbols", events), ("global_strongest", global_events)):
        branch: dict[str, object] = {}
        for quantile in TAIL_QUANTILES:
            tail = frame.loc[_tail_mask(frame, quantile)].copy()
            qkey = f"q{quantile:.3f}"
            horizon_branch: dict[str, object] = {}
            for horizon in HORIZONS:
                independent = nonoverlap(tail, horizon)
                item = _summary(tail, horizon)
                independent_item = _summary(independent, horizon)
                independent_item["trades_per_calendar_day"] = len(independent) / calendar_days
                item["nonoverlap"] = independent_item
                horizon_branch[str(horizon)] = item
            branch[qkey] = horizon_branch
        output[label] = branch

    # Replication sanity check.  The paper reports short-run reversal followed by
    # medium-horizon continuation.  We do not demand exact magnitudes, but a
    # sign/profile mismatch is a warning to inspect implementation before making
    # a strategy inference.
    base = global_events.loc[_tail_mask(global_events, 0.50)]
    profile = {str(h): _summary(base, h)["mean_gross_bps"] for h in HORIZONS}
    output["replication_profile_mean_gross_bps"] = profile
    output["medium_exceeds_30m"] = bool(
        max(float(profile[str(h)]) for h in (240, 480, 720)) > float(profile["30"])
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    core_start = date.fromisoformat(args.start)
    core_end = date.fromisoformat(args.end)
    if core_end < core_start:
        raise ValueError("end precedes start")
    load_start = core_start - timedelta(days=WARMUP_DAYS)
    # Keep enough future data to evaluate the longest fixed horizon.
    load_end = core_end + timedelta(days=1)
    args.output.mkdir(parents=True, exist_ok=True)
    all_events: list[pd.DataFrame] = []
    all_evidence: dict[str, list[dict[str, object]]] = {}
    for symbol in SYMBOLS:
        panel, evidence = load_symbol(symbol, load_start, load_end, args.cache)
        all_evidence[symbol] = evidence
        all_events.append(build_events(symbol, panel, core_start, core_end))
    events = pd.concat(all_events, ignore_index=True)
    events["boundary_ts"] = pd.to_datetime(events["boundary_ts"], utc=True)
    events["decision_ts"] = pd.to_datetime(events["decision_ts"], utc=True)
    events = events.sort_values(["boundary_ts", "symbol"], kind="stable").reset_index(drop=True)
    result = summarize(events, core_start, core_end)
    events.to_csv(args.output / "events.csv.gz", index=False, compression="gzip")
    (args.output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (args.output / "raw_evidence.json").write_text(
        json.dumps(all_evidence, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
