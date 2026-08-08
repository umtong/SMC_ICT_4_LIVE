"""Candidate 21 execution clock aligned to the configured order latency.

Candidate 20 intentionally retained one actual aggregate trade per minute, but
selected the first trade at least one second after the minute boundary. Candidate
21 submits a marketable FOK limit from the completed minute bar. Its configured
base plus insertion latency is 250 ms, so a fixed one-second execution event adds
about 750 ms of artificial delay and can turn an immediately executable order
into a false no-fill.

This adapter keeps the same sparse actual-trade design while selecting the first
real Binance aggregate trade at or after 300 ms into each minute: 250 ms modeled
latency plus a 50 ms safety margin. No price is fabricated and no event is exposed
to the strategy as alpha; the ticks are execution-clock events only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler

from smc_ict_4.manifest import write_json_atomic
from tick_backtest import TickRunnerError
from tick_backtest import _agg_reader
from tick_backtest import _maker_mask


MINUTE_NS = 60_000_000_000
MODELED_ORDER_LATENCY_NS = 250_000_000
EXECUTION_OFFSET_NS = 300_000_000
SAFETY_MARGIN_NS = EXECUTION_OFFSET_NS - MODELED_ORDER_LATENCY_NS


def _select_latency_aligned_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """Select the earliest real trade after the executable offset per minute."""
    required = {
        "ts_ns",
        "minute_ns",
        "price",
        "quantity",
        "trade_id",
        "buyer_maker",
    }
    missing = required.difference(rows.columns)
    if missing:
        raise TickRunnerError(f"latency clock rows missing columns: {sorted(missing)}")
    work = rows.copy()
    work["offset_ns"] = work["ts_ns"] - work["minute_ns"]
    work["latency_ready"] = work["offset_ns"] >= EXECUTION_OFFSET_NS
    work = work[(work["price"] > 0.0) & (work["quantity"] > 0.0)]
    if work.empty:
        raise TickRunnerError("latency clock received no positive trades")

    fallback = (
        work.sort_values("ts_ns")
        .drop_duplicates("minute_ns", keep="first")
    )
    eligible = (
        work[work["latency_ready"]]
        .sort_values("ts_ns")
        .drop_duplicates("minute_ns", keep="first")
    )
    selected = pd.concat([fallback, eligible], ignore_index=True)
    selected = (
        selected.sort_values(
            ["minute_ns", "latency_ready", "ts_ns"],
            ascending=[True, False, True],
        )
        .drop_duplicates("minute_ns", keep="first")
        .sort_values("ts_ns")
        .reset_index(drop=True)
    )
    if selected["ts_ns"].duplicated().any():
        raise TickRunnerError("duplicate latency-aligned execution timestamps")
    if not selected["ts_ns"].is_monotonic_increasing:
        raise TickRunnerError("latency-aligned execution ticks are not ordered")
    return selected


def _sparse_latency_aligned_trades(paths: list[Path]) -> pd.DataFrame:
    candidates: list[pd.DataFrame] = []
    agg_paths = sorted(
        path
        for path in paths
        if path.suffix == ".zip" and "-aggTrades-" in path.name
    )
    if not agg_paths:
        raise TickRunnerError("no aggTrades archives were supplied by load_range")

    for path in agg_paths:
        chunks: list[pd.DataFrame] = []
        for chunk in _agg_reader(path):
            transact = pd.to_numeric(
                chunk["transact_time"],
                errors="raise",
            ).astype("int64")
            factor = 1_000 if int(transact.iloc[0]) > 10**14 else 1_000_000
            ts_ns = transact * factor
            work = pd.DataFrame(
                {
                    "ts_ns": ts_ns,
                    "minute_ns": (ts_ns // MINUTE_NS) * MINUTE_NS,
                    "price": pd.to_numeric(
                        chunk["price"],
                        errors="raise",
                    ).astype(float),
                    "quantity": pd.to_numeric(
                        chunk["quantity"],
                        errors="raise",
                    ).astype(float),
                    "trade_id": pd.to_numeric(
                        chunk["agg_trade_id"],
                        errors="raise",
                    ).astype("int64").astype(str),
                    "buyer_maker": _maker_mask(
                        chunk["is_buyer_maker"],
                    ).to_numpy(),
                },
            )
            chunks.append(work)
        if not chunks:
            raise TickRunnerError(f"empty aggregate-trade archive {path}")
        candidates.append(pd.concat(chunks, ignore_index=True))

    selected = _select_latency_aligned_rows(
        pd.concat(candidates, ignore_index=True),
    )
    frame = selected[
        ["price", "quantity", "trade_id", "buyer_maker"]
    ].copy()
    frame.index = pd.to_datetime(selected["ts_ns"], unit="ns", utc=True)
    frame.index.name = "timestamp"
    frame.attrs["latency_ready_ticks"] = int(selected["latency_ready"].sum())
    frame.attrs["fallback_ticks"] = int((~selected["latency_ready"]).sum())
    frame.attrs["minimum_selected_offset_ns"] = (
        int(selected.loc[selected["latency_ready"], "offset_ns"].min())
        if selected["latency_ready"].any()
        else None
    )
    return frame


def append_latency_aligned_execution_ticks(
    *,
    raw_files: list[Path],
    catalog_path: Path,
    instrument: Any,
    output: Path,
) -> int:
    """Write one real, latency-eligible aggregate trade per minute."""
    frame = _sparse_latency_aligned_trades(raw_files)
    ticks = TradeTickDataWrangler(instrument).process(frame, ts_init_delta=0)
    if not ticks:
        raise TickRunnerError("TradeTickDataWrangler produced no latency ticks")
    ParquetDataCatalog(catalog_path).write_data(ticks)
    evidence = {
        "schema": "candidate-21-latency-aligned-aggtrade-clock-v1",
        "selection": (
            "first actual aggTrade at or after 300 ms into each minute; "
            "fallback first actual trade only when no eligible trade exists"
        ),
        "modeled_order_latency_ns": MODELED_ORDER_LATENCY_NS,
        "selection_offset_ns": EXECUTION_OFFSET_NS,
        "safety_margin_ns": SAFETY_MARGIN_NS,
        "source_rows": len(frame),
        "latency_ready_ticks": int(frame.attrs["latency_ready_ticks"]),
        "fallback_ticks": int(frame.attrs["fallback_ticks"]),
        "minimum_selected_offset_ns": frame.attrs[
            "minimum_selected_offset_ns"
        ],
        "first_ts_event": int(ticks[0].ts_event),
        "last_ts_event": int(ticks[-1].ts_event),
        "strictly_increasing": all(
            int(left.ts_event) < int(right.ts_event)
            for left, right in zip(ticks, ticks[1:])
        ),
        "actual_prices_only": True,
        "strategy_alpha_visibility": False,
    }
    write_json_atomic(output / "execution_clock.json", evidence)
    return len(ticks)


__all__ = [
    "EXECUTION_OFFSET_NS",
    "MODELED_ORDER_LATENCY_NS",
    "SAFETY_MARGIN_NS",
    "_select_latency_aligned_rows",
    "append_latency_aligned_execution_ticks",
]
