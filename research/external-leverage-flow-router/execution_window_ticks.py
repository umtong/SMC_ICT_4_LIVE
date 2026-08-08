"""Volume-preserving actual aggTrade execution windows for NautilusTrader.

Candidate 20 kept one actual aggregate trade per minute only to advance the
latency clock.  That sparse stream is not a liquidity-capacity model.  This
module reuses the same Binance parser, TradeTickDataWrangler, Parquet catalog,
and Nautilus matching engine, but retains every actual futures aggregate trade
from 1s through 16s after each minute boundary.  A bar-close order therefore
arrives after the inherited 250ms latency and can accumulate real opposite-side
trade volume for a bounded fifteen-second execution window.

No prices, quantities, matching, fills, positions, or PnL are synthesized.
Nanosecond ordinals only break ties among source rows sharing the same exchange
timestamp; source order is preserved by aggregate-trade ID and ordinals remain
inside the source timestamp resolution.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler

import backtest as base
from tick_backtest import TickRunnerError, _agg_reader, _maker_mask


MINUTE_NS = 60_000_000_000
WINDOW_START_NS = 1_000_000_000
WINDOW_END_NS = 16_000_000_000


def _source_factor(transact: pd.Series) -> int:
    """Return source timestamp-to-nanosecond multiplier."""
    first = int(transact.iloc[0])
    return 1_000 if first > 10**14 else 1_000_000


def _window_rows(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    selected: list[pd.DataFrame] = []
    source_rows = 0
    source_factor: int | None = None
    for chunk in _agg_reader(path):
        source_rows += len(chunk)
        transact = pd.to_numeric(
            chunk["transact_time"],
            errors="raise",
        ).astype("int64")
        factor = _source_factor(transact)
        if source_factor is None:
            source_factor = factor
        elif source_factor != factor:
            raise TickRunnerError(f"mixed timestamp resolutions in {path}")
        ts_ns = transact * factor
        phase = ts_ns % MINUTE_NS
        mask = (phase >= WINDOW_START_NS) & (phase < WINDOW_END_NS)
        if not bool(mask.any()):
            continue
        part = chunk.loc[mask]
        part_ts = ts_ns.loc[mask]
        work = pd.DataFrame(
            {
                "source_ts_ns": part_ts.to_numpy(dtype="int64"),
                "minute_ns": (
                    part_ts.to_numpy(dtype="int64") // MINUTE_NS
                ) * MINUTE_NS,
                "price": pd.to_numeric(
                    part["price"],
                    errors="raise",
                ).to_numpy(dtype=float),
                "quantity": pd.to_numeric(
                    part["quantity"],
                    errors="raise",
                ).to_numpy(dtype=float),
                "trade_id_int": pd.to_numeric(
                    part["agg_trade_id"],
                    errors="raise",
                ).to_numpy(dtype="int64"),
                "buyer_maker": _maker_mask(part["is_buyer_maker"]).to_numpy(),
            },
        )
        work = work[(work["price"] > 0.0) & (work["quantity"] > 0.0)]
        if not work.empty:
            selected.append(work)

    if not selected or source_factor is None:
        raise TickRunnerError(f"no actual trades in execution windows for {path}")
    day = pd.concat(selected, ignore_index=True)
    day = day.sort_values(
        ["source_ts_ns", "trade_id_int"],
        kind="mergesort",
    ).reset_index(drop=True)

    # Binance historical aggTrades use millisecond timestamps in this period.
    # Several ordered aggregate trades can share a timestamp.  Add only a
    # within-timestamp ordinal, bounded by the source timestamp resolution.
    ordinal = day.groupby("source_ts_ns", sort=False).cumcount().astype("int64")
    if int(ordinal.max()) >= source_factor:
        raise TickRunnerError(
            f"timestamp tie count exceeds source resolution in {path}",
        )
    day["ts_ns"] = day["source_ts_ns"] + ordinal
    if day["ts_ns"].duplicated().any() or not day["ts_ns"].is_monotonic_increasing:
        raise TickRunnerError(f"execution ticks are not uniquely ordered in {path}")

    minute = day.groupby("minute_ns", sort=False)
    minute_total = minute["quantity"].sum()
    # buyer_maker=False means buyer aggressor; this is evidence able to fill a
    # resting/crossing SELL.  buyer_maker=True analogously supports a BUY.
    buyer_aggressor = (
        day.loc[~day["buyer_maker"]]
        .groupby("minute_ns")["quantity"]
        .sum()
        .reindex(minute_total.index, fill_value=0.0)
    )
    seller_aggressor = (
        day.loc[day["buyer_maker"]]
        .groupby("minute_ns")["quantity"]
        .sum()
        .reindex(minute_total.index, fill_value=0.0)
    )
    evidence = {
        "archive": str(path),
        "source_rows": int(source_rows),
        "selected_rows": int(len(day)),
        "selected_minutes": int(len(minute_total)),
        "selected_quantity": float(day["quantity"].sum()),
        "minute_total_quantity_p05": float(minute_total.quantile(0.05)),
        "minute_total_quantity_median": float(minute_total.median()),
        "minute_buyer_aggressor_quantity_p05": float(buyer_aggressor.quantile(0.05)),
        "minute_buyer_aggressor_quantity_median": float(buyer_aggressor.median()),
        "minute_seller_aggressor_quantity_p05": float(seller_aggressor.quantile(0.05)),
        "minute_seller_aggressor_quantity_median": float(seller_aggressor.median()),
        "source_timestamp_resolution_ns": int(source_factor),
        "max_tie_break_ordinal_ns": int(ordinal.max()),
    }
    return day, evidence


def append_execution_window_ticks(
    *,
    raw_files: list[Path],
    catalog_path: Path,
    instrument: Any,
    output: Path,
) -> int:
    """Write bounded, volume-preserving execution windows to the catalog."""
    agg_paths = sorted(
        path
        for path in raw_files
        if path.suffix == ".zip" and "-aggTrades-" in path.name
    )
    if not agg_paths:
        raise TickRunnerError("no futures aggTrades archives were supplied")
    duplicate_names = [
        name for name, count in Counter(path.name for path in agg_paths).items()
        if count > 1
    ]
    if duplicate_names:
        raise TickRunnerError(
            "duplicate futures/spot archive names reached execution loader: "
            + ", ".join(duplicate_names),
        )

    catalog = ParquetDataCatalog(catalog_path)
    wrangler = TradeTickDataWrangler(instrument)
    day_evidence: list[dict[str, Any]] = []
    total_ticks = 0
    first_ts: int | None = None
    last_ts: int | None = None
    previous_last: int | None = None

    for path in agg_paths:
        day, evidence = _window_rows(path)
        frame = day[["price", "quantity", "buyer_maker"]].copy()
        frame["trade_id"] = day["trade_id_int"].astype(str)
        frame = frame[["price", "quantity", "trade_id", "buyer_maker"]]
        frame.index = pd.to_datetime(day["ts_ns"], unit="ns", utc=True)
        frame.index.name = "timestamp"
        ticks = wrangler.process(frame, ts_init_delta=0)
        if not ticks:
            raise TickRunnerError(f"TradeTickDataWrangler produced no ticks for {path}")
        current_first = int(ticks[0].ts_event)
        current_last = int(ticks[-1].ts_event)
        if previous_last is not None and current_first <= previous_last:
            raise TickRunnerError("execution-window archives overlap or are unordered")
        catalog.write_data(ticks)
        previous_last = current_last
        first_ts = current_first if first_ts is None else first_ts
        last_ts = current_last
        total_ticks += len(ticks)
        day_evidence.append(evidence)

    if total_ticks <= 0 or first_ts is None or last_ts is None:
        raise TickRunnerError("execution-window catalog contains no ticks")
    summary = {
        "schema": "external-actual-aggtrade-execution-windows-v1",
        "selection": (
            "every actual USD-M futures aggTrade from 1.000s inclusive through "
            "16.000s exclusive after each UTC minute boundary"
        ),
        "purpose": (
            "volume-preserving bounded execution capacity after bar-close signals; "
            "bars remain the alpha clock"
        ),
        "window_start_ns": WINDOW_START_NS,
        "window_end_ns": WINDOW_END_NS,
        "window_duration_seconds": (WINDOW_END_NS - WINDOW_START_NS) / 1e9,
        "source_rows": int(sum(item["source_rows"] for item in day_evidence)),
        "selected_rows": int(total_ticks),
        "selected_fraction": float(
            total_ticks / sum(item["source_rows"] for item in day_evidence)
        ),
        "selected_quantity": float(
            sum(item["selected_quantity"] for item in day_evidence)
        ),
        "first_ts_event": first_ts,
        "last_ts_event": last_ts,
        "strictly_increasing_across_archives": True,
        "source_price_and_quantity_unchanged": True,
        "timestamp_tie_break": (
            "aggregate-trade-ID order encoded as sub-resolution nanosecond ordinal"
        ),
        "days": day_evidence,
    }
    base.write_json_atomic(output / "execution_clock.json", summary)
    return total_ticks


__all__ = [
    "WINDOW_END_NS",
    "WINDOW_START_NS",
    "append_execution_window_ticks",
]
