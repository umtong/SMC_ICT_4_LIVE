"""Checksum-verified USD-M aggregate trades reduced to causal one-second bars.

The raw Binance Vision ``aggTrades`` archives are streamed once and reduced to
one row per UTC second.  Missing seconds are never synthesized.  A downstream
auction window must therefore prove exact one-second continuity before it is
eligible.  Buyer-maker means the aggressive order was a sell; all quote-flow
fields are derived from raw price * quantity, not from future candles.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import io
from itertools import chain
from pathlib import Path
from typing import Iterable
import zipfile

import pandas as pd

from data import _days, _ensure_checked_archive, _timestamp_ns
from data_positioning import PositioningBundle, load_positioning_bundle
from smc_ict_4.manifest import build_data_manifest, write_data_manifest


AGG_TRADES_DAILY_URL = (
    "https://data.binance.vision/data/futures/um/daily/aggTrades/{symbol}/"
    "{symbol}-aggTrades-{day}.zip"
)

_HEADER_ALIASES = {
    "aggregate_trade_id": "agg_trade_id",
    "agg_trade_id": "agg_trade_id",
    "aggtradeid": "agg_trade_id",
    "price": "price",
    "quantity": "quantity",
    "qty": "quantity",
    "first_trade_id": "first_trade_id",
    "firsttradeid": "first_trade_id",
    "last_trade_id": "last_trade_id",
    "lasttradeid": "last_trade_id",
    "timestamp": "transact_time",
    "transact_time": "transact_time",
    "time": "transact_time",
    "was_the_buyer_the_maker": "is_buyer_maker",
    "is_buyer_maker": "is_buyer_maker",
    "isbuyermaker": "is_buyer_maker",
}
_FIXED_COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)


@dataclass(frozen=True, slots=True)
class AggTrade1sBundle:
    seconds: pd.DataFrame
    minute_positioning: PositioningBundle
    archives: tuple[Path, ...]
    data_manifest_path: Path
    diagnostics: dict[str, int]


def _normalize_header(value: str) -> str:
    return "_".join(value.strip().lower().replace("?", "").split())


def _truth(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "t", "yes"}:
        return True
    if normalized in {"false", "0", "f", "no"}:
        return False
    raise ValueError(f"cannot parse boolean: {value!r}")


def _column_indices(first_row: list[str]) -> tuple[dict[str, int], bool]:
    normalized = [_normalize_header(value) for value in first_row]
    mapped = {
        _HEADER_ALIASES[name]: index
        for index, name in enumerate(normalized)
        if name in _HEADER_ALIASES
    }
    required = {"price", "quantity", "transact_time", "is_buyer_maker"}
    if required.issubset(mapped):
        return mapped, True
    if len(first_row) < len(_FIXED_COLUMNS):
        raise RuntimeError(f"short aggTrades row: {first_row}")
    return {name: index for index, name in enumerate(_FIXED_COLUMNS)}, False


def _read_archive_to_seconds(
    path: Path,
    *,
    load_start_ns: int,
    trade_end_ns: int,
) -> tuple[list[dict[str, float | int]], dict[str, int]]:
    buckets: dict[int, dict[str, float | int]] = {}
    diagnostics = {
        "raw_rows": 0,
        "filtered_rows": 0,
        "out_of_order_rows": 0,
        "duplicate_agg_trade_ids": 0,
    }
    previous_ts: int | None = None
    previous_agg_id: int | None = None

    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"expected one aggTrades CSV in {path}, found {names}")
        with archive.open(names[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
            try:
                first = next(reader)
            except StopIteration:
                return [], diagnostics
            indices, header = _column_indices(first)
            rows: Iterable[list[str]] = reader if header else chain((first,), reader)
            for row in rows:
                if not row:
                    continue
                diagnostics["raw_rows"] += 1
                try:
                    ts_ns = _timestamp_ns(row[indices["transact_time"]])
                    price = float(Decimal(row[indices["price"]]))
                    quantity = float(Decimal(row[indices["quantity"]]))
                    buyer_maker = _truth(row[indices["is_buyer_maker"]])
                    agg_id = (
                        int(Decimal(row[indices["agg_trade_id"]]))
                        if "agg_trade_id" in indices
                        else diagnostics["raw_rows"]
                    )
                except Exception as exc:
                    raise RuntimeError(f"cannot parse aggTrades row in {path}: {row[:7]}") from exc
                if price <= 0.0 or quantity <= 0.0:
                    raise RuntimeError(f"nonpositive aggTrade in {path}: {row[:7]}")
                if previous_ts is not None and ts_ns < previous_ts:
                    diagnostics["out_of_order_rows"] += 1
                if previous_agg_id is not None and agg_id == previous_agg_id:
                    diagnostics["duplicate_agg_trade_ids"] += 1
                previous_ts = ts_ns
                previous_agg_id = agg_id
                if not load_start_ns <= ts_ns < trade_end_ns:
                    diagnostics["filtered_rows"] += 1
                    continue

                second_ns = (ts_ns // 1_000_000_000) * 1_000_000_000
                quote = price * quantity
                item = buckets.get(second_ns)
                if item is None:
                    item = {
                        "timestamp_ns": second_ns + 999_999_999,
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "volume": quantity,
                        "quote_volume": quote,
                        "taker_buy_quote": 0.0 if buyer_maker else quote,
                        "taker_sell_quote": quote if buyer_maker else 0.0,
                        "trade_count": 1,
                        "first_trade_ns": ts_ns,
                        "last_trade_ns": ts_ns,
                    }
                    buckets[second_ns] = item
                else:
                    item["high"] = max(float(item["high"]), price)
                    item["low"] = min(float(item["low"]), price)
                    item["close"] = price
                    item["volume"] = float(item["volume"]) + quantity
                    item["quote_volume"] = float(item["quote_volume"]) + quote
                    if buyer_maker:
                        item["taker_sell_quote"] = float(item["taker_sell_quote"]) + quote
                    else:
                        item["taker_buy_quote"] = float(item["taker_buy_quote"]) + quote
                    item["trade_count"] = int(item["trade_count"]) + 1
                    item["last_trade_ns"] = ts_ns

    records = [buckets[key] for key in sorted(buckets)]
    return records, diagnostics


def load_aggtrade_1s_bundle(
    *,
    symbol: str,
    trade_start: date,
    trade_end: date,
    positioning_warmup_days: int,
    event_warmup_days: int,
    cache_root: Path,
    manifest_destination: Path,
) -> AggTrade1sBundle:
    if trade_end <= trade_start:
        raise ValueError("trade_end must follow trade_start")
    if positioning_warmup_days < 1 or event_warmup_days < 1:
        raise ValueError("warmup days must be positive")
    symbol = symbol.upper()
    base_manifest = manifest_destination.with_name(
        f"{manifest_destination.stem}-minute-positioning.json"
    )
    base = load_positioning_bundle(
        symbol=symbol,
        trade_start=trade_start,
        trade_end=trade_end,
        warmup_days=positioning_warmup_days,
        cache_root=cache_root,
        manifest_destination=base_manifest,
    )

    load_start = trade_start - timedelta(days=event_warmup_days)
    load_start_ns = int(
        datetime.combine(load_start, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        * 1e9
    )
    trade_end_ns = int(
        datetime.combine(trade_end, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        * 1e9
    )
    archives: list[Path] = []
    records: list[dict[str, float | int]] = []
    aggregate_diagnostics = {
        "raw_rows": 0,
        "filtered_rows": 0,
        "out_of_order_rows": 0,
        "duplicate_agg_trade_ids": 0,
    }
    root = cache_root.resolve() / symbol / "aggTrades"
    for day in _days(load_start, trade_end):
        stamp = day.isoformat()
        url = AGG_TRADES_DAILY_URL.format(symbol=symbol, day=stamp)
        destination = root / f"{symbol}-aggTrades-{stamp}.zip"
        archive = _ensure_checked_archive(url, destination)
        archives.append(archive)
        daily_records, daily_diagnostics = _read_archive_to_seconds(
            archive,
            load_start_ns=load_start_ns,
            trade_end_ns=trade_end_ns,
        )
        records.extend(daily_records)
        for name, value in daily_diagnostics.items():
            aggregate_diagnostics[name] += int(value)

    if not records:
        raise RuntimeError("no aggregate trades loaded")
    seconds = pd.DataFrame.from_records(records)
    seconds = seconds.sort_values("timestamp_ns", kind="stable")
    seconds = seconds.drop_duplicates(subset=["timestamp_ns"], keep="last")
    numeric = [
        "open", "high", "low", "close", "volume", "quote_volume",
        "taker_buy_quote", "taker_sell_quote",
    ]
    for name in numeric:
        seconds[name] = pd.to_numeric(seconds[name], errors="raise")
    seconds["timestamp_ns"] = seconds["timestamp_ns"].astype("int64")
    seconds["first_trade_ns"] = seconds["first_trade_ns"].astype("int64")
    seconds["last_trade_ns"] = seconds["last_trade_ns"].astype("int64")
    seconds["trade_count"] = seconds["trade_count"].astype("int64")
    if not seconds["timestamp_ns"].is_monotonic_increasing:
        raise RuntimeError("aggregate-trade seconds are not monotonic")
    if bool((seconds[numeric] < 0.0).any().any()):
        raise RuntimeError("negative aggregate-trade second values")
    if bool((seconds[["open", "high", "low", "close"]] <= 0.0).any().any()):
        raise RuntimeError("nonpositive aggregate-trade second prices")
    if bool((seconds["high"] < seconds[["open", "close"]].max(axis=1)).any()):
        raise RuntimeError("aggregate-trade high is inconsistent")
    if bool((seconds["low"] > seconds[["open", "close"]].min(axis=1)).any()):
        raise RuntimeError("aggregate-trade low is inconsistent")
    flow_error = (
        seconds["quote_volume"]
        - seconds["taker_buy_quote"]
        - seconds["taker_sell_quote"]
    ).abs()
    if bool((flow_error > seconds["quote_volume"].clip(lower=1.0) * 1e-9).any()):
        raise RuntimeError("aggressor quote flow does not reconcile")

    gaps = seconds["timestamp_ns"].diff().dropna()
    aggregate_diagnostics["second_rows"] = int(len(seconds.index))
    aggregate_diagnostics["noncontiguous_second_transitions"] = int(
        (gaps != 1_000_000_000).sum()
    )
    aggregate_diagnostics["missing_seconds_from_span"] = int(
        ((int(seconds.iloc[-1]["timestamp_ns"]) - int(seconds.iloc[0]["timestamp_ns"]))
        // 1_000_000_000 + 1) - len(seconds.index)
    )
    seconds.index = pd.to_datetime(seconds["timestamp_ns"], unit="ns", utc=True)
    seconds.index.name = "timestamp"

    all_archives = tuple([*base.archives, *archives])
    manifest = build_data_manifest(
        cache_root.resolve() / symbol,
        dataset="binance-usdm-public-aggtrades-one-second-and-positioning",
        include=all_archives,
        metadata_values={
            "symbol": symbol,
            "event_load_start": load_start.isoformat(),
            "trade_start": trade_start.isoformat(),
            "trade_end_exclusive": trade_end.isoformat(),
            "aggregate_trade_archives": len(archives),
            "event_diagnostics": aggregate_diagnostics,
            "positioning_rows": int(len(base.metrics.index)),
            "source": "Binance Vision public USD-M aggTrades and metrics",
            "checksum": "published SHA-256 CHECKSUM verified per archive",
            "missing_second_policy": "not synthesized; event continuity required",
        },
    )
    write_data_manifest(manifest_destination, manifest)
    return AggTrade1sBundle(
        seconds=seconds,
        minute_positioning=base,
        archives=all_archives,
        data_manifest_path=manifest_destination,
        diagnostics=aggregate_diagnostics,
    )


__all__ = ["AGG_TRADES_DAILY_URL", "AggTrade1sBundle", "load_aggtrade_1s_bundle"]
