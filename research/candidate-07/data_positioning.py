"""Checksum-verified Binance USD-M positioning data for candidate-07."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import zipfile

import pandas as pd

from data import _days, _ensure_checked_archive
from data_flow import load_flow_bundle
from smc_ict_4.manifest import build_data_manifest, write_data_manifest


METRICS_URL = (
    "https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/"
    "{symbol}-metrics-{day}.zip"
)
METRICS_COLUMNS = (
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)


@dataclass(frozen=True, slots=True)
class PositioningBundle:
    frame: pd.DataFrame
    funding: tuple
    metrics: pd.DataFrame
    archives: tuple[Path, ...]
    data_manifest_path: Path


def _read_metrics_archive(path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"expected one metrics CSV in {path}, found {names}")
        with archive.open(names[0]) as raw:
            rows = csv.DictReader(line.decode("utf-8") for line in raw)
            if rows.fieldnames is None:
                raise RuntimeError(f"metrics header missing in {path}")
            required = {"create_time", "symbol", *METRICS_COLUMNS}
            missing = required - set(rows.fieldnames)
            if missing:
                raise RuntimeError(f"metrics columns missing in {path}: {sorted(missing)}")
            records.extend(dict(row) for row in rows)
    return records


def _numeric_or_nan(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.replace("", pd.NA), errors="coerce")


def _invalid_preview(
    metrics: pd.DataFrame,
    mask: pd.Series,
) -> list[dict[str, object]]:
    columns = [
        "timestamp",
        "sum_open_interest",
        "sum_open_interest_value",
        "create_time",
    ]
    return metrics.loc[mask, columns].head(12).to_dict(orient="records")


def load_positioning_bundle(
    *,
    symbol: str,
    trade_start: date,
    trade_end: date,
    warmup_days: int,
    cache_root: Path,
    manifest_destination: Path,
) -> PositioningBundle:
    """Load verified OHLCV, taker flow, funding and five-minute positioning."""
    if trade_end <= trade_start:
        raise ValueError("trade_end must follow trade_start")
    if warmup_days < 0:
        raise ValueError("warmup_days must be non-negative")
    symbol = symbol.upper()
    base_manifest = manifest_destination.with_name(f"{manifest_destination.stem}-base.json")
    flow = load_flow_bundle(
        symbol=symbol,
        trade_start=trade_start,
        trade_end=trade_end,
        warmup_days=warmup_days,
        cache_root=cache_root,
        manifest_destination=base_manifest,
    )

    load_start = trade_start - timedelta(days=warmup_days)
    root = cache_root.resolve() / symbol / "metrics-5m"
    metric_archives: list[Path] = []
    rows: list[dict[str, str]] = []
    for day in _days(load_start, trade_end):
        stamp = day.isoformat()
        url = METRICS_URL.format(symbol=symbol, day=stamp)
        destination = root / f"{symbol}-metrics-{stamp}.zip"
        archive = _ensure_checked_archive(url, destination)
        metric_archives.append(archive)
        rows.extend(_read_metrics_archive(archive))
    if not rows:
        raise RuntimeError("no positioning metrics rows loaded")

    metrics = pd.DataFrame.from_records(rows)
    metrics = metrics[metrics["symbol"].str.upper() == symbol].copy()
    metrics["timestamp"] = pd.to_datetime(metrics["create_time"], utc=True, errors="raise")
    metrics["timestamp_ns"] = metrics["timestamp"].map(lambda value: int(value.value))
    for name in METRICS_COLUMNS:
        metrics[name] = _numeric_or_nan(metrics[name])
    metrics = metrics.sort_values("timestamp_ns", kind="stable")
    metrics = metrics.drop_duplicates(subset=["timestamp_ns"], keep="last")

    load_start_ns = int(
        datetime.combine(load_start, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        * 1e9
    )
    trade_end_ns = int(
        datetime.combine(trade_end, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        * 1e9
    )
    metrics = metrics[
        (metrics["timestamp_ns"] >= load_start_ns)
        & (metrics["timestamp_ns"] < trade_end_ns)
    ].copy()
    if metrics.empty:
        raise RuntimeError("positioning metrics empty after interval filter")
    if not metrics["timestamp_ns"].is_monotonic_increasing:
        raise RuntimeError("positioning metrics are not monotonic")

    invalid_oi = metrics["sum_open_interest"].isna() | (
        metrics["sum_open_interest"] <= 0
    )
    if bool(invalid_oi.any()):
        raise RuntimeError(
            "open interest must be present and positive: "
            f"count={int(invalid_oi.sum())}, sample={_invalid_preview(metrics, invalid_oi)}"
        )
    invalid_oi_value = metrics["sum_open_interest_value"].isna() | (
        metrics["sum_open_interest_value"] <= 0
    )
    if bool(invalid_oi_value.any()):
        raise RuntimeError(
            "open interest value must be present and positive: "
            f"count={int(invalid_oi_value.sum())}, "
            f"sample={_invalid_preview(metrics, invalid_oi_value)}"
        )
    if any(timestamp.minute % 5 for timestamp in metrics["timestamp"]):
        raise RuntimeError("positioning timestamps are not aligned to five-minute boundaries")

    gaps = metrics["timestamp_ns"].diff().dropna()
    five_minutes_ns = 5 * 60 * 1_000_000_000
    ten_minutes_ns = 10 * 60 * 1_000_000_000
    unexpected = gaps[(gaps != five_minutes_ns) & (gaps != ten_minutes_ns)]
    if not unexpected.empty:
        raise RuntimeError(
            "unexpected positioning cadence gaps: "
            f"count={len(unexpected)}, sample={unexpected.head().tolist()}"
        )
    metrics = metrics.set_index("timestamp", drop=False)

    archives = tuple([*flow.archives, *metric_archives])
    manifest = build_data_manifest(
        cache_root.resolve() / symbol,
        dataset="binance-usdm-public-klines-funding-and-positioning",
        include=archives,
        metadata_values={
            "symbol": symbol,
            "load_start": load_start.isoformat(),
            "trade_start": trade_start.isoformat(),
            "trade_end_exclusive": trade_end.isoformat(),
            "flow_rows": int(len(flow.frame.index)),
            "metrics_rows": int(len(metrics.index)),
            "metrics_frequency": "five minutes; an expected ten-minute UTC-day boundary gap is allowed",
            "metrics_columns": list(METRICS_COLUMNS),
            "source": "Binance Vision public USD-M archives",
        },
    )
    write_data_manifest(manifest_destination, manifest)
    return PositioningBundle(
        frame=flow.frame,
        funding=flow.funding,
        metrics=metrics,
        archives=archives,
        data_manifest_path=manifest_destination,
    )


__all__ = ["METRICS_COLUMNS", "PositioningBundle", "load_positioning_bundle"]
