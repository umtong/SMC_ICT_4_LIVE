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
NS_PER_MINUTE = 60 * 1_000_000_000
NS_PER_FIVE_MINUTES = 5 * NS_PER_MINUTE
NS_PER_TEN_MINUTES = 10 * NS_PER_MINUTE


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


def _drop_invalid_positioning_rows(
    metrics: pd.DataFrame,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Drop unusable snapshots without forward filling or interpolation.

    A zero or missing open-interest snapshot is not a market state. The exact
    completed five-minute interval is made unavailable. The router separately
    treats the first later non-contiguous snapshot as OI-neutral, so a ten-minute
    change cannot masquerade as a normal five-minute inventory impulse.
    """
    required = {
        "timestamp",
        "sum_open_interest",
        "sum_open_interest_value",
    }
    missing = required - set(metrics.columns)
    if missing:
        raise ValueError(f"positioning cleanup columns missing: {sorted(missing)}")
    invalid = (
        metrics["sum_open_interest"].isna()
        | (metrics["sum_open_interest"] <= 0)
        | metrics["sum_open_interest_value"].isna()
        | (metrics["sum_open_interest_value"] <= 0)
    )
    timestamps = tuple(
        timestamp.isoformat()
        for timestamp in metrics.loc[invalid, "timestamp"].tolist()
    )
    return metrics.loc[~invalid].copy(), timestamps


def _positioning_cadence_diagnostics(metrics: pd.DataFrame) -> dict[str, int]:
    """Validate interval cadence while retaining actual publication seconds.

    Binance metrics rows may be published a few seconds after a nominal
    five-minute boundary. A raw 298/302-second delta is therefore not a missing
    interval. We classify cadence by the completed interval label while keeping
    the original publication timestamp untouched for causal availability. No
    snapshot is shifted backward, forward-filled or interpolated.
    """
    required = {"timestamp", "timestamp_ns", "interval_timestamp_ns"}
    missing = required.difference(metrics.columns)
    if missing:
        raise ValueError(f"positioning cadence columns missing: {sorted(missing)}")
    if metrics.empty:
        raise ValueError("positioning cadence frame must not be empty")
    if not metrics["timestamp_ns"].is_monotonic_increasing:
        raise RuntimeError("positioning metrics are not monotonic")
    if any(timestamp.minute % 5 for timestamp in metrics["timestamp"]):
        raise RuntimeError("positioning timestamps are not in a five-minute label minute")

    delays = (
        metrics["timestamp_ns"].astype("int64")
        - metrics["interval_timestamp_ns"].astype("int64")
    )
    if bool(((delays < 0) | (delays >= NS_PER_MINUTE)).any()):
        raise RuntimeError(
            "positioning publication time falls outside the labelled boundary minute"
        )
    if bool(metrics["interval_timestamp_ns"].duplicated().any()):
        raise RuntimeError("duplicate positioning snapshots for one five-minute interval")

    interval_gaps = (
        metrics["interval_timestamp_ns"].astype("int64").diff().dropna()
    )
    unexpected = interval_gaps[
        (interval_gaps != NS_PER_FIVE_MINUTES)
        & (interval_gaps != NS_PER_TEN_MINUTES)
    ]
    if not unexpected.empty:
        raise RuntimeError(
            "unexpected positioning interval gaps: "
            f"count={len(unexpected)}, sample={unexpected.head().tolist()}"
        )
    raw_gaps = metrics["timestamp_ns"].astype("int64").diff().dropna()
    return {
        "publication_jitter_rows": int((delays != 0).sum()),
        "maximum_publication_delay_ns": int(delays.max()),
        "minimum_publication_delay_ns": int(delays.min()),
        "raw_non_300_second_deltas": int(
            (raw_gaps != NS_PER_FIVE_MINUTES).sum()
        ),
        "ten_minute_interval_gaps": int(
            (interval_gaps == NS_PER_TEN_MINUTES).sum()
        ),
    }


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
    metrics["interval_timestamp_ns"] = (
        metrics["timestamp_ns"].astype("int64") // NS_PER_FIVE_MINUTES
    ) * NS_PER_FIVE_MINUTES
    metrics["interval_timestamp"] = pd.to_datetime(
        metrics["interval_timestamp_ns"],
        unit="ns",
        utc=True,
    )
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

    metrics, invalid_timestamps = _drop_invalid_positioning_rows(metrics)
    if metrics.empty:
        raise RuntimeError("no valid positioning snapshots remain")
    cadence = _positioning_cadence_diagnostics(metrics)
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
            "metrics_frequency": (
                "five-minute interval labels with retained publication-second "
                "jitter; one isolated invalid snapshot may create a ten-minute gap"
            ),
            "metrics_cadence": cadence,
            "metrics_columns": list(METRICS_COLUMNS),
            "invalid_positioning_rows_dropped": len(invalid_timestamps),
            "invalid_positioning_timestamps": list(invalid_timestamps),
            "invalid_positioning_policy": (
                "drop exact snapshot; no interpolation or forward fill; affected "
                "signal interval unavailable; next non-contiguous OI delta neutral"
            ),
            "publication_jitter_policy": (
                "retain actual publication timestamp; validate cadence by nominal "
                "five-minute interval label; never shift information earlier"
            ),
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


__all__ = [
    "METRICS_COLUMNS",
    "NS_PER_FIVE_MINUTES",
    "PositioningBundle",
    "_drop_invalid_positioning_rows",
    "_positioning_cadence_diagnostics",
    "load_positioning_bundle",
]
