#!/usr/bin/env python3
"""Frozen state-first true-L1 OFI selector diagnostic.

Economic rules are frozen in ``L1_OFI_STATE_FIRST_FREEZE.md``.  The study
reuses the preserved Binance BBO and kline source contracts, exactly reconstructs
physically disordered daily bookTicker files in exchange timestamp order, and
asks one narrow question: does a q90 OFI shock retain 240-minute continuation
edge when the completed liquidity state also shows price acceptance, a non-wide
spread, and depletion rather than replenishment of the opposing best queue?

This is not a matching/account/NAV engine.  It reports causal forward-return
mechanism evidence only.  Final trading proof remains NautilusTrader-only.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import bookticker_source_v3 as source
import topbook_features as topbook
from bookticker_exact_order import iter_book_ticker_paths_exact

# SourceFile in the reused source module is slots-based.  The older evidence
# writer expects ``__dict__``; expose a serialization proxy only.
_original_download = source.download_verified


class _SourceProxy:
    def __init__(self, record):
        self.kind = record.kind
        self.source_url = record.source_url
        self.local_path = record.local_path
        self.sha256 = record.sha256
        self.size_bytes = record.size_bytes
        self.__dict__ = {
            "kind": record.kind,
            "source_url": record.source_url,
            "local_path": record.local_path,
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
        }


def _download_verified(*args, **kwargs):
    return _SourceProxy(_original_download(*args, **kwargs))


source.download_verified = _download_verified

import l1_ofi_participation_study as base  # noqa: E402

ROUND_TRIP_COST_BPS = 21.0
TARGET_HORIZON_MINUTES = 240
TAIL_QUANTILE = 0.90
TRAILING_BARS = 90
MIN_TRAILING_BARS = 45


def _ofi_contribution(previous, current) -> float:
    _, bid0, bidq0, ask0, askq0, _, _ = previous
    _, bid1, bidq1, ask1, askq1, _, _ = current
    if bid1 > bid0:
        bid_term = bidq1
    elif bid1 == bid0:
        bid_term = bidq1 - bidq0
    else:
        bid_term = -bidq0
    if ask1 < ask0:
        ask_term = -askq1
    elif ask1 == ask0:
        ask_term = askq0 - askq1
    else:
        ask_term = askq0
    return float(bid_term + ask_term)


def aggregate_micro_minutes(paths: list[Path]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    accumulator = None
    minute_ns = None
    previous = None
    ofi_qty = 0.0
    depth_sum = 0.0
    spread_sum = 0.0
    updates = 0

    def flush() -> None:
        nonlocal accumulator, ofi_qty, depth_sum, spread_sum, updates
        if accumulator is None or updates <= 0:
            return
        item = accumulator.finalize()
        item.update(
            {
                "ofi_qty": float(ofi_qty),
                "mean_top_depth_qty": float(depth_sum / updates),
                "mean_spread_bps": float(spread_sum / updates),
            },
        )
        rows.append(item)
        accumulator = None
        ofi_qty = 0.0
        depth_sum = 0.0
        spread_sum = 0.0
        updates = 0

    for record in iter_book_ticker_paths_exact(paths):
        observed_ns = int(record[6])
        current_minute = observed_ns // topbook.NS_PER_MINUTE * topbook.NS_PER_MINUTE
        if previous is not None:
            contribution = _ofi_contribution(previous, record)
        else:
            contribution = 0.0
        if minute_ns is None or current_minute != minute_ns:
            flush()
            minute_ns = current_minute
            accumulator = topbook._MinuteAccumulator.from_record(record)
        else:
            accumulator.update(record)
        _, bid, bid_qty, ask, ask_qty, _, _ = record
        mid = (bid + ask) / 2.0
        spread_bps = (ask - bid) / mid * 10_000.0
        ofi_qty += contribution
        depth_sum += bid_qty + ask_qty
        spread_sum += spread_bps
        updates += 1
        previous = record
    flush()

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("no exact-order topbook minutes produced")
    if frame["minute_start_ns"].duplicated().any():
        raise ValueError("duplicate microstructure minute")
    frame["ofi_depth_ratio"] = frame["ofi_qty"] / frame["mean_top_depth_qty"].replace(0.0, np.nan)
    frame["start_mid"] = (
        pd.to_numeric(frame["topbook_bid_start"], errors="coerce")
        + pd.to_numeric(frame["topbook_ask_start"], errors="coerce")
    ) / 2.0
    frame["end_mid"] = (
        pd.to_numeric(frame["topbook_bid_end"], errors="coerce")
        + pd.to_numeric(frame["topbook_ask_end"], errors="coerce")
    ) / 2.0
    return frame.sort_values("minute_start_ns", kind="stable").set_index("minute_start_ns", drop=False)


def participation_state_bars(minutes: pd.DataFrame, micro: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    thresholds = base.daily_thresholds(minutes)
    columns = [
        "ofi_qty",
        "mean_top_depth_qty",
        "mean_spread_bps",
        "topbook_quote_updates",
        "start_mid",
        "end_mid",
        "topbook_bid_same_price_add_qty",
        "topbook_bid_same_price_remove_qty",
        "topbook_ask_same_price_add_qty",
        "topbook_ask_same_price_remove_qty",
        "topbook_bid_improve_count",
        "topbook_bid_retreat_count",
        "topbook_ask_improve_count",
        "topbook_ask_retreat_count",
    ]
    joined = minutes.join(micro[columns], how="left")
    records: list[dict[str, object]] = []
    acc = None

    def new_acc(row, day, threshold):
        return {
            "start_ns": int(row.minute_ns),
            "day": day,
            "threshold": float(threshold),
            "notional": 0.0,
            "ofi_qty": 0.0,
            "depth_num": 0.0,
            "spread_num": 0.0,
            "updates": 0.0,
            "start_mid": math.nan,
            "end_mid": math.nan,
            "minutes": 0,
            "bid_add": 0.0,
            "bid_remove": 0.0,
            "ask_add": 0.0,
            "ask_remove": 0.0,
            "bid_improve": 0,
            "bid_retreat": 0,
            "ask_improve": 0,
            "ask_retreat": 0,
        }

    for row in joined.itertuples():
        day = row.minute.date()
        threshold = thresholds.get(day)
        if threshold is None or threshold <= 0.0:
            continue
        if acc is None or day != acc["day"]:
            acc = new_acc(row, day, threshold)
        acc["notional"] += float(row.notional)
        acc["minutes"] += 1
        q = float(row.topbook_quote_updates) if pd.notna(row.topbook_quote_updates) else 0.0
        if q > 0.0:
            depth = float(row.mean_top_depth_qty)
            spread = float(row.mean_spread_bps)
            if math.isfinite(depth) and depth > 0.0 and math.isfinite(spread):
                if not math.isfinite(acc["start_mid"]):
                    acc["start_mid"] = float(row.start_mid)
                acc["end_mid"] = float(row.end_mid)
                acc["ofi_qty"] += float(row.ofi_qty)
                acc["depth_num"] += depth * q
                acc["spread_num"] += spread * q
                acc["updates"] += q
                acc["bid_add"] += float(row.topbook_bid_same_price_add_qty)
                acc["bid_remove"] += float(row.topbook_bid_same_price_remove_qty)
                acc["ask_add"] += float(row.topbook_ask_same_price_add_qty)
                acc["ask_remove"] += float(row.topbook_ask_same_price_remove_qty)
                acc["bid_improve"] += int(row.topbook_bid_improve_count)
                acc["bid_retreat"] += int(row.topbook_bid_retreat_count)
                acc["ask_improve"] += int(row.topbook_ask_improve_count)
                acc["ask_retreat"] += int(row.topbook_ask_retreat_count)
        if acc["notional"] >= acc["threshold"] and acc["updates"] > 0.0 and math.isfinite(acc["start_mid"]):
            mean_depth = acc["depth_num"] / acc["updates"]
            normalized = acc["ofi_qty"] / mean_depth if mean_depth > 0.0 else math.nan
            mid_ret = math.log(acc["end_mid"] / acc["start_mid"]) * 10_000.0
            records.append(
                {
                    "start_ns": acc["start_ns"],
                    "end_ns": int(row.minute_ns),
                    "bar_minutes": int(acc["minutes"]),
                    "trade_notional": float(acc["notional"]),
                    "ofi_depth_ratio": float(normalized),
                    "abs_ofi": float(abs(normalized)),
                    "mid_ret_bps": float(mid_ret),
                    "mean_spread_bps": float(acc["spread_num"] / acc["updates"]),
                    "bid_add": float(acc["bid_add"]),
                    "bid_remove": float(acc["bid_remove"]),
                    "ask_add": float(acc["ask_add"]),
                    "ask_remove": float(acc["ask_remove"]),
                    "bid_improve": int(acc["bid_improve"]),
                    "bid_retreat": int(acc["bid_retreat"]),
                    "ask_improve": int(acc["ask_improve"]),
                    "ask_retreat": int(acc["ask_retreat"]),
                },
            )
            acc = None

    bars = pd.DataFrame(records)
    if bars.empty:
        return bars
    bars["end_ts"] = pd.to_datetime(bars["end_ns"], unit="ns", utc=True)
    bars["start_ts"] = pd.to_datetime(bars["start_ns"], unit="ns", utc=True)
    bars["ofi_side"] = np.sign(bars["ofi_depth_ratio"]).astype(int)
    bars["abs_ofi_q90"] = bars["abs_ofi"].shift(1).rolling(
        TRAILING_BARS,
        min_periods=MIN_TRAILING_BARS,
    ).quantile(TAIL_QUANTILE)
    bars["spread_median"] = bars["mean_spread_bps"].shift(1).rolling(
        TRAILING_BARS,
        min_periods=MIN_TRAILING_BARS,
    ).median()
    bars["price_accepted"] = bars["ofi_side"] * bars["mid_ret_bps"] > 0.0
    bars["spread_not_wide"] = bars["spread_median"].notna() & bars["mean_spread_bps"].le(bars["spread_median"])
    long_depletion = bars["ask_remove"].gt(bars["ask_add"]) & bars["ask_retreat"].ge(bars["ask_improve"])
    short_depletion = bars["bid_remove"].gt(bars["bid_add"]) & bars["bid_retreat"].ge(bars["bid_improve"])
    bars["opposing_queue_depletion"] = np.where(
        bars["ofi_side"] > 0,
        long_depletion,
        np.where(bars["ofi_side"] < 0, short_depletion, False),
    )
    bars["q90_shock"] = bars["abs_ofi_q90"].notna() & bars["abs_ofi"].ge(bars["abs_ofi_q90"])
    bars["state_pass"] = (
        bars["q90_shock"]
        & bars["price_accepted"]
        & bars["spread_not_wide"]
        & bars["opposing_queue_depletion"]
    )
    core_open = pd.Timestamp(start, tz="UTC")
    core_close = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    return bars[(bars["end_ts"] >= core_open) & (bars["end_ts"] < core_close)].reset_index(drop=True)


def add_outcomes(bars: pd.DataFrame, minutes: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return bars
    lookup = minutes.set_index("minute_ns")
    records = []
    for item in bars.to_dict("records"):
        if not bool(item["state_pass"]):
            continue
        side = int(item["ofi_side"])
        entry_ns = int(item["end_ns"] + 60_000_000_000)
        future_ns = entry_ns + TARGET_HORIZON_MINUTES * 60_000_000_000
        if entry_ns not in lookup.index or future_ns not in lookup.index:
            continue
        entry = float(lookup.loc[entry_ns, "open"])
        future = float(lookup.loc[future_ns, "open"])
        gross = side * math.log(future / entry) * 10_000.0
        item.update(
            {
                "entry_ns": entry_ns,
                "entry_ts": pd.to_datetime(entry_ns, unit="ns", utc=True),
                "entry_price": entry,
                "future_price": future,
                "gross_bps_240": float(gross),
                "net_bps_240": float(gross - ROUND_TRIP_COST_BPS),
            },
        )
        records.append(item)
    return pd.DataFrame(records)


def nonoverlap(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    chosen = []
    free_at = None
    for idx, row in events.sort_values("entry_ts", kind="stable").iterrows():
        ts = pd.Timestamp(row["entry_ts"])
        if free_at is not None and ts < free_at:
            continue
        chosen.append(idx)
        free_at = ts + pd.Timedelta(minutes=TARGET_HORIZON_MINUTES)
    return events.loc[chosen].sort_values("entry_ts", kind="stable").copy()


def stats(events: pd.DataFrame, days_count: int) -> dict[str, object]:
    if events.empty:
        return {
            "trades": 0,
            "mean_gross_bps": 0.0,
            "mean_net_bps": 0.0,
            "hit_rate": 0.0,
            "cost_clear_rate": 0.0,
            "gross_profit_factor": 0.0,
            "trades_per_calendar_day": 0.0,
        }
    values = pd.to_numeric(events["gross_bps_240"], errors="coerce").dropna().to_numpy(dtype=float)
    if values.size == 0:
        return stats(pd.DataFrame(), days_count)
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    return {
        "trades": int(values.size),
        "mean_gross_bps": float(values.mean()),
        "mean_net_bps": float(values.mean() - ROUND_TRIP_COST_BPS),
        "hit_rate": float((values > 0.0).mean()),
        "cost_clear_rate": float((values > ROUND_TRIP_COST_BPS).mean()),
        "gross_profit_factor": float(gains / losses) if losses > 0.0 else 999999.0,
        "trades_per_calendar_day": float(values.size / days_count),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    args.output.mkdir(parents=True, exist_ok=True)

    kline_paths, book_paths, evidence = base.download_symbol(args.symbol, start, end, args.cache)
    minutes = base.minute_frame(kline_paths)
    micro = aggregate_micro_minutes(book_paths)
    bars = participation_state_bars(minutes, micro, start, end)
    selected = add_outcomes(bars, minutes)
    independent = nonoverlap(selected)
    days_count = (end - start).days + 1
    result = {
        "symbol": args.symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_days": days_count,
        "frozen_selector": {
            "abs_ofi_tail": TAIL_QUANTILE,
            "price_acceptance": "OFI_ALIGNED_MID_RETURN_GT_0",
            "spread_state": "MEAN_SPREAD_LE_TRAILING_90_BAR_MEDIAN",
            "opposing_queue": "SAME_PRICE_REMOVAL_GT_ADD_AND_RETREAT_GE_IMPROVE",
            "direction": "OFI_CONTINUATION",
            "horizon_minutes": TARGET_HORIZON_MINUTES,
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        },
        "participation_bars": len(bars),
        "q90_shocks": int(bars["q90_shock"].sum()) if not bars.empty else 0,
        "price_accepted_q90": int((bars["q90_shock"] & bars["price_accepted"]).sum()) if not bars.empty else 0,
        "state_pass_events": len(selected),
        "all_state_pass": stats(selected, days_count),
        "nonoverlap": stats(independent, days_count),
    }
    bars.to_csv(args.output / "state_bars.csv.gz", index=False, compression="gzip")
    selected.to_csv(args.output / "selected_events.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (args.output / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
