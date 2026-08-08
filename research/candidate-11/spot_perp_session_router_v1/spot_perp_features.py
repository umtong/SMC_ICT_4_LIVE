"""Add causal Binance spot participation and spot/perpetual basis observations.

The existing Candidate 16 v4 loader continues to own USD-M bars, aggregate
trades, book depth, immutable L1 pressure, checksums, and feature timing.  This
module adds only same-minute spot aggregate-trade observations.  It never
matches orders or computes account PnL.
"""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import urllib.request

import numpy as np
import pandas as pd

from features import aggregate_agg_trades
from features_v4 import load_range as load_range_v4

SPOT_BASE = "https://data.binance.vision/data/spot/daily/aggTrades"
NS_PER_MINUTE = 60_000_000_000


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _download_spot_checked(symbol: str, day: date, cache: Path) -> tuple[Path, Path, dict[str, object]]:
    stamp = day.isoformat()
    filename = f"{symbol}-aggTrades-{stamp}.zip"
    url = f"{SPOT_BASE}/{symbol}/{filename}"
    directory = cache / "spot-aggTrades"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256_file(archive)
    if actual != expected:
        raise RuntimeError(f"spot checksum mismatch: {archive}: {actual} != {expected}")
    return archive, checksum, {
        "endpoint": "spot_aggTrades",
        "day": stamp,
        "archive": str(archive),
        "checksum": str(checksum),
        "size_bytes": archive.stat().st_size,
        "sha256": actual,
        "source_url": url,
        "role": "spot participation and price discovery only",
    }


def _merge_agg(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frame = pd.concat(frames).sort_index()
    if not frame.index.duplicated().any():
        return frame
    return frame.groupby(level=0, sort=True).agg(
        trade_open=("trade_open", "first"),
        trade_high=("trade_high", "max"),
        trade_low=("trade_low", "min"),
        trade_close=("trade_close", "last"),
        quantity_60s=("quantity_60s", "sum"),
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
        notional_open_10s=("notional_open_10s", "sum"),
        signed_notional_open_10s=("signed_notional_open_10s", "sum"),
        trade_count_open_10s=("trade_count_open_10s", "sum"),
    )


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
):
    if symbol != "BTCUSDT":
        raise ValueError("the frozen L1 and spot/perpetual study covers BTCUSDT")
    klines, feature_path, raw_files, evidence = load_range_v4(
        symbol=symbol,
        start=start,
        end=end,
        cache=cache,
        output=output,
    )

    spot_frames: list[pd.DataFrame] = []
    spot_evidence: list[dict[str, object]] = []
    day = start
    while day <= end:
        archive, checksum, item = _download_spot_checked(symbol, day, cache)
        raw_files.extend([archive, checksum])
        spot_frames.append(aggregate_agg_trades(archive))
        spot_evidence.append(item)
        day += timedelta(days=1)
    spot = _merge_agg(spot_frames)

    spot_feature = pd.DataFrame(index=spot.index)
    spot_feature["spot_trade_close"] = pd.to_numeric(spot["trade_close"], errors="raise")
    denominator = pd.to_numeric(spot["notional_60s"], errors="raise").replace(0.0, np.nan)
    spot_feature["spot_flow_60s"] = pd.to_numeric(
        spot["signed_notional_60s"],
        errors="raise",
    ) / denominator
    spot_feature["spot_ret_60s_bps"] = (
        np.log(
            pd.to_numeric(spot["trade_close"], errors="raise")
            / pd.to_numeric(spot["trade_open"], errors="raise")
        )
        * 10_000.0
    )
    # Binance spot archives can materialize a datetime64[us] index.  Integer
    # conversion without explicit normalization silently produces microseconds
    # and cannot join the nanosecond feature clock.
    spot_index_ns = spot_feature.index.astype("datetime64[ns, UTC]")
    spot_feature["minute_start_ns"] = spot_index_ns.astype("int64")
    spot_feature = spot_feature.reset_index(drop=True)

    futures = klines[["open_time_dt", "close"]].copy()
    futures["minute_start_ns"] = futures["open_time_dt"].astype("datetime64[ns, UTC]").astype("int64")
    futures["perp_trade_close"] = pd.to_numeric(futures["close"], errors="raise")
    futures = futures[["minute_start_ns", "perp_trade_close"]]

    base = pd.read_csv(feature_path, compression="infer")
    observed = pd.to_numeric(base["observed_time_ns"], errors="raise").astype("int64")
    base["minute_start_ns"] = observed // NS_PER_MINUTE * NS_PER_MINUTE
    merged = base.merge(
        spot_feature,
        on="minute_start_ns",
        how="left",
        validate="one_to_one",
        sort=False,
    ).merge(
        futures,
        on="minute_start_ns",
        how="left",
        validate="one_to_one",
        sort=False,
    )
    merged["basis_bps"] = np.log(
        merged["perp_trade_close"] / merged["spot_trade_close"],
    ) * 10_000.0
    merged["basis_change_bps"] = merged["basis_bps"].diff()
    merged["spot_perp_return_gap_bps"] = (
        pd.to_numeric(merged["ret_60s_bps"], errors="coerce")
        - merged["spot_ret_60s_bps"]
    )
    merged["spot_perp_feature_ready"] = merged[
        [
            "spot_trade_close",
            "spot_flow_60s",
            "spot_ret_60s_bps",
            "perp_trade_close",
            "basis_bps",
            "basis_change_bps",
        ]
    ].notna().all(axis=1)
    if int(merged["spot_trade_close"].notna().sum()) == 0:
        raise RuntimeError(
            "spot aggregate-trade clock did not join the nanosecond feature clock",
        )
    base_ready = merged["feature_ready"].astype(str).str.lower().isin({"true", "1", "yes"})
    merged["feature_ready"] = base_ready & merged["spot_perp_feature_ready"]
    if merged["observed_time_ns"].duplicated().any():
        raise RuntimeError("spot/perpetual join duplicated observations")
    merged.to_csv(feature_path, index=False, compression="gzip")

    raw_path = output / "raw_evidence.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw.extend(spot_evidence)
    raw_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return klines, feature_path, raw_files, evidence


__all__ = ["load_range"]
