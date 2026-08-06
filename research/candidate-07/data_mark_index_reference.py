"""Checksum-verified Binance USD-M mark and index reference data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from data import _days, _ensure_checked_archive, _read_kline_archive, _timestamp_ns
from data_index_reference import IndexPositioningBundle, load_index_positioning_bundle
from smc_ict_4.manifest import build_data_manifest, write_data_manifest


MARK_DAILY_URL = (
    "https://data.binance.vision/data/futures/um/daily/markPriceKlines/"
    "{symbol}/1m/{symbol}-1m-{day}.zip"
)


@dataclass(frozen=True, slots=True)
class MarkIndexPositioningBundle:
    frame: pd.DataFrame
    funding: tuple
    metrics: pd.DataFrame
    index_frame: pd.DataFrame
    mark_frame: pd.DataFrame
    archives: tuple[Path, ...]
    data_manifest_path: Path


def _validated_reference_frame(
    rows: list[dict[str, str]],
    *,
    load_start_ns: int,
    trade_end_ns: int,
    label: str,
) -> pd.DataFrame:
    if not rows:
        raise RuntimeError(f"no {label} rows loaded")
    frame = pd.DataFrame.from_records(rows)
    for name in ("open", "high", "low", "close"):
        frame[name] = pd.to_numeric(frame[name], errors="raise")
    frame["close_time_ns"] = frame["close_time"].map(_timestamp_ns)
    frame = frame.sort_values("close_time_ns", kind="stable")
    frame = frame.drop_duplicates(subset=["close_time_ns"], keep="last")
    frame = frame[
        (frame["close_time_ns"] >= load_start_ns)
        & (frame["close_time_ns"] < trade_end_ns)
    ].copy()
    frame.index = pd.to_datetime(frame["close_time_ns"], unit="ns", utc=True)
    frame.index.name = "timestamp"
    frame = frame[["open", "high", "low", "close"]].copy()
    if frame.empty:
        raise RuntimeError(f"{label} frame empty after interval filter")
    invalid = (
        (frame[["open", "high", "low", "close"]] <= 0.0).any(axis=1)
        | (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame["low"])
    )
    if bool(invalid.any()):
        raise RuntimeError(
            f"invalid {label} rows: count={int(invalid.sum())}, "
            f"sample={frame.loc[invalid].head().to_dict(orient='index')}"
        )
    return frame


def load_mark_index_positioning_bundle(
    *,
    symbol: str,
    trade_start: date,
    trade_end: date,
    warmup_days: int,
    cache_root: Path,
    manifest_destination: Path,
) -> MarkIndexPositioningBundle:
    """Load exact completed-minute trade, mark, index, flow and positioning."""
    if trade_end <= trade_start:
        raise ValueError("trade_end must follow trade_start")
    if warmup_days < 0:
        raise ValueError("warmup_days must be non-negative")
    symbol = symbol.upper()
    base_manifest = manifest_destination.with_name(
        f"{manifest_destination.stem}-index-positioning.json"
    )
    base: IndexPositioningBundle = load_index_positioning_bundle(
        symbol=symbol,
        trade_start=trade_start,
        trade_end=trade_end,
        warmup_days=warmup_days,
        cache_root=cache_root,
        manifest_destination=base_manifest,
    )

    load_start = trade_start - timedelta(days=warmup_days)
    mark_root = cache_root.resolve() / symbol / "mark-price-1m"
    mark_archives: list[Path] = []
    rows: list[dict[str, str]] = []
    for day in _days(load_start, trade_end):
        stamp = day.isoformat()
        url = MARK_DAILY_URL.format(symbol=symbol, day=stamp)
        destination = mark_root / f"{symbol}-markPrice-1m-{stamp}.zip"
        archive = _ensure_checked_archive(url, destination)
        mark_archives.append(archive)
        rows.extend(_read_kline_archive(archive))

    load_start_ns = int(
        datetime.combine(load_start, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        * 1e9
    )
    trade_end_ns = int(
        datetime.combine(trade_end, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        * 1e9
    )
    mark_frame = _validated_reference_frame(
        rows,
        load_start_ns=load_start_ns,
        trade_end_ns=trade_end_ns,
        label="mark-price",
    )
    if not mark_frame.index.equals(base.frame.index):
        missing = base.frame.index.difference(mark_frame.index)
        extra = mark_frame.index.difference(base.frame.index)
        raise RuntimeError(
            "mark-price timestamps do not match verified trade bars: "
            f"missing={len(missing)}, extra={len(extra)}, "
            f"missing_sample={[item.isoformat() for item in missing[:5]]}, "
            f"extra_sample={[item.isoformat() for item in extra[:5]]}"
        )

    archives = tuple([*base.archives, *mark_archives])
    manifest = build_data_manifest(
        cache_root.resolve() / symbol,
        dataset=(
            "binance-usdm-public-trade-mark-index-flow-funding-and-positioning"
        ),
        include=archives,
        metadata_values={
            "symbol": symbol,
            "load_start": load_start.isoformat(),
            "trade_start": trade_start.isoformat(),
            "trade_end_exclusive": trade_end.isoformat(),
            "trade_rows": int(len(base.frame.index)),
            "mark_rows": int(len(mark_frame.index)),
            "index_rows": int(len(base.index_frame.index)),
            "positioning_rows": int(len(base.metrics.index)),
            "reference_frequency": "one minute",
            "mark_source": "Binance Vision USD-M markPriceKlines",
            "index_source": "Binance Vision USD-M indexPriceKlines",
            "timestamp_join": "exact completed-minute close timestamp",
            "source": "Binance Vision public data",
        },
    )
    write_data_manifest(manifest_destination, manifest)
    return MarkIndexPositioningBundle(
        frame=base.frame,
        funding=base.funding,
        metrics=base.metrics,
        index_frame=base.index_frame,
        mark_frame=mark_frame,
        archives=archives,
        data_manifest_path=manifest_destination,
    )


__all__ = [
    "MARK_DAILY_URL",
    "MarkIndexPositioningBundle",
    "load_mark_index_positioning_bundle",
]
