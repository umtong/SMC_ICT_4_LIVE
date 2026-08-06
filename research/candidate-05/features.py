#!/usr/bin/env python3
"""Causal Binance public-data ingestion for Candidate 05.

This module creates observations only.  It never matches orders, simulates
fills, maintains positions, or computes strategy PnL.  All trade decisions and
all execution/accounting are performed later inside a NautilusTrader Strategy
and BacktestNode.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import urllib.request
from typing import Iterable

import numpy as np
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
AGG_COLUMNS = [
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
]


@dataclass(frozen=True, slots=True)
class RawEvidence:
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


def _archive_spec(endpoint: str, symbol: str, day: date) -> tuple[str, str]:
    stamp = day.isoformat()
    if endpoint == "klines":
        relative = f"klines/{symbol}/1m/{symbol}-1m-{stamp}.zip"
    elif endpoint == "aggTrades":
        relative = f"aggTrades/{symbol}/{symbol}-aggTrades-{stamp}.zip"
    elif endpoint == "bookDepth":
        relative = f"bookDepth/{symbol}/{symbol}-bookDepth-{stamp}.zip"
    else:
        raise ValueError(f"unsupported endpoint: {endpoint}")
    return f"{BASE}/{relative}", Path(relative).name


def download_checked(endpoint: str, symbol: str, day: date, cache: Path) -> tuple[Path, Path, RawEvidence]:
    url, filename = _archive_spec(endpoint, symbol, day)
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
    evidence = RawEvidence(
        endpoint=endpoint,
        day=day.isoformat(),
        archive=str(archive),
        checksum=str(checksum),
        size_bytes=archive.stat().st_size,
        sha256=actual,
    )
    return archive, checksum, evidence


def _timestamp_unit(values: pd.Series) -> str:
    first = float(pd.to_numeric(values, errors="raise").iloc[0])
    return "us" if first > 10**14 else "ms"


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

    for column in ("open", "high", "low", "close", "volume", "quote_volume"):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    open_unit = _timestamp_unit(raw["open_time"])
    close_unit = _timestamp_unit(raw["close_time"])
    raw["open_time_dt"] = pd.to_datetime(raw["open_time"], unit=open_unit, utc=True)
    raw["close_time_dt"] = pd.to_datetime(raw["close_time"], unit=close_unit, utc=True)
    frame = raw[
        ["open_time_dt", "close_time_dt", "open", "high", "low", "close", "volume", "quote_volume"]
    ].copy()
    frame = frame.sort_values("close_time_dt")
    if frame["close_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate kline close times in {path}")
    return frame


def _agg_reader(path: Path, chunksize: int = 500_000) -> Iterable[pd.DataFrame]:
    probe = pd.read_csv(path, compression="zip", nrows=1)
    if set(AGG_COLUMNS).issubset(probe.columns):
        return pd.read_csv(path, compression="zip", usecols=AGG_COLUMNS, chunksize=chunksize)
    return pd.read_csv(
        path,
        compression="zip",
        header=None,
        names=AGG_COLUMNS,
        usecols=range(len(AGG_COLUMNS)),
        chunksize=chunksize,
    )


def _maker_mask(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    normalized = series.astype(str).str.strip().str.lower()
    return normalized.isin({"true", "1", "t", "yes"})


def aggregate_agg_trades(path: Path) -> pd.DataFrame:
    grouped_chunks: list[pd.DataFrame] = []
    grouped_tail_chunks: list[pd.DataFrame] = []
    previous_price: float | None = None

    for chunk in _agg_reader(path):
        price = pd.to_numeric(chunk["price"], errors="raise").astype(float)
        quantity = pd.to_numeric(chunk["quantity"], errors="raise").astype(float)
        transact = pd.to_numeric(chunk["transact_time"], errors="raise")
        unit = "us" if float(transact.iloc[0]) > 10**14 else "ms"
        timestamp = pd.to_datetime(transact, unit=unit, utc=True)
        maker = _maker_mask(chunk["is_buyer_maker"])

        notional = price * quantity
        signed = np.where(maker.to_numpy(), -notional.to_numpy(), notional.to_numpy())
        prior = price.shift(1)
        prior.iloc[0] = previous_price if previous_price is not None else price.iloc[0]
        path_bps = np.abs(np.log(price / prior)) * 10_000.0
        previous_price = float(price.iloc[-1])

        work = pd.DataFrame(
            {
                "minute": timestamp.dt.floor("min"),
                "second": timestamp.dt.second,
                "price": price.to_numpy(),
                "notional": notional.to_numpy(),
                "signed_notional": signed,
                "buy_notional": np.where(maker.to_numpy(), 0.0, notional.to_numpy()),
                "sell_notional": np.where(maker.to_numpy(), notional.to_numpy(), 0.0),
                "path_bps": path_bps.to_numpy(),
            },
        )
        grouped_chunks.append(
            work.groupby("minute", sort=True).agg(
                trade_open=("price", "first"),
                trade_high=("price", "max"),
                trade_low=("price", "min"),
                trade_close=("price", "last"),
                notional_60s=("notional", "sum"),
                signed_notional_60s=("signed_notional", "sum"),
                buy_notional_60s=("buy_notional", "sum"),
                sell_notional_60s=("sell_notional", "sum"),
                trade_count_60s=("price", "size"),
                path_60s_bps=("path_bps", "sum"),
            ),
        )
        tail = work[work["second"] >= 45]
        if not tail.empty:
            grouped_tail_chunks.append(
                tail.groupby("minute", sort=True).agg(
                    notional_15s=("notional", "sum"),
                    signed_notional_15s=("signed_notional", "sum"),
                    trade_count_15s=("price", "size"),
                    path_15s_bps=("path_bps", "sum"),
                ),
            )

    if not grouped_chunks:
        raise RuntimeError(f"empty aggregate-trade archive: {path}")
    full = pd.concat(grouped_chunks).sort_index()
    full = full.groupby(level=0, sort=True).agg(
        trade_open=("trade_open", "first"),
        trade_high=("trade_high", "max"),
        trade_low=("trade_low", "min"),
        trade_close=("trade_close", "last"),
        notional_60s=("notional_60s", "sum"),
        signed_notional_60s=("signed_notional_60s", "sum"),
        buy_notional_60s=("buy_notional_60s", "sum"),
        sell_notional_60s=("sell_notional_60s", "sum"),
        trade_count_60s=("trade_count_60s", "sum"),
        path_60s_bps=("path_60s_bps", "sum"),
    )
    if grouped_tail_chunks:
        tail = pd.concat(grouped_tail_chunks).sort_index()
        tail = tail.groupby(level=0, sort=True).sum()
        full = full.join(tail, how="left")
    for column in ("notional_15s", "signed_notional_15s", "trade_count_15s", "path_15s_bps"):
        if column not in full:
            full[column] = 0.0
        full[column] = full[column].fillna(0.0)
    return full


def _parse_depth_timestamp(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().all():
        unit = "us" if float(numeric.iloc[0]) > 10**14 else "ms"
        return pd.to_datetime(numeric, unit=unit, utc=True)
    return pd.to_datetime(series, utc=True, errors="raise")


def aggregate_book_depth(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip")
    required = {"timestamp", "percentage", "notional"}
    if not required.issubset(raw.columns):
        raise RuntimeError(f"unexpected bookDepth schema in {path}: {list(raw.columns)}")
    raw["ts"] = _parse_depth_timestamp(raw["timestamp"])
    raw["percentage"] = pd.to_numeric(raw["percentage"], errors="raise")
    raw["notional"] = pd.to_numeric(raw["notional"], errors="raise")
    raw = raw.sort_values("ts")
    raw["minute"] = raw["ts"].dt.floor("min")
    last = raw.groupby(["minute", "percentage"], sort=True)["notional"].last().unstack("percentage")
    snapshot = raw.groupby("minute", sort=True)["ts"].max().rename("depth_snapshot_time")
    result = last.join(snapshot)
    for band in (1, 2):
        bid_key = -float(band)
        ask_key = float(band)
        if bid_key not in result.columns or ask_key not in result.columns:
            raise RuntimeError(f"bookDepth missing +/-{band}% bands in {path}")
        result[f"bid_depth_{band}"] = pd.to_numeric(result[bid_key], errors="coerce")
        result[f"ask_depth_{band}"] = pd.to_numeric(result[ask_key], errors="coerce")
    keep = ["depth_snapshot_time", "bid_depth_1", "ask_depth_1", "bid_depth_2", "ask_depth_2"]
    return result[keep]


def build_features(klines: pd.DataFrame, agg: pd.DataFrame, depth: pd.DataFrame) -> pd.DataFrame:
    frame = klines.set_index("open_time_dt").copy()
    frame = frame.join(agg, how="left")
    depth = depth.reindex(frame.index).ffill()
    frame = frame.join(depth, how="left")

    flow_denominator = frame["notional_60s"].replace(0.0, np.nan)
    tail_denominator = frame["notional_15s"].replace(0.0, np.nan)
    frame["flow_60s"] = frame["signed_notional_60s"] / flow_denominator
    frame["flow_15s"] = frame["signed_notional_15s"] / tail_denominator
    frame["flow_15s"] = frame["flow_15s"].fillna(frame["flow_60s"])
    frame["flow_3m"] = (
        frame["signed_notional_60s"].rolling(3, min_periods=3).sum()
        / frame["notional_60s"].rolling(3, min_periods=3).sum().replace(0.0, np.nan)
    )

    frame["ret_60s_bps"] = np.log(frame["trade_close"] / frame["trade_open"]) * 10_000.0
    frame["efficiency_60s"] = (
        frame["ret_60s_bps"].abs() / frame["path_60s_bps"].replace(0.0, np.nan)
    ).clip(0.0, 1.0)
    frame["flow_price_alignment_60s"] = np.sign(frame["flow_60s"]) * frame["ret_60s_bps"]
    frame["absorption_60s"] = frame["flow_60s"].abs() * (1.0 - frame["efficiency_60s"])

    past_median = frame["notional_60s"].shift(1).rolling(120, min_periods=60).median()
    frame["notional_burst"] = frame["notional_60s"] / past_median.replace(0.0, np.nan)
    past_trade_median = frame["trade_count_60s"].shift(1).rolling(120, min_periods=60).median()
    frame["trade_count_burst"] = frame["trade_count_60s"] / past_trade_median.replace(0.0, np.nan)

    for band in (1, 2):
        total = frame[f"bid_depth_{band}"] + frame[f"ask_depth_{band}"]
        frame[f"depth_imbalance_{band}"] = (
            (frame[f"bid_depth_{band}"] - frame[f"ask_depth_{band}"])
            / total.replace(0.0, np.nan)
        )
        frame[f"bid_depth_change_{band}_1m"] = frame[f"bid_depth_{band}"].pct_change(1, fill_method=None)
        frame[f"ask_depth_change_{band}_1m"] = frame[f"ask_depth_{band}"].pct_change(1, fill_method=None)
        frame[f"bid_depth_change_{band}_5m"] = frame[f"bid_depth_{band}"].pct_change(5, fill_method=None)
        frame[f"ask_depth_change_{band}_5m"] = frame[f"ask_depth_{band}"].pct_change(5, fill_method=None)

    close_boundary = frame.index.to_series(index=frame.index) + pd.Timedelta(minutes=1)
    frame["depth_snapshot_age_seconds"] = (
        close_boundary - frame["depth_snapshot_time"]
    ).dt.total_seconds()
    frame["observed_time_ns"] = frame["close_time_dt"].astype("int64")

    required = [
        "flow_15s",
        "flow_60s",
        "flow_3m",
        "efficiency_60s",
        "notional_burst",
        "depth_imbalance_1",
        "bid_depth_change_1_1m",
        "ask_depth_change_1_1m",
        "depth_snapshot_age_seconds",
    ]
    frame["feature_ready"] = frame[required].notna().all(axis=1) & frame["depth_snapshot_age_seconds"].le(120.0)

    columns = [
        "observed_time_ns",
        "feature_ready",
        "flow_15s",
        "flow_60s",
        "flow_3m",
        "notional_60s",
        "notional_burst",
        "trade_count_60s",
        "trade_count_burst",
        "ret_60s_bps",
        "path_60s_bps",
        "efficiency_60s",
        "flow_price_alignment_60s",
        "absorption_60s",
        "depth_snapshot_age_seconds",
        "depth_imbalance_1",
        "depth_imbalance_2",
        "bid_depth_change_1_1m",
        "ask_depth_change_1_1m",
        "bid_depth_change_1_5m",
        "ask_depth_change_1_5m",
        "bid_depth_change_2_1m",
        "ask_depth_change_2_1m",
        "bid_depth_change_2_5m",
        "ask_depth_change_2_5m",
    ]
    result = frame[columns].copy()
    if result["observed_time_ns"].duplicated().any():
        raise RuntimeError("duplicate feature observation timestamps")
    if not result["observed_time_ns"].is_monotonic_increasing:
        raise RuntimeError("feature observation timestamps are not monotonic")
    return result


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> tuple[pd.DataFrame, Path, list[Path], list[RawEvidence]]:
    """Download, verify, and transform one contiguous range."""
    if end < start:
        raise ValueError("end precedes start")
    output.mkdir(parents=True, exist_ok=True)
    kline_frames: list[pd.DataFrame] = []
    agg_frames: list[pd.DataFrame] = []
    depth_frames: list[pd.DataFrame] = []
    manifest_files: list[Path] = []
    evidence: list[RawEvidence] = []

    day = start
    while day <= end:
        kline_path, kline_checksum, kline_evidence = download_checked("klines", symbol, day, cache)
        agg_path, agg_checksum, agg_evidence = download_checked("aggTrades", symbol, day, cache)
        depth_path, depth_checksum, depth_evidence = download_checked("bookDepth", symbol, day, cache)
        kline_frames.append(read_kline(kline_path))
        agg_frames.append(aggregate_agg_trades(agg_path))
        depth_frames.append(aggregate_book_depth(depth_path))
        manifest_files.extend(
            [kline_path, kline_checksum, agg_path, agg_checksum, depth_path, depth_checksum],
        )
        evidence.extend([kline_evidence, agg_evidence, depth_evidence])
        day += timedelta(days=1)

    klines = pd.concat(kline_frames, ignore_index=True).sort_values("close_time_dt")
    if klines["close_time_dt"].duplicated().any():
        raise RuntimeError("duplicate klines across daily files")
    expected_days = (end - start).days + 1
    if len(klines) < expected_days * 1_430:
        raise RuntimeError(f"incomplete minute data: {len(klines)} rows for {expected_days} days")

    agg = pd.concat(agg_frames).sort_index()
    if agg.index.duplicated().any():
        agg = agg.groupby(level=0, sort=True).agg(
            trade_open=("trade_open", "first"),
            trade_high=("trade_high", "max"),
            trade_low=("trade_low", "min"),
            trade_close=("trade_close", "last"),
            notional_60s=("notional_60s", "sum"),
            signed_notional_60s=("signed_notional_60s", "sum"),
            buy_notional_60s=("buy_notional_60s", "sum"),
            sell_notional_60s=("sell_notional_60s", "sum"),
            trade_count_60s=("trade_count_60s", "sum"),
            path_60s_bps=("path_60s_bps", "sum"),
            notional_15s=("notional_15s", "sum"),
            signed_notional_15s=("signed_notional_15s", "sum"),
            trade_count_15s=("trade_count_15s", "sum"),
            path_15s_bps=("path_15s_bps", "sum"),
        )
    depth = pd.concat(depth_frames).sort_index()
    if depth.index.duplicated().any():
        depth = depth[~depth.index.duplicated(keep="last")]

    feature_frame = build_features(klines, agg, depth)
    feature_path = output / "features.csv.gz"
    feature_frame.to_csv(feature_path, index=False, compression="gzip")
    (output / "raw_evidence.json").write_text(
        json.dumps([asdict(item) for item in evidence], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return klines, feature_path, manifest_files, evidence
