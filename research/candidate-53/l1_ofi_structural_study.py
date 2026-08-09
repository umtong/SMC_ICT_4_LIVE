#!/usr/bin/env python3
"""Frozen structural true-L1 OFI continuation diagnostic.

The economic policy is frozen in ``L1_OFI_STRUCTURAL_FREEZE.md``.  This module
only turns that preregistered state transition into exact causal trade geometry:

    trailing q90 true L1 OFI shock
      -> completed participation bar accepts price in the OFI direction
      -> next-minute-open entry
      -> invalidation at the pressure leg's origin (bar start open)
      -> target solved for exactly +2R after 21 bp round-trip cost
      -> stop-before-target one-minute path ordering
      -> 240-minute time exit when unresolved.

No custom account, matching, leverage, position-sizing or NAV engine is created.
A passing mechanism must still be promoted to NautilusTrader for final proof.

Preserved Binance bookTicker files are not always physically sorted by observed
timestamp.  We therefore install the same bounded 120-second reorder repair
introduced after that implementation defect was discovered.  It changes only
file ordering, never timestamps, prices, quantities, or the frozen policy, and
raises if lateness exceeds the bound.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import date
import heapq
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import bookticker_source_v3 as source
import topbook_features

REORDER_NS = 120 * 1_000_000_000
COST_RATE = 0.0021
TAIL_Q_COLUMN = "abs_ofi_q900"
TARGET_R = 2.0
TIME_EXIT_MINUTES = 240


def iter_book_ticker_paths_reordered(paths):
    previous_emitted_ns = -1
    for path in sorted(paths, key=lambda item: item.name):
        archive, reader = source.one_csv_reader(path)
        heap = []
        sequence = 0
        max_seen_ns = -1
        try:
            for row in reader:
                if not row or not row[0] or not row[0][0].isdigit():
                    continue
                if len(row) < 7:
                    raise ValueError(f"bookTicker row too short in {path}")
                transaction_ns = source.normalize_timestamp_ns(int(row[5]))
                observed_ns = max(source.normalize_timestamp_ns(int(row[6])), transaction_ns)
                max_seen_ns = max(max_seen_ns, observed_ns)
                record = (
                    int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                    transaction_ns, observed_ns,
                )
                heapq.heappush(heap, (observed_ns, sequence, record))
                sequence += 1
                cutoff = max_seen_ns - REORDER_NS
                while heap and heap[0][0] <= cutoff:
                    ts, _, item = heapq.heappop(heap)
                    if ts < previous_emitted_ns:
                        raise ValueError(
                            f"bookTicker lateness exceeded {REORDER_NS / 1e9:.0f}s in {path}: "
                            f"{ts} < {previous_emitted_ns}",
                        )
                    previous_emitted_ns = ts
                    yield item
            while heap:
                ts, _, item = heapq.heappop(heap)
                if ts < previous_emitted_ns:
                    raise ValueError(f"bookTicker daily flush moved backwards in {path}")
                previous_emitted_ns = ts
                yield item
        finally:
            archive.close()


topbook_features.iter_book_ticker_paths = iter_book_ticker_paths_reordered

# Candidate03's frozen SourceFile uses slots.  The base L1 study stores source
# evidence through ``__dict__``; expose that representation without altering the
# verified file bytes or checksum contract.
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

# Import only after the data-contract repairs above.  The base module imports
# ``iter_book_ticker_paths`` by value, so ordering matters here.
import l1_ofi_participation_study as base  # noqa: E402


@dataclass(frozen=True, slots=True)
class StructuralTrade:
    symbol: str
    bar_start_ts: pd.Timestamp
    bar_end_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    side: int
    ofi_depth_ratio: float
    abs_ofi_q90: float
    shock_ratio: float
    price_acceptance_bps: float
    bar_minutes: int
    mean_spread_bps: float
    entry: float
    stop: float
    target: float
    gross_risk_rate: float
    planned_loss_rate: float
    target_net_r: float
    exit_ts: pd.Timestamp
    exit_price: float
    exit_reason: str
    gross_return: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def _lookup_row(minutes: pd.DataFrame, minute_ns: int):
    if minute_ns not in minutes.index:
        return None
    row = minutes.loc[minute_ns]
    if isinstance(row, pd.DataFrame):
        raise ValueError(f"duplicate minute {minute_ns}")
    return row


def _candidate_geometry(bar: dict[str, object], minutes: pd.DataFrame):
    q90 = float(bar[TAIL_Q_COLUMN]) if pd.notna(bar[TAIL_Q_COLUMN]) else math.nan
    abs_ofi = float(bar["abs_ofi"])
    ofi = float(bar["ofi_depth_ratio"])
    if not (math.isfinite(q90) and q90 > 0.0 and math.isfinite(abs_ofi) and abs_ofi >= q90):
        return None
    if not math.isfinite(ofi) or ofi == 0.0:
        return None
    side = 1 if ofi > 0.0 else -1

    start_ns = int(bar["start_ns"])
    end_ns = int(bar["end_ns"])
    start_row = _lookup_row(minutes, start_ns)
    end_row = _lookup_row(minutes, end_ns)
    entry_ns = end_ns + 60_000_000_000
    entry_row = _lookup_row(minutes, entry_ns)
    if start_row is None or end_row is None or entry_row is None:
        return None

    origin = float(start_row["open"])
    end_close = float(end_row["close"])
    entry = float(entry_row["open"])
    if not all(math.isfinite(v) and v > 0.0 for v in (origin, end_close, entry)):
        return None

    acceptance_bps = side * math.log(end_close / origin) * 10_000.0
    if acceptance_bps <= 0.0:
        return None

    # Full pressure-leg origin is structural invalidation.  Entry must have
    # progressed beyond it in the intended direction, otherwise geometry is
    # already invalid before the trade begins.
    gross_risk = side * (entry - origin) / entry
    if not math.isfinite(gross_risk) or gross_risk <= 0.0:
        return None
    planned_loss = gross_risk + COST_RATE
    target_move = TARGET_R * planned_loss + COST_RATE
    if not (math.isfinite(target_move) and 0.0 < target_move < 1.0):
        return None
    target = entry * (1.0 + side * target_move)
    if target <= 0.0:
        return None
    target_net_r = (target_move - COST_RATE) / planned_loss
    if abs(target_net_r - TARGET_R) > 1e-10:
        raise AssertionError("target algebra no longer yields frozen +2R")
    return {
        "side": side,
        "origin": origin,
        "entry_ns": entry_ns,
        "entry": entry,
        "target": target,
        "gross_risk": gross_risk,
        "planned_loss": planned_loss,
        "target_net_r": target_net_r,
        "acceptance_bps": acceptance_bps,
        "shock_ratio": abs_ofi / q90,
    }


def _score_trade(symbol: str, bar: dict[str, object], geometry: dict[str, float], minutes: pd.DataFrame):
    side = int(geometry["side"])
    entry_ns = int(geometry["entry_ns"])
    time_exit_ns = entry_ns + TIME_EXIT_MINUTES * 60_000_000_000
    entry = float(geometry["entry"])
    stop = float(geometry["origin"])
    target = float(geometry["target"])

    path = minutes[(minutes.index >= entry_ns) & (minutes.index < time_exit_ns)]
    if path.empty:
        return None
    exit_ts = pd.to_datetime(path.iloc[-1]["minute_ns"], unit="ns", utc=True)
    exit_price = float(path.iloc[-1]["close"])
    reason = "TIME_FALLBACK"
    mfe = 0.0
    mae = 0.0
    for row in path.itertuples():
        high = float(row.high)
        low = float(row.low)
        close = float(row.close)
        if side > 0:
            mfe = max(mfe, high / entry - 1.0)
            mae = min(mae, low / entry - 1.0)
            if low <= stop:
                exit_ts = pd.Timestamp(row.minute)
                exit_price = stop
                reason = "STOP"
                break
            if high >= target:
                exit_ts = pd.Timestamp(row.minute)
                exit_price = target
                reason = "TARGET"
                break
        else:
            mfe = max(mfe, entry / low - 1.0)
            mae = min(mae, entry / high - 1.0)
            if high >= stop:
                exit_ts = pd.Timestamp(row.minute)
                exit_price = stop
                reason = "STOP"
                break
            if low <= target:
                exit_ts = pd.Timestamp(row.minute)
                exit_price = target
                reason = "TARGET"
                break
        exit_ts = pd.Timestamp(row.minute)
        exit_price = close

    if reason.startswith("TIME"):
        time_row = _lookup_row(minutes, time_exit_ns)
        if time_row is not None:
            exit_ts = pd.to_datetime(time_exit_ns, unit="ns", utc=True)
            exit_price = float(time_row["open"])
            reason = "TIME"

    gross_return = side * (exit_price / entry - 1.0)
    net_return = gross_return - COST_RATE
    net_r = net_return / float(geometry["planned_loss"])
    return StructuralTrade(
        symbol=symbol,
        bar_start_ts=pd.to_datetime(int(bar["start_ns"]), unit="ns", utc=True),
        bar_end_ts=pd.to_datetime(int(bar["end_ns"]), unit="ns", utc=True),
        entry_ts=pd.to_datetime(entry_ns, unit="ns", utc=True),
        side=side,
        ofi_depth_ratio=float(bar["ofi_depth_ratio"]),
        abs_ofi_q90=float(bar[TAIL_Q_COLUMN]),
        shock_ratio=float(geometry["shock_ratio"]),
        price_acceptance_bps=float(geometry["acceptance_bps"]),
        bar_minutes=int(bar["bar_minutes"]),
        mean_spread_bps=float(bar["mean_spread_bps"]),
        entry=entry,
        stop=stop,
        target=target,
        gross_risk_rate=float(geometry["gross_risk"]),
        planned_loss_rate=float(geometry["planned_loss"]),
        target_net_r=float(geometry["target_net_r"]),
        exit_ts=exit_ts,
        exit_price=exit_price,
        exit_reason=reason,
        gross_return=gross_return,
        net_return=net_return,
        net_r=net_r,
        mfe=mfe,
        mae=mae,
    )


def structural_trades(symbol: str, bars: pd.DataFrame, minutes: pd.DataFrame) -> list[StructuralTrade]:
    result: list[StructuralTrade] = []
    free_at: pd.Timestamp | None = None
    for bar in bars.sort_values("end_ns", kind="stable").to_dict("records"):
        geometry = _candidate_geometry(bar, minutes)
        if geometry is None:
            continue
        entry_ts = pd.to_datetime(int(geometry["entry_ns"]), unit="ns", utc=True)
        if free_at is not None and entry_ts <= free_at:
            continue
        trade = _score_trade(symbol, bar, geometry, minutes)
        if trade is None:
            continue
        result.append(trade)
        free_at = trade.exit_ts
    return result


def summarize(trades: list[StructuralTrade], calendar_days: int) -> dict[str, object]:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "win_rate": 0.0,
            "mean_net_r": 0.0,
            "median_net_r": 0.0,
            "profit_factor_r": 0.0,
            "mean_net_return": 0.0,
            "target_rate": 0.0,
            "stop_rate": 0.0,
            "time_rate": 0.0,
            "trades_per_calendar_day": 0.0,
        }
    rs = np.asarray([trade.net_r for trade in trades], dtype=float)
    gains = float(rs[rs > 0.0].sum())
    losses = float(-rs[rs < 0.0].sum())
    return {
        "trades": len(trades),
        "wins": int((rs > 0.0).sum()),
        "win_rate": float((rs > 0.0).mean()),
        "mean_net_r": float(rs.mean()),
        "median_net_r": float(np.median(rs)),
        "profit_factor_r": float(gains / losses) if losses > 0.0 else 999999.0,
        "mean_net_return": float(np.mean([trade.net_return for trade in trades])),
        "target_rate": sum(trade.exit_reason == "TARGET" for trade in trades) / len(trades),
        "stop_rate": sum(trade.exit_reason == "STOP" for trade in trades) / len(trades),
        "time_rate": sum(trade.exit_reason.startswith("TIME") for trade in trades) / len(trades),
        "trades_per_calendar_day": len(trades) / calendar_days,
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
    kpaths, bpaths, evidence = base.download_symbol(args.symbol, start, end, args.cache)
    minutes = base.minute_frame(kpaths)
    ofi = base.aggregate_minute_ofi(bpaths)
    bars = base.participation_bars(minutes, ofi, start, end)
    trades = structural_trades(args.symbol, bars, minutes)
    result = {
        "symbol": args.symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_days": (end - start).days + 1,
        "frozen_policy": {
            "absolute_ofi_tail": 0.90,
            "direction": "OFI_CONTINUATION",
            "price_acceptance": "participation_bar_return_aligned_with_ofi_gt_0",
            "entry": "strict_next_minute_open",
            "stop": "participation_bar_start_minute_open",
            "target_net_r": TARGET_R,
            "round_trip_cost_rate": COST_RATE,
            "time_exit_minutes": TIME_EXIT_MINUTES,
            "same_minute_ordering": "STOP_BEFORE_TARGET",
        },
        "participation_bars": len(bars),
        "summary": summarize(trades, (end - start).days + 1),
    }
    pd.DataFrame([asdict(trade) for trade in trades]).to_csv(args.output / "trades.csv", index=False)
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
