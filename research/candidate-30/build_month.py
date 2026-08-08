#!/usr/bin/env python3
"""Build a checksum-verified price/OI/premium month for Candidate 30.

This is an observational data builder, not a backtester.  It deliberately avoids
aggTrades and bookDepth so a multi-year mechanism screen can finish quickly.
Every retained value is available no later than its published observation time:
completed one-minute futures and premium-index bars, and Binance five-minute
metrics delayed by one full metrics interval.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import urllib.request
from typing import Any

import pandas as pd

BASE = "https://data.binance.vision/data/futures/um/daily"
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]
METRIC_COLUMNS = [
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]
METRIC_DAY = re.compile(r"-metrics-(\d{4}-\d{2}-\d{2})\.zip$")


@dataclass(frozen=True, slots=True)
class Evidence:
    endpoint: str
    day: str
    archive: str
    checksum: str
    size_bytes: int
    sha256: str


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _spec(endpoint: str, symbol: str, day: date) -> tuple[str, str]:
    stamp = day.isoformat()
    if endpoint == "klines":
        relative = f"klines/{symbol}/1m/{symbol}-1m-{stamp}.zip"
    elif endpoint == "premiumIndexKlines":
        relative = f"premiumIndexKlines/{symbol}/1m/{symbol}-1m-{stamp}.zip"
    elif endpoint == "metrics":
        relative = f"metrics/{symbol}/{symbol}-metrics-{stamp}.zip"
    else:
        raise ValueError(f"unsupported endpoint: {endpoint}")
    return f"{BASE}/{relative}", Path(relative).name


def download_checked(
    endpoint: str,
    symbol: str,
    day: date,
    cache: Path,
) -> tuple[Path, Evidence]:
    url, filename = _spec(endpoint, symbol, day)
    directory = cache / endpoint
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    return archive, Evidence(
        endpoint=endpoint,
        day=day.isoformat(),
        archive=str(archive),
        checksum=str(checksum),
        size_bytes=archive.stat().st_size,
        sha256=actual,
    )


def _epoch_datetime(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise").astype("int64")
    first = abs(int(numeric.iloc[0]))
    if first >= 10**17:
        unit = "ns"
    elif first >= 10**14:
        unit = "us"
    elif first >= 10**11:
        unit = "ms"
    else:
        unit = "s"
    return pd.to_datetime(numeric, unit=unit, utc=True)


def read_kline(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None)
    if raw.shape[1] == len(KLINE_COLUMNS):
        raw.columns = KLINE_COLUMNS
        first = str(raw.iloc[0]["open_time"])
        if not first.lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    else:
        with_header = pd.read_csv(path, compression="zip")
        if not set(KLINE_COLUMNS).issubset(with_header.columns):
            raise RuntimeError(f"unexpected kline schema in {path}: {list(with_header.columns)}")
        raw = with_header[KLINE_COLUMNS].copy()
    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_quote_volume",
    ):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw["open_time_dt"] = _epoch_datetime(raw["open_time"])
    raw["close_time_dt"] = _epoch_datetime(raw["close_time"])
    result = raw[
        [
            "open_time_dt",
            "close_time_dt",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "taker_buy_quote_volume",
        ]
    ].copy()
    result = result.sort_values("close_time_dt").reset_index(drop=True)
    if result["close_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate completed bars in {path}")
    return result


def read_owned_metrics(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip")
    required = {"create_time", *METRIC_COLUMNS}
    if not required.issubset(raw.columns):
        raise RuntimeError(f"unexpected metrics schema in {path}: {list(raw.columns)}")
    match = METRIC_DAY.search(path.name)
    if match is None:
        raise RuntimeError(f"cannot infer metrics owner day from {path.name}")
    owner = date.fromisoformat(match.group(1))
    raw["create_time"] = pd.to_datetime(raw["create_time"], utc=True, errors="raise")
    raw = raw.loc[raw["create_time"].dt.date == owner].copy()
    if raw.empty:
        raise RuntimeError(f"no metrics rows owned by {owner} in {path}")
    for column in METRIC_COLUMNS:
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw["metrics_observed_time"] = raw["create_time"] + pd.Timedelta(minutes=5)
    raw = raw.sort_values("metrics_observed_time").reset_index(drop=True)
    if raw["metrics_observed_time"].duplicated().any():
        raise RuntimeError(f"duplicate owned metrics observations in {path}")
    return raw[["metrics_observed_time", *METRIC_COLUMNS]].copy()


def _exact_minute_grid(values: pd.Series, start: date, end: date, label: str) -> None:
    actual = pd.DatetimeIndex(pd.to_datetime(values, utc=True).dt.floor("min"))
    expected = pd.date_range(
        pd.Timestamp(start, tz="UTC"),
        pd.Timestamp(end + timedelta(days=1), tz="UTC") - pd.Timedelta(minutes=1),
        freq="1min",
    )
    if actual.has_duplicates or not actual.equals(expected):
        missing = expected.difference(actual)[:10]
        extra = actual.difference(expected)[:10]
        raise RuntimeError(
            f"{label} is not an exact minute grid: rows={len(actual)} "
            f"expected={len(expected)} missing={list(map(str, missing))} "
            f"extra={list(map(str, extra))}",
        )


def build(
    *,
    symbol: str,
    core_start: date,
    core_end: date,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    if core_end < core_start:
        raise ValueError("core end precedes core start")
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    price_parts: list[pd.DataFrame] = []
    premium_parts: list[pd.DataFrame] = []
    metric_parts: list[pd.DataFrame] = []
    evidence: list[Evidence] = []
    day = core_start - timedelta(days=1)
    while day <= core_end:
        for endpoint in ("klines", "premiumIndexKlines", "metrics"):
            archive, item = download_checked(endpoint, symbol, day, cache)
            evidence.append(item)
            if endpoint == "klines":
                price_parts.append(read_kline(archive))
            elif endpoint == "premiumIndexKlines":
                premium = read_kline(archive)[["close_time_dt", "close"]].copy()
                premium = premium.rename(
                    columns={
                        "close_time_dt": "premium_observed_time",
                        "close": "premium_index",
                    },
                )
                premium_parts.append(premium)
            else:
                metric_parts.append(read_owned_metrics(archive))
        day += timedelta(days=1)

    core_open = pd.Timestamp(core_start, tz="UTC")
    core_close = pd.Timestamp(core_end + timedelta(days=1), tz="UTC")
    price = pd.concat(price_parts, ignore_index=True).sort_values("close_time_dt")
    premium = pd.concat(premium_parts, ignore_index=True).sort_values("premium_observed_time")
    metrics = pd.concat(metric_parts, ignore_index=True).sort_values("metrics_observed_time")
    for label, frame, column in (
        ("price", price, "close_time_dt"),
        ("premium", premium, "premium_observed_time"),
        ("metrics", metrics, "metrics_observed_time"),
    ):
        duplicated = frame[column].duplicated(keep=False)
        if duplicated.any():
            raise RuntimeError(
                f"{label} has duplicate observations after owned-day assembly: "
                f"{frame.loc[duplicated, column].head().tolist()}",
            )

    price["time"] = pd.to_datetime(price["close_time_dt"], utc=True).dt.floor("min")
    price = price[(price["time"] >= core_open) & (price["time"] < core_close)].copy()
    price = price.sort_values("time").reset_index(drop=True)
    _exact_minute_grid(price["time"], core_start, core_end, "price")

    premium["premium_time"] = pd.to_datetime(
        premium["premium_observed_time"],
        utc=True,
    ).dt.floor("min")
    premium = premium[
        (premium["premium_time"] >= core_open)
        & (premium["premium_time"] < core_close)
    ].copy()
    premium = premium.sort_values("premium_time").reset_index(drop=True)
    _exact_minute_grid(premium["premium_time"], core_start, core_end, "premium")

    joined = price.merge(
        premium[["premium_time", "premium_index"]],
        left_on="time",
        right_on="premium_time",
        how="left",
        validate="one_to_one",
    ).drop(columns=["premium_time"])
    joined = pd.merge_asof(
        joined.sort_values("time"),
        metrics.sort_values("metrics_observed_time"),
        left_on="time",
        right_on="metrics_observed_time",
        direction="backward",
        allow_exact_matches=True,
    )
    joined["metrics_age_seconds"] = (
        joined["time"] - joined["metrics_observed_time"]
    ).dt.total_seconds()
    if joined["metrics_age_seconds"].dropna().lt(0.0).any():
        raise RuntimeError("future metrics observation reached minute rows")
    joined["metrics_ready"] = (
        joined["sum_open_interest"].notna()
        & joined["metrics_age_seconds"].between(0.0, 600.0, inclusive="both")
    )
    joined["basis_ready"] = joined["premium_index"].notna()
    joined["observed_time_ns"] = pd.Series(
        (pd.Timestamp(value).value for value in joined["time"]),
        dtype="int64",
    )
    joined = joined.drop(columns=["close_time_dt", "metrics_observed_time"])
    if joined["observed_time_ns"].duplicated().any():
        raise RuntimeError("duplicate minute observation timestamps")

    data_path = output / "minute_state.csv.gz"
    joined.to_csv(data_path, index=False, compression="gzip")
    manifest = {
        "schema_version": 1,
        "candidate": "candidate-30-lightweight-leverage-state",
        "symbol": symbol,
        "core_start": core_start.isoformat(),
        "core_end": core_end.isoformat(),
        "calendar_days": (core_end - core_start).days + 1,
        "rows": len(joined),
        "first_observed_time_ns": int(joined["observed_time_ns"].iloc[0]),
        "last_observed_time_ns": int(joined["observed_time_ns"].iloc[-1]),
        "metrics_ready_rows": int(joined["metrics_ready"].sum()),
        "basis_ready_rows": int(joined["basis_ready"].sum()),
        "metrics_boundary_policy": "create_time_owned_by_filename_utc_day",
        "files": {
            "minute_state.csv.gz": {
                "size_bytes": data_path.stat().st_size,
                "sha256": sha256_file(data_path),
            },
        },
        "raw_evidence": [asdict(item) for item in evidence],
    }
    (output / "month_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--core-start", required=True)
    parser.add_argument("--core-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        symbol=args.symbol,
        core_start=date.fromisoformat(args.core_start),
        core_end=date.fromisoformat(args.core_end),
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
