#!/usr/bin/env python3
"""Causal minute features from Binance USD-M public high-resolution archives.

For every minute labelled by its open time, all feature values use only data
through that minute's close. ``observed_time`` records when the row is knowable.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import urllib.request

import numpy as np
import pandas as pd

BASE = "https://data.binance.vision/data/futures/um/daily"


@dataclass(frozen=True)
class Evidence:
    endpoint: str
    day: str
    path: str
    size_bytes: int
    sha256: str


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def archive_spec(endpoint: str, symbol: str, day: date) -> tuple[str, str]:
    stamp = day.isoformat()
    if endpoint == "aggTrades":
        relative = f"aggTrades/{symbol}/{symbol}-aggTrades-{stamp}.zip"
    elif endpoint == "bookDepth":
        relative = f"bookDepth/{symbol}/{symbol}-bookDepth-{stamp}.zip"
    elif endpoint == "metrics":
        relative = f"metrics/{symbol}/{symbol}-metrics-{stamp}.zip"
    elif endpoint in {"markPriceKlines", "indexPriceKlines", "premiumIndexKlines"}:
        relative = f"{endpoint}/{symbol}/1m/{symbol}-1m-{stamp}.zip"
    else:
        raise ValueError(endpoint)
    return f"{BASE}/{relative}", relative


def acquire(
    endpoint: str,
    symbol: str,
    day: date,
    cache: Path,
    *,
    required: bool,
) -> tuple[Path | None, Evidence | None]:
    url, relative = archive_spec(endpoint, symbol, day)
    directory = cache / endpoint
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / Path(relative).name
    checksum = target.with_suffix(target.suffix + ".CHECKSUM")
    try:
        if not target.exists():
            urllib.request.urlretrieve(url, target)
        if not checksum.exists():
            urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    except Exception:
        if required:
            raise
        target.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)
        return None, None
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = digest(target)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {target}: {actual} != {expected}")
    return target, Evidence(endpoint, day.isoformat(), str(target), target.stat().st_size, actual)


def aggregate_trades(path: Path, day: date) -> pd.DataFrame:
    groups: list[pd.DataFrame] = []
    columns = [
        "agg_trade_id",
        "price",
        "quantity",
        "first_trade_id",
        "last_trade_id",
        "transact_time",
        "is_buyer_maker",
    ]
    for chunk in pd.read_csv(path, compression="zip", usecols=columns, chunksize=500_000):
        maker = (
            chunk["is_buyer_maker"].astype(str).str.lower().eq("true")
            if chunk["is_buyer_maker"].dtype == object
            else chunk["is_buyer_maker"].astype(bool)
        )
        chunk["ts"] = pd.to_datetime(chunk["transact_time"], unit="ms", utc=True).dt.floor("s")
        notional = chunk["price"] * chunk["quantity"]
        chunk["notional"] = notional
        chunk["signed_notional"] = np.where(maker, -notional, notional)
        chunk["buy_notional"] = np.where(maker, 0.0, notional)
        chunk["sell_notional"] = np.where(maker, notional, 0.0)
        chunk["trade_count"] = (chunk["last_trade_id"] - chunk["first_trade_id"] + 1).clip(lower=1)
        chunk["max_trade_notional"] = notional
        groups.append(
            chunk.groupby("ts", sort=True).agg(
                open=("price", "first"),
                high=("price", "max"),
                low=("price", "min"),
                close=("price", "last"),
                notional=("notional", "sum"),
                signed_notional=("signed_notional", "sum"),
                buy_notional=("buy_notional", "sum"),
                sell_notional=("sell_notional", "sum"),
                agg_count=("agg_trade_id", "count"),
                trade_count=("trade_count", "sum"),
                max_trade_notional=("max_trade_notional", "max"),
            ),
        )
    if not groups:
        raise RuntimeError(f"empty archive: {path}")
    seconds = pd.concat(groups).sort_index().groupby(level=0, sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        notional=("notional", "sum"),
        signed_notional=("signed_notional", "sum"),
        buy_notional=("buy_notional", "sum"),
        sell_notional=("sell_notional", "sum"),
        agg_count=("agg_count", "sum"),
        trade_count=("trade_count", "sum"),
        max_trade_notional=("max_trade_notional", "max"),
    )
    start = pd.Timestamp(day, tz="UTC")
    index = pd.date_range(start, start + pd.Timedelta(days=1) - pd.Timedelta(seconds=1), freq="1s")
    seconds = seconds.reindex(index)
    seconds["close"] = seconds["close"].ffill().bfill()
    for column in ("open", "high", "low"):
        seconds[column] = seconds[column].fillna(seconds["close"])
    flow_columns = [
        "notional",
        "signed_notional",
        "buy_notional",
        "sell_notional",
        "agg_count",
        "trade_count",
        "max_trade_notional",
    ]
    seconds[flow_columns] = seconds[flow_columns].fillna(0.0)

    one_second_return = seconds["close"].pct_change(fill_method=None) * 10_000.0
    travelled = one_second_return.abs()
    flow_sign = np.sign(seconds["signed_notional"])
    minute = pd.DataFrame(index=seconds.resample("1min").last().index)
    minute["trade_close"] = seconds["close"].resample("1min").last()

    for window in (5, 15, 30, 60, 180, 300):
        total = seconds["notional"].rolling(window, min_periods=window).sum()
        signed = seconds["signed_notional"].rolling(window, min_periods=window).sum()
        buy = seconds["buy_notional"].rolling(window, min_periods=window).sum()
        path = travelled.rolling(window, min_periods=window).sum()
        change = seconds["close"].pct_change(window, fill_method=None) * 10_000.0
        minute[f"flow_{window}s"] = (signed / total.replace(0.0, np.nan)).resample("1min").last()
        minute[f"buy_share_{window}s"] = (buy / total.replace(0.0, np.nan)).resample("1min").last()
        minute[f"notional_{window}s"] = total.resample("1min").last()
        minute[f"ret_{window}s_bps"] = change.resample("1min").last()
        minute[f"path_{window}s_bps"] = path.resample("1min").last()
        minute[f"eff_{window}s"] = (change.abs() / path.replace(0.0, np.nan)).resample("1min").last()
        minute[f"trade_count_{window}s"] = (
            seconds["trade_count"].rolling(window, min_periods=window).sum().resample("1min").last()
        )
        minute[f"active_seconds_{window}s"] = (
            seconds["notional"].gt(0).rolling(window, min_periods=window).sum().resample("1min").last()
        )
        minute[f"flow_sign_persistence_{window}s"] = (
            flow_sign.rolling(window, min_periods=window).mean().abs().resample("1min").last()
        )
        minute[f"max_trade_share_{window}s"] = (
            seconds["max_trade_notional"].rolling(window, min_periods=window).max()
            / total.replace(0.0, np.nan)
        ).resample("1min").last()

    for window in (15, 30, 60):
        for stem in ("notional", "trade_count"):
            column = f"{stem}_{window}s"
            median = minute[column].shift(1).rolling(120, min_periods=60).median()
            minute[f"{stem}_burst_{window}s"] = minute[column] / median.replace(0.0, np.nan)

    prior_45_notional = minute["notional_60s"] - minute["notional_15s"]
    prior_45_signed = (
        minute["flow_60s"] * minute["notional_60s"]
        - minute["flow_15s"] * minute["notional_15s"]
    )
    minute["flow_accel_15_vs_prior45"] = (
        minute["flow_15s"] - prior_45_signed / prior_45_notional.replace(0.0, np.nan)
    )
    minute["flow_price_alignment_15s"] = np.sign(minute["flow_15s"]) * minute["ret_15s_bps"]
    minute["flow_price_alignment_60s"] = np.sign(minute["flow_60s"]) * minute["ret_60s_bps"]
    minute["absorption_15s"] = minute["flow_15s"].abs() * (1.0 - minute["eff_15s"].clip(0.0, 1.0))
    minute["absorption_60s"] = minute["flow_60s"].abs() * (1.0 - minute["eff_60s"].clip(0.0, 1.0))
    return minute


def aggregate_depth(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip")
    raw["ts"] = pd.to_datetime(raw["timestamp"], utc=True)
    depth = raw.pivot(index="ts", columns="percentage", values="notional").sort_index()
    minute = pd.DataFrame(index=depth.resample("1min").last().index)
    for band in (1, 2, 3, 4, 5):
        bid, ask = depth[-band], depth[band]
        total = bid + ask
        minute[f"depth_imb_{band}"] = ((bid - ask) / total.replace(0.0, np.nan)).resample("1min").last()
        minute[f"bid_depth_{band}"] = bid.resample("1min").last()
        minute[f"ask_depth_{band}"] = ask.resample("1min").last()
        for seconds in (30, 60, 300):
            lag = max(1, seconds // 5)
            minute[f"bid_chg_{band}_{seconds}s"] = (bid / bid.shift(lag) - 1.0).resample("1min").last()
            minute[f"ask_chg_{band}_{seconds}s"] = (ask / ask.shift(lag) - 1.0).resample("1min").last()
    return minute


def aggregate_metrics(path: Path | None) -> pd.DataFrame | None:
    if path is None:
        return None
    raw = pd.read_csv(path, compression="zip")
    raw.index = pd.to_datetime(raw["create_time"], utc=True)
    raw = raw.sort_index()
    columns = [column for column in raw.columns if column not in {"create_time", "symbol"}]
    for column in columns:
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    minute = raw[columns].add_prefix("metric_").resample("1min").ffill()
    for horizon in (5, 15, 60):
        minute[f"metric_oi_change_{horizon}m"] = minute["metric_sum_open_interest"].pct_change(
            horizon,
            fill_method=None,
        )
        minute[f"metric_oi_value_change_{horizon}m"] = minute["metric_sum_open_interest_value"].pct_change(
            horizon,
            fill_method=None,
        )
    return minute


def aggregate_reference(path: Path, prefix: str) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip")
    raw.index = pd.to_datetime(raw["open_time"], unit="ms", utc=True)
    result = raw[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    return result.add_prefix(prefix + "_")


def build_day(symbol: str, day: date, cache: Path) -> tuple[pd.DataFrame, list[Evidence]]:
    required: dict[str, Path] = {}
    evidence: list[Evidence] = []
    endpoints = (
        "aggTrades",
        "bookDepth",
        "markPriceKlines",
        "indexPriceKlines",
        "premiumIndexKlines",
    )
    for endpoint in endpoints:
        path, item = acquire(endpoint, symbol, day, cache, required=True)
        assert path is not None and item is not None
        required[endpoint] = path
        evidence.append(item)
    metrics_path, metrics_evidence = acquire("metrics", symbol, day, cache, required=False)
    if metrics_evidence is not None:
        evidence.append(metrics_evidence)

    features = aggregate_trades(required["aggTrades"], day)
    features = features.join(aggregate_depth(required["bookDepth"]), how="left")
    features = features.join(aggregate_reference(required["markPriceKlines"], "mark"), how="left")
    features = features.join(aggregate_reference(required["indexPriceKlines"], "index"), how="left")
    features = features.join(aggregate_reference(required["premiumIndexKlines"], "premium"), how="left")
    metrics = aggregate_metrics(metrics_path)
    if metrics is not None:
        features = features.join(metrics, how="left")
    features = features.ffill()
    features["trade_index_basis_bps"] = (features["trade_close"] / features["index_close"] - 1.0) * 10_000.0
    features["mark_index_basis_bps"] = (features["mark_close"] / features["index_close"] - 1.0) * 10_000.0
    for horizon in (1, 5, 15, 60):
        features[f"basis_change_{horizon}m"] = features["trade_index_basis_bps"].diff(horizon)
        features[f"premium_change_{horizon}m"] = features["premium_close"].diff(horizon)
    features["feature_complete"] = features.notna().all(axis=1)
    features.insert(0, "observed_time", (features.index + pd.Timedelta(minutes=1)).astype(str))
    features.insert(0, "open_time", features.index.astype(str))
    return features, evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    args.output.mkdir(parents=True, exist_ok=True)
    all_evidence: list[Evidence] = []
    current = start
    while current <= end:
        print(f"building {current}", flush=True)
        frame, evidence = build_day(args.symbol, current, args.cache)
        all_evidence.extend(evidence)
        frame.to_csv(
            args.output / f"{args.symbol}-rich-{current}.csv.gz",
            index=False,
            compression="gzip",
        )
        current += timedelta(days=1)
    manifest = {
        "symbol": args.symbol,
        "start": args.start,
        "end": args.end,
        "files": [asdict(item) for item in all_evidence],
    }
    (args.output / "data_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
