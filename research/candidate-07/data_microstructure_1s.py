"""Checksum-verified one-second trade, mark and index data for candidate-07."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from data import (
    _days,
    _ensure_checked_archive,
    _read_kline_archive,
    _timestamp_ns,
)
from data_positioning import PositioningBundle, load_positioning_bundle
from smc_ict_4.manifest import build_data_manifest, write_data_manifest


TRADE_1S_DAILY_URL = (
    "https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1s/"
    "{symbol}-1s-{day}.zip"
)
MARK_1S_DAILY_URL = (
    "https://data.binance.vision/data/futures/um/daily/markPriceKlines/"
    "{symbol}/1s/{symbol}-1s-{day}.zip"
)
INDEX_1S_DAILY_URL = (
    "https://data.binance.vision/data/futures/um/daily/indexPriceKlines/"
    "{symbol}/1s/{symbol}-1s-{day}.zip"
)


@dataclass(frozen=True, slots=True)
class Microstructure1sBundle:
    seconds: pd.DataFrame
    minute_positioning: PositioningBundle
    archives: tuple[Path, ...]
    data_manifest_path: Path
    cadence_gaps: dict[str, int]


def _load_archives(
    *,
    symbol: str,
    days: Iterable[date],
    cache_root: Path,
    kind: str,
) -> tuple[list[pd.DataFrame], list[Path]]:
    if kind == "trade":
        url_template = TRADE_1S_DAILY_URL
        directory = "klines-1s"
    elif kind == "mark":
        url_template = MARK_1S_DAILY_URL
        directory = "mark-price-1s"
    elif kind == "index":
        url_template = INDEX_1S_DAILY_URL
        directory = "index-price-1s"
    else:
        raise ValueError(f"unknown one-second data kind: {kind}")

    frames: list[pd.DataFrame] = []
    archives: list[Path] = []
    root = cache_root.resolve() / symbol / directory
    for day in days:
        stamp = day.isoformat()
        url = url_template.format(symbol=symbol, day=stamp)
        destination = root / f"{symbol}-{kind}-1s-{stamp}.zip"
        archive = _ensure_checked_archive(url, destination)
        archives.append(archive)
        rows = _read_kline_archive(archive)
        if not rows:
            raise RuntimeError(f"no {kind} one-second rows in {archive}")
        frame = pd.DataFrame.from_records(rows)
        frame["close_time_ns"] = frame["close_time"].map(_timestamp_ns)
        numeric = ["open", "high", "low", "close"]
        if kind == "trade":
            numeric.extend(
                [
                    "volume",
                    "quote_volume",
                    "trade_count",
                    "taker_buy_base",
                    "taker_buy_quote",
                ]
            )
        for name in numeric:
            frame[name] = pd.to_numeric(frame[name], errors="raise")
        keep = ["close_time_ns", *numeric]
        frames.append(frame[keep].copy())
    return frames, archives


def _validated_frame(
    frames: list[pd.DataFrame],
    *,
    load_start_ns: int,
    trade_end_ns: int,
    kind: str,
) -> pd.DataFrame:
    if not frames:
        raise RuntimeError(f"no {kind} one-second frames loaded")
    frame = pd.concat(frames, ignore_index=True)
    frame = frame.sort_values("close_time_ns", kind="stable")
    frame = frame.drop_duplicates(subset=["close_time_ns"], keep="last")
    frame = frame[
        (frame["close_time_ns"] >= load_start_ns)
        & (frame["close_time_ns"] < trade_end_ns)
    ].copy()
    if frame.empty:
        raise RuntimeError(f"{kind} one-second frame empty after interval filter")
    if not frame["close_time_ns"].is_monotonic_increasing:
        raise RuntimeError(f"{kind} one-second timestamps are not monotonic")
    if frame["close_time_ns"].duplicated().any():
        raise RuntimeError(f"{kind} one-second timestamps are duplicated")
    invalid_price = (
        (frame[["open", "high", "low", "close"]] <= 0.0).any(axis=1)
        | (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame["low"])
    )
    if bool(invalid_price.any()):
        raise RuntimeError(
            f"invalid {kind} one-second prices: count={int(invalid_price.sum())}, "
            f"sample={frame.loc[invalid_price].head().to_dict(orient='records')}"
        )
    if kind == "trade":
        invalid_flow = (
            (frame[["volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]] < 0.0)
            .any(axis=1)
            | (frame["taker_buy_base"] > frame["volume"] + 1e-9)
            | (frame["taker_buy_quote"] > frame["quote_volume"] + 1e-6)
            | (frame["trade_count"] < 0)
        )
        if bool(invalid_flow.any()):
            raise RuntimeError(
                f"invalid trade one-second flow: count={int(invalid_flow.sum())}, "
                f"sample={frame.loc[invalid_flow].head().to_dict(orient='records')}"
            )
    frame = frame.set_index("close_time_ns", drop=False)
    return frame


def _gap_count(frame: pd.DataFrame) -> int:
    gaps = frame["close_time_ns"].diff().dropna()
    return int((gaps != 1_000_000_000).sum())


def load_microstructure_1s_bundle(
    *,
    symbol: str,
    trade_start: date,
    trade_end: date,
    positioning_warmup_days: int,
    micro_warmup_days: int,
    cache_root: Path,
    manifest_destination: Path,
) -> Microstructure1sBundle:
    """Load exact one-second trade/mark/index bars and completed OI metrics."""
    if trade_end <= trade_start:
        raise ValueError("trade_end must follow trade_start")
    if positioning_warmup_days < 0 or micro_warmup_days < 0:
        raise ValueError("warmup days must be non-negative")
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

    load_start = trade_start - timedelta(days=micro_warmup_days)
    requested_days = tuple(_days(load_start, trade_end))
    trade_frames, trade_archives = _load_archives(
        symbol=symbol,
        days=requested_days,
        cache_root=cache_root,
        kind="trade",
    )
    mark_frames, mark_archives = _load_archives(
        symbol=symbol,
        days=requested_days,
        cache_root=cache_root,
        kind="mark",
    )
    index_frames, index_archives = _load_archives(
        symbol=symbol,
        days=requested_days,
        cache_root=cache_root,
        kind="index",
    )

    load_start_ns = int(
        datetime.combine(load_start, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        * 1e9
    )
    trade_end_ns = int(
        datetime.combine(trade_end, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        * 1e9
    )
    trade = _validated_frame(
        trade_frames,
        load_start_ns=load_start_ns,
        trade_end_ns=trade_end_ns,
        kind="trade",
    )
    mark = _validated_frame(
        mark_frames,
        load_start_ns=load_start_ns,
        trade_end_ns=trade_end_ns,
        kind="mark",
    )
    index = _validated_frame(
        index_frames,
        load_start_ns=load_start_ns,
        trade_end_ns=trade_end_ns,
        kind="index",
    )

    seconds = trade.copy()
    for prefix, reference in (("mark", mark), ("index", index)):
        renamed = reference[["open", "high", "low", "close"]].rename(
            columns={name: f"{prefix}_{name}" for name in ("open", "high", "low", "close")}
        )
        seconds = seconds.join(renamed, how="left")
    reference_columns = [
        f"{prefix}_{name}"
        for prefix in ("mark", "index")
        for name in ("open", "high", "low", "close")
    ]
    seconds["reference_valid"] = seconds[reference_columns].notna().all(axis=1)
    seconds.index = pd.to_datetime(seconds["close_time_ns"], unit="ns", utc=True)
    seconds.index.name = "timestamp"

    cadence_gaps = {
        "trade": _gap_count(trade),
        "mark": _gap_count(mark),
        "index": _gap_count(index),
        "missing_mark_or_index_at_trade_second": int((~seconds["reference_valid"]).sum()),
    }
    archives = tuple(
        [
            *base.archives,
            *trade_archives,
            *mark_archives,
            *index_archives,
        ]
    )
    manifest = build_data_manifest(
        cache_root.resolve() / symbol,
        dataset=(
            "binance-usdm-public-one-second-trade-mark-index-and-five-minute-positioning"
        ),
        include=archives,
        metadata_values={
            "symbol": symbol,
            "micro_load_start": load_start.isoformat(),
            "trade_start": trade_start.isoformat(),
            "trade_end_exclusive": trade_end.isoformat(),
            "one_second_trade_rows": int(len(trade.index)),
            "one_second_mark_rows": int(len(mark.index)),
            "one_second_index_rows": int(len(index.index)),
            "exact_reference_rows": int(seconds["reference_valid"].sum()),
            "positioning_rows": int(len(base.metrics.index)),
            "cadence_gaps": cadence_gaps,
            "source": "Binance Vision public USD-M archives",
            "checksum": "published SHA-256 CHECKSUM verified per archive",
        },
    )
    write_data_manifest(manifest_destination, manifest)
    return Microstructure1sBundle(
        seconds=seconds,
        minute_positioning=base,
        archives=archives,
        data_manifest_path=manifest_destination,
        cadence_gaps=cadence_gaps,
    )


__all__ = [
    "INDEX_1S_DAILY_URL",
    "MARK_1S_DAILY_URL",
    "Microstructure1sBundle",
    "TRADE_1S_DAILY_URL",
    "load_microstructure_1s_bundle",
]
