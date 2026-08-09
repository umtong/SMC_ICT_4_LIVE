#!/usr/bin/env python3
"""Candidate 53 pressure/capacity/fragility state mechanism screen.

External microstructure work in 2026 converges on a state-first view: raw
aggressive flow is not enough.  What matters is pressure relative to displayed
liquidity capacity and whether that capacity is replenishing or withdrawing.
This study adapts that decision logic symmetrically to the project's four USD-M
perpetuals while reusing Candidate 05's checksum-verified aggTrades and
bookDepth ingestion.

Complete scenario families:

PRESSURE_BREAK
    extreme 5m aggressive pressure / opposing displayed capacity
    -> price already moves with pressure
    -> opposing capacity is withdrawing
    -> strictly later minute confirms the same direction
    -> test continuation from the confirmation close.

ABSORPTION_REVERSAL
    extreme 5m aggressive pressure / opposing displayed capacity
    -> price does not move with pressure
    -> opposing capacity is stable or replenishing
    -> strictly later minute confirms against pressure
    -> test reversal from the confirmation close.

The state bar never confirms itself.  All thresholds are trailing/shifted.  This
is mechanism evidence only, not a fill/account simulator.  The current 21 bp
round-trip cost is subtracted as the hurdle before any promotion to
NautilusTrader.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, timedelta
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from features import (
    aggregate_agg_trades,
    aggregate_book_depth,
    download_checked,
    read_kline,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
HORIZONS = (30, 60, 120, 240)
TAIL_QUANTILES = (0.90, 0.95, 0.975, 0.99)
ROUND_TRIP_COST_BPS = 21.0
WARMUP_DAYS = 7
TRAILING_MINUTES = WARMUP_DAYS * 1_440
MIN_TRAILING_MINUTES = 3 * 1_440
MAX_DEPTH_AGE_SECONDS = 120.0


class StudyError(RuntimeError):
    pass


def _days(start: date, end: date) -> list[date]:
    return [item.date() for item in pd.date_range(start, end, freq="D")]


def _fetch(symbol: str, day: date, endpoint: str, cache: Path):
    path, checksum, evidence = download_checked(endpoint, symbol, day, cache / symbol)
    if endpoint == "klines":
        frame = read_kline(path)
    elif endpoint == "aggTrades":
        frame = aggregate_agg_trades(path)
    elif endpoint == "bookDepth":
        frame = aggregate_book_depth(path)
    else:
        raise ValueError(endpoint)
    return day, endpoint, frame, path, checksum, evidence


def load_symbol(symbol: str, start: date, end: date, cache: Path) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    days = _days(start, end)
    work: dict[tuple[date, str], pd.DataFrame] = {}
    evidence: list[dict[str, object]] = []
    jobs = []
    with ThreadPoolExecutor(max_workers=9) as pool:
        for day in days:
            for endpoint in ("klines", "aggTrades", "bookDepth"):
                jobs.append(pool.submit(_fetch, symbol, day, endpoint, cache))
        for job in as_completed(jobs):
            day, endpoint, frame, path, checksum, record = job.result()
            work[(day, endpoint)] = frame
            item = asdict(record)
            item["archive"] = str(path)
            item["checksum"] = str(checksum)
            evidence.append(item)

    klines = pd.concat([work[(day, "klines")] for day in days], ignore_index=True).sort_values(
        "open_time_dt", kind="stable"
    )
    if klines["open_time_dt"].duplicated().any():
        raise StudyError(f"duplicate klines: {symbol}")
    agg = pd.concat([work[(day, "aggTrades")] for day in days]).sort_index()
    if agg.index.duplicated().any():
        agg = agg[~agg.index.duplicated(keep="last")]
    depth = pd.concat([work[(day, "bookDepth")] for day in days]).sort_index()
    if depth.index.duplicated().any():
        depth = depth[~depth.index.duplicated(keep="last")]

    panel = klines.set_index("open_time_dt").join(agg, how="left")
    depth = depth.reindex(panel.index).ffill()
    panel = panel.join(depth, how="left")
    if panel.index.has_duplicates or not panel.index.is_monotonic_increasing:
        raise StudyError(f"invalid minute clock: {symbol}")

    close_boundary = panel.index.to_series(index=panel.index) + pd.Timedelta(minutes=1)
    panel["depth_age_seconds"] = (close_boundary - panel["depth_snapshot_time"]).dt.total_seconds()
    valid_depth = panel["depth_age_seconds"].between(0.0, MAX_DEPTH_AGE_SECONDS)
    for column in ("bid_depth_1", "ask_depth_1"):
        panel[column] = pd.to_numeric(panel[column], errors="coerce").where(valid_depth)

    signed = pd.to_numeric(panel["signed_notional_60s"], errors="coerce").fillna(0.0)
    total = pd.to_numeric(panel["notional_60s"], errors="coerce").fillna(0.0)
    panel["signed_5m"] = signed.rolling(5, min_periods=5).sum()
    panel["notional_5m"] = total.rolling(5, min_periods=5).sum()
    panel["flow_5m"] = panel["signed_5m"] / panel["notional_5m"].replace(0.0, np.nan)
    panel["flow_side"] = np.sign(panel["flow_5m"])
    bid_change = panel["bid_depth_1"].pct_change(5, fill_method=None)
    ask_change = panel["ask_depth_1"].pct_change(5, fill_method=None)
    panel["opposing_capacity"] = np.where(
        panel["flow_side"] > 0.0,
        panel["ask_depth_1"],
        panel["bid_depth_1"],
    )
    panel["opposing_capacity_change_5m"] = np.where(
        panel["flow_side"] > 0.0,
        ask_change,
        bid_change,
    )
    panel["pressure_capacity"] = panel["signed_5m"].abs() / pd.Series(
        panel["opposing_capacity"], index=panel.index
    ).replace(0.0, np.nan)
    close = pd.to_numeric(panel["close"], errors="coerce")
    panel["ret_5m_bps"] = np.log(close / close.shift(5)) * 10_000.0
    panel["aligned_result_bps"] = panel["flow_side"] * panel["ret_5m_bps"]
    history = panel["pressure_capacity"].shift(1).rolling(
        TRAILING_MINUTES,
        min_periods=MIN_TRAILING_MINUTES,
    )
    for quantile in TAIL_QUANTILES:
        panel[f"pc_q{int(quantile * 1000):03d}"] = history.quantile(quantile)
    return panel, sorted(evidence, key=lambda x: (str(x["day"]), str(x["endpoint"])))


def build_events(symbol: str, panel: pd.DataFrame, core_start: date, core_end: date) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    close = pd.to_numeric(panel["close"], errors="coerce")
    values = panel.reset_index(names="minute")
    for i in range(1, len(values) - max(HORIZONS) - 2):
        row = values.iloc[i]
        pressure = float(row["pressure_capacity"])
        flow_side = int(np.sign(float(row["flow_5m"]))) if pd.notna(row["flow_5m"]) else 0
        aligned = float(row["aligned_result_bps"]) if pd.notna(row["aligned_result_bps"]) else math.nan
        capacity_change = (
            float(row["opposing_capacity_change_5m"])
            if pd.notna(row["opposing_capacity_change_5m"])
            else math.nan
        )
        if flow_side == 0 or not all(math.isfinite(v) for v in (pressure, aligned, capacity_change)):
            continue
        if aligned > 0.0 and capacity_change < 0.0:
            family = "PRESSURE_BREAK"
            side = flow_side
        elif aligned <= 0.0 and capacity_change >= 0.0:
            family = "ABSORPTION_REVERSAL"
            side = -flow_side
        else:
            continue

        confirmation = values.iloc[i + 1]
        state_close = float(row["close"])
        confirm_close = float(confirmation["close"])
        if not (state_close > 0.0 and confirm_close > 0.0):
            continue
        confirm_return = side * math.log(confirm_close / state_close) * 10_000.0
        if confirm_return <= 0.0:
            continue
        event: dict[str, object] = {
            "symbol": symbol,
            "family": family,
            "state_ts": pd.Timestamp(row["close_time_dt"]),
            "entry_ts": pd.Timestamp(confirmation["close_time_dt"]),
            "side": side,
            "entry_price": confirm_close,
            "pressure_capacity": pressure,
            "flow_5m": float(row["flow_5m"]),
            "aligned_result_bps": aligned,
            "opposing_capacity": float(row["opposing_capacity"]),
            "opposing_capacity_change_5m": capacity_change,
            "depth_age_seconds": float(row["depth_age_seconds"]),
        }
        for quantile in TAIL_QUANTILES:
            event[f"pc_q{int(quantile * 1000):03d}"] = float(row[f"pc_q{int(quantile * 1000):03d}"])
        for horizon in HORIZONS:
            future_close = float(values.iloc[i + 1 + horizon]["close"])
            gross = side * math.log(future_close / confirm_close) * 10_000.0
            event[f"gross_bps_{horizon}"] = gross
            event[f"net_bps_{horizon}"] = gross - ROUND_TRIP_COST_BPS
        records.append(event)

    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame
    core_open = pd.Timestamp(core_start, tz="UTC")
    core_close = pd.Timestamp(core_end + timedelta(days=1), tz="UTC")
    frame["entry_ts"] = pd.to_datetime(frame["entry_ts"], utc=True)
    frame["state_ts"] = pd.to_datetime(frame["state_ts"], utc=True)
    return frame[(frame["entry_ts"] >= core_open) & (frame["entry_ts"] < core_close)].reset_index(drop=True)


def _tail(frame: pd.DataFrame, quantile: float) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    threshold = frame[f"pc_q{int(quantile * 1000):03d}"]
    return frame[threshold.notna() & frame["pressure_capacity"].ge(threshold)].copy()


def global_arbitrate(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values(
        ["entry_ts", "pressure_capacity", "symbol"],
        ascending=[True, False, True],
        kind="stable",
    )
    return ordered.groupby("entry_ts", sort=True, as_index=False).first()


def nonoverlap(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    selected = []
    free_at: pd.Timestamp | None = None
    for index, row in frame.sort_values("entry_ts", kind="stable").iterrows():
        ts = pd.Timestamp(row["entry_ts"])
        if free_at is not None and ts < free_at:
            continue
        selected.append(index)
        free_at = ts + pd.Timedelta(minutes=horizon)
    return frame.loc[selected].sort_values("entry_ts", kind="stable").copy()


def _stats(frame: pd.DataFrame, horizon: int, calendar_days: int) -> dict[str, object]:
    if frame.empty:
        return {"trades": 0, "mean_gross_bps": 0.0, "mean_net_bps": 0.0, "direction_hit_rate": 0.0,
                "cost_clear_rate": 0.0, "gross_profit_factor": 0.0, "trades_per_calendar_day": 0.0}
    values = pd.to_numeric(frame[f"gross_bps_{horizon}"], errors="coerce").dropna().to_numpy(dtype=float)
    if values.size == 0:
        return {"trades": 0, "mean_gross_bps": 0.0, "mean_net_bps": 0.0, "direction_hit_rate": 0.0,
                "cost_clear_rate": 0.0, "gross_profit_factor": 0.0, "trades_per_calendar_day": 0.0}
    gains = values[values > 0.0].sum()
    losses = -values[values < 0.0].sum()
    pf = float(gains / losses) if losses > 0.0 else 999999.0
    return {
        "trades": int(values.size),
        "mean_gross_bps": float(values.mean()),
        "mean_net_bps": float(values.mean() - ROUND_TRIP_COST_BPS),
        "direction_hit_rate": float((values > 0.0).mean()),
        "cost_clear_rate": float((values > ROUND_TRIP_COST_BPS).mean()),
        "gross_profit_factor": pf,
        "trades_per_calendar_day": float(values.size / calendar_days),
    }


def summarize(events: pd.DataFrame, start: date, end: date) -> dict[str, object]:
    days = (end - start).days + 1
    result: dict[str, object] = {"calendar_days": days, "event_count": len(events), "cost_bps": ROUND_TRIP_COST_BPS,
                                "tail_quantiles": list(TAIL_QUANTILES), "families": {}}
    for family in ("PRESSURE_BREAK", "ABSORPTION_REVERSAL"):
        family_frame = events[events["family"].eq(family)].copy() if not events.empty else events.copy()
        global_frame = global_arbitrate(family_frame)
        branch: dict[str, object] = {"raw_events": len(family_frame), "global_events": len(global_frame), "tails": {}}
        for quantile in TAIL_QUANTILES:
            tail = _tail(global_frame, quantile)
            qbranch: dict[str, object] = {}
            for horizon in HORIZONS:
                direct = _stats(tail, horizon, days)
                independent = nonoverlap(tail, horizon)
                direct["nonoverlap"] = _stats(independent, horizon, days)
                qbranch[str(horizon)] = direct
            branch["tails"][f"q{quantile:.3f}"] = qbranch
        result["families"][family] = branch
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    load_start = start - timedelta(days=WARMUP_DAYS)
    load_end = end + timedelta(days=1)
    args.output.mkdir(parents=True, exist_ok=True)
    frames = []
    evidence: dict[str, list[dict[str, object]]] = {}
    for symbol in SYMBOLS:
        panel, records = load_symbol(symbol, load_start, load_end, args.cache)
        evidence[symbol] = records
        frames.append(build_events(symbol, panel, start, end))
    events = pd.concat(frames, ignore_index=True) if any(not frame.empty for frame in frames) else pd.DataFrame()
    if not events.empty:
        events = events.sort_values(["entry_ts", "symbol", "family"], kind="stable").reset_index(drop=True)
        events.to_csv(args.output / "events.csv.gz", index=False, compression="gzip")
    result = summarize(events, start, end)
    (args.output / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    (args.output / "raw_evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
