"""Add raw Binance aggTrades to the shared NautilusTrader catalog.

Bars remain the strategy observation clock. Trade ticks are supplied only to the
native Nautilus execution and emulation components so configured latency and
protective triggers advance on real transaction timestamps rather than the next
one-minute bar.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler

from features import _agg_reader
from features import _maker_mask
from features import sha256_file
from smc_ict_4.manifest import write_json_atomic


class TradeTickCatalogError(RuntimeError):
    """Raised when raw execution data cannot be made trustworthy."""


def _aggtrade_archives(raw_files: list[Path]) -> list[Path]:
    archives = sorted(
        {
            Path(path).resolve()
            for path in raw_files
            if Path(path).suffix.lower() == ".zip"
            and "aggTrades" in Path(path).name
        },
    )
    if not archives:
        raise TradeTickCatalogError("no Binance aggTrades archives in raw evidence")
    return archives


def _trade_frame(chunk: pd.DataFrame) -> pd.DataFrame:
    required = {
        "agg_trade_id",
        "price",
        "quantity",
        "transact_time",
        "is_buyer_maker",
    }
    if not required.issubset(chunk.columns):
        raise TradeTickCatalogError(
            f"aggTrades chunk missing columns: {sorted(required - set(chunk.columns))}",
        )
    trade_id = pd.to_numeric(chunk["agg_trade_id"], errors="raise").astype("int64")
    price = pd.to_numeric(chunk["price"], errors="raise").astype(float)
    quantity = pd.to_numeric(chunk["quantity"], errors="raise").astype(float)
    transact = pd.to_numeric(chunk["transact_time"], errors="raise")
    unit = "us" if float(transact.iloc[0]) > 10**14 else "ms"
    timestamp = pd.to_datetime(transact, unit=unit, utc=True)
    buyer_maker = _maker_mask(chunk["is_buyer_maker"]).astype(bool)

    frame = pd.DataFrame(
        {
            "price": price.to_numpy(),
            "quantity": quantity.to_numpy(),
            "side": pd.Series(
                pd.array(buyer_maker, dtype="boolean"),
            ).map({True: "SELL", False: "BUY"}).to_numpy(),
            "trade_id": trade_id.astype(str).to_numpy(),
            "_trade_id_numeric": trade_id.to_numpy(),
        },
        index=pd.DatetimeIndex(timestamp, name="timestamp"),
    )
    frame = frame[(frame["price"] > 0.0) & (frame["quantity"] > 0.0)]
    frame = (
        frame.reset_index()
        .sort_values(["timestamp", "_trade_id_numeric"], kind="stable")
        .drop_duplicates("trade_id", keep="last")
        .set_index("timestamp")
    )
    return frame.drop(columns=["_trade_id_numeric"])


def add_trade_ticks_to_catalog(
    *,
    instrument: CryptoPerpetual,
    catalog_path: Path,
    raw_files: list[Path],
    output: Path,
    build_start: date,
    build_end: date,
    execution_start: date,
    execution_end: date,
    chunk_size: int = 250_000,
) -> dict[str, Any]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not build_start <= execution_start <= execution_end <= build_end:
        raise TradeTickCatalogError("execution range must be contained in build range")
    archives = [
        path
        for path in _aggtrade_archives(raw_files)
        if execution_start <= date.fromisoformat(path.stem[-10:]) <= execution_end
    ]
    if not archives:
        raise TradeTickCatalogError("no aggTrades archives in execution range")
    catalog = ParquetDataCatalog(catalog_path)
    wrangler = TradeTickDataWrangler(instrument)
    total = 0
    first_ns: int | None = None
    last_ns: int | None = None
    first_trade_id: str | None = None
    last_trade_id: str | None = None

    for archive in archives:
        for raw_chunk in _agg_reader(archive, chunksize=chunk_size):
            frame = _trade_frame(raw_chunk)
            if frame.empty:
                continue
            ticks = wrangler.process(frame)
            if not ticks:
                continue
            # Daily files and conversion chunks can share a millisecond boundary.
            # Trade IDs remain unique; the explicit bypass permits these legitimate
            # timestamp overlaps while Parquet retains every native TradeTick.
            catalog.write_data(ticks, skip_disjoint_check=True)
            total += len(ticks)
            chunk_first = int(ticks[0].ts_event)
            chunk_last = int(ticks[-1].ts_event)
            first_ns = chunk_first if first_ns is None else min(first_ns, chunk_first)
            last_ns = chunk_last if last_ns is None else max(last_ns, chunk_last)
            if first_trade_id is None:
                first_trade_id = str(ticks[0].trade_id)
            last_trade_id = str(ticks[-1].trade_id)

    if total <= 0 or first_ns is None or last_ns is None:
        raise TradeTickCatalogError("TradeTickDataWrangler produced no trade ticks")
    if first_ns > last_ns:
        raise TradeTickCatalogError("trade tick timestamps are inverted")

    manifest: dict[str, Any] = {
        "schema": "candidate-18-trade-tick-catalog-v1",
        "instrument_id": str(instrument.id),
        "build_start": str(build_start),
        "build_end": str(build_end),
        "execution_start": str(execution_start),
        "execution_end": str(execution_end),
        "trade_ticks": total,
        "first_ts_event_ns": first_ns,
        "last_ts_event_ns": last_ns,
        "first_trade_id": first_trade_id,
        "last_trade_id": last_trade_id,
        "timestamp_semantics": "Binance aggTrades transact_time converted to UTC nanoseconds",
        "aggressor_semantics": "buyer_maker=true maps to SELLER; false maps to BUYER",
        "execution_role": "native NautilusTrader trade execution and LAST_PRICE order emulation",
        "archives": [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in archives
        ],
    }
    write_json_atomic(output.resolve() / "trade_tick_manifest.json", manifest)
    return manifest


__all__ = ["TradeTickCatalogError", "add_trade_ticks_to_catalog"]
