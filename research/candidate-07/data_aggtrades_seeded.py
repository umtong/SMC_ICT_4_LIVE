"""Causal aggTrades clock seconds seeded by the last real pre-window trade."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from data import _days, _ensure_checked_archive
from data_aggtrades_1s import (
    AGG_TRADES_DAILY_URL,
    NS_PER_SECOND,
    SECOND_END_OFFSET_NS,
    AggTrade1sBundle,
    _read_archive_to_seconds,
)
from data_positioning import load_positioning_bundle
from smc_ict_4.manifest import build_data_manifest, write_data_manifest


def complete_no_trade_seconds_with_seed(
    seconds: pd.DataFrame,
    *,
    load_start_ns: int,
    trade_end_ns: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fill zero-flow seconds from the last actual trade before the window.

    The seed is never a future observation. It must be a real aggregate trade
    whose completed second precedes the requested clock span. Only its close is
    carried forward; volume, aggressor flow and trade count remain zero until a
    new trade occurs.
    """
    if trade_end_ns <= load_start_ns:
        raise ValueError("trade_end_ns must follow load_start_ns")
    if seconds.empty:
        raise ValueError("seconds must not be empty")
    work = seconds.copy()
    work["timestamp_ns"] = work["timestamp_ns"].astype("int64")
    work = (
        work.sort_values("timestamp_ns", kind="stable")
        .drop_duplicates(subset=["timestamp_ns"], keep="last")
        .reset_index(drop=True)
    )

    first_second_end = (
        (load_start_ns // NS_PER_SECOND) * NS_PER_SECOND
        + SECOND_END_OFFSET_NS
    )
    last_second_end = (
        ((trade_end_ns - 1) // NS_PER_SECOND) * NS_PER_SECOND
        + SECOND_END_OFFSET_NS
    )
    seed_rows = work[work["timestamp_ns"] < first_second_end]
    if seed_rows.empty:
        raise RuntimeError(
            "no actual pre-window aggregate trade is available for causal seeding"
        )
    seed_position = int(seed_rows.index[-1])
    seed_timestamp_ns = int(work.loc[seed_position, "timestamp_ns"])
    seed_close = float(work.loc[seed_position, "close"])
    if seed_close <= 0.0 or seed_timestamp_ns >= first_second_end:
        raise RuntimeError("invalid causal aggregate-trade seed")

    expected = pd.RangeIndex(
        start=first_second_end,
        stop=last_second_end + NS_PER_SECOND,
        step=NS_PER_SECOND,
        name="timestamp_ns",
    )
    window = work[
        (work["timestamp_ns"] >= first_second_end)
        & (work["timestamp_ns"] <= last_second_end)
    ].copy()
    raw_second_rows = int(len(window.index))
    window = window.set_index("timestamp_ns", drop=True).reindex(expected)
    had_trade = window["trade_count"].notna()
    carried_close = window["close"].ffill().fillna(seed_close)
    if bool(carried_close.isna().any()):
        raise RuntimeError("causal seed did not produce a complete price clock")

    no_trade = ~had_trade
    for name in ("open", "high", "low", "close"):
        window.loc[no_trade, name] = carried_close.loc[no_trade]
    for name in (
        "volume",
        "quote_volume",
        "taker_buy_quote",
        "taker_sell_quote",
    ):
        window[name] = window[name].fillna(0.0)
    window["trade_count"] = window["trade_count"].fillna(0).astype("int64")
    window["first_trade_ns"] = window["first_trade_ns"].fillna(-1).astype("int64")
    window["last_trade_ns"] = window["last_trade_ns"].fillna(-1).astype("int64")
    window["had_trade"] = had_trade.astype(bool)
    window = window.reset_index()

    first_observed = next(
        (index for index, value in enumerate(had_trade.tolist()) if bool(value)),
        len(had_trade.index),
    )
    diagnostics: dict[str, Any] = {
        "raw_second_rows": raw_second_rows,
        "expected_clock_seconds": int(len(expected)),
        "observed_trade_seconds": int(had_trade.sum()),
        "causal_zero_flow_seconds": int(no_trade.sum()),
        "causal_seed_used": True,
        "causal_seed_timestamp_ns": seed_timestamp_ns,
        "causal_seed_close": seed_close,
        "leading_seeded_zero_flow_seconds": int(first_observed),
    }
    return window, diagnostics


def load_aggtrade_1s_bundle_seeded(
    *,
    symbol: str,
    trade_start: date,
    trade_end: date,
    positioning_warmup_days: int,
    event_warmup_days: int,
    cache_root: Path,
    manifest_destination: Path,
) -> AggTrade1sBundle:
    """Load the standard bundle plus one prior archive for a causal price seed."""
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

    event_load_start = trade_start - timedelta(days=event_warmup_days)
    archive_start = event_load_start - timedelta(days=1)
    archive_start_ns = int(
        datetime.combine(
            archive_start,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9
    )
    load_start_ns = int(
        datetime.combine(
            event_load_start,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9
    )
    trade_end_ns = int(
        datetime.combine(
            trade_end,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9
    )

    archives: list[Path] = []
    records: list[dict[str, float | int]] = []
    aggregate_diagnostics: dict[str, Any] = {
        "raw_rows": 0,
        "filtered_rows": 0,
        "out_of_order_rows": 0,
        "duplicate_agg_trade_ids": 0,
    }
    root = cache_root.resolve() / symbol / "aggTrades"
    for day in _days(archive_start, trade_end):
        stamp = day.isoformat()
        url = AGG_TRADES_DAILY_URL.format(symbol=symbol, day=stamp)
        destination = root / f"{symbol}-aggTrades-{stamp}.zip"
        archive = _ensure_checked_archive(url, destination)
        archives.append(archive)
        daily_records, daily_diagnostics = _read_archive_to_seconds(
            archive,
            load_start_ns=archive_start_ns,
            trade_end_ns=trade_end_ns,
        )
        records.extend(daily_records)
        for name, value in daily_diagnostics.items():
            aggregate_diagnostics[name] = int(
                aggregate_diagnostics.get(name, 0)
            ) + int(value)

    if not records:
        raise RuntimeError("no aggregate trades loaded")
    seconds, clock_diagnostics = complete_no_trade_seconds_with_seed(
        pd.DataFrame.from_records(records),
        load_start_ns=load_start_ns,
        trade_end_ns=trade_end_ns,
    )
    aggregate_diagnostics.update(clock_diagnostics)

    numeric = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_quote",
        "taker_sell_quote",
    ]
    for name in numeric:
        seconds[name] = pd.to_numeric(seconds[name], errors="raise")
    seconds["timestamp_ns"] = seconds["timestamp_ns"].astype("int64")
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
    if bool(
        (
            flow_error
            > seconds["quote_volume"].clip(lower=1.0) * 1e-9
        ).any()
    ):
        raise RuntimeError("aggressor quote flow does not reconcile")

    gaps = seconds["timestamp_ns"].diff().dropna()
    aggregate_diagnostics["second_rows"] = int(len(seconds.index))
    aggregate_diagnostics["noncontiguous_second_transitions"] = int(
        (gaps != NS_PER_SECOND).sum()
    )
    aggregate_diagnostics["missing_seconds_from_span"] = int(
        (
            (
                int(seconds.iloc[-1]["timestamp_ns"])
                - int(seconds.iloc[0]["timestamp_ns"])
            )
            // NS_PER_SECOND
            + 1
        )
        - len(seconds.index)
    )
    if aggregate_diagnostics["noncontiguous_second_transitions"] != 0:
        raise RuntimeError("completed clock seconds are not contiguous")
    if aggregate_diagnostics["missing_seconds_from_span"] != 0:
        raise RuntimeError("completed clock-second span is incomplete")
    seconds.index = pd.to_datetime(seconds["timestamp_ns"], unit="ns", utc=True)
    seconds.index.name = "timestamp"

    all_archives = tuple([*base.archives, *archives])
    manifest = build_data_manifest(
        cache_root.resolve() / symbol,
        dataset=(
            "binance-usdm-public-aggtrades-causal-seeded-clock-seconds-"
            "and-positioning"
        ),
        include=all_archives,
        metadata_values={
            "symbol": symbol,
            "archive_seed_start": archive_start.isoformat(),
            "event_load_start": event_load_start.isoformat(),
            "trade_start": trade_start.isoformat(),
            "trade_end_exclusive": trade_end.isoformat(),
            "aggregate_trade_archives": len(archives),
            "event_diagnostics": aggregate_diagnostics,
            "positioning_rows": int(len(base.metrics.index)),
            "source": "Binance Vision public USD-M aggTrades and metrics",
            "checksum": "published SHA-256 CHECKSUM verified per archive",
            "missing_second_policy": (
                "causal zero-flow seconds carry the last actual pre-window or "
                "in-window trade price; no future price and no fabricated trade"
            ),
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


__all__ = [
    "complete_no_trade_seconds_with_seed",
    "load_aggtrade_1s_bundle_seeded",
]
