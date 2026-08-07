#!/usr/bin/env python3
"""Download BTC spot minute bars and causally align them to 5-minute features."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import io
import json
from pathlib import Path
import urllib.request
import zipfile

import numpy as np
import pandas as pd

from v53_nt_core import load_feature_matrix

COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]


def fetch(day: date, root: Path) -> Path:
    name = f"BTCUSDT-1m-{day.isoformat()}.zip"
    url = f"https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/{name}"
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    req = urllib.request.Request(url, headers={"User-Agent": "candidate-02-v108/1.0"})
    tmp = path.with_suffix(".tmp")
    with urllib.request.urlopen(req, timeout=180) as response, tmp.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    with zipfile.ZipFile(tmp) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"invalid spot kline archive {path}: {members}")
    tmp.replace(path)
    return path


def read(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        name = [name for name in archive.namelist() if not name.endswith("/")][0]
        raw = archive.read(name)
    frame = pd.read_csv(io.BytesIO(raw), header=None)
    if str(frame.iloc[0, 0]).lower().startswith("open"):
        frame = frame.iloc[1:]
    frame = frame.iloc[:, :12]
    frame.columns = COLS
    for column in ("open_time", "open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame.dropna(subset=["open_time", "close"], inplace=True)
    unit = "us" if float(frame["open_time"].median()) >= 1e14 else "ms"
    frame.index = (
        pd.to_datetime(frame["open_time"].astype("int64"), unit=unit, utc=True)
        + pd.Timedelta(minutes=1)
    )
    return frame[["open", "high", "low", "close", "volume"]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--start", required=True)
    args = parser.parse_args()

    npz = next(args.input_root.rglob("v48_features.npz"))
    columns = npz.with_name("columns.json")
    features = load_feature_matrix(npz, columns)

    start_day = date.fromisoformat(args.start)
    days = [start_day - timedelta(days=2) + timedelta(days=i) for i in range(10)]
    spot_root = args.input_root / "spot_klines"
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda day: fetch(day, spot_root), days))
    spot = pd.concat([read(path) for path in sorted(paths)]).sort_index()
    if spot.index.has_duplicates:
        raise ValueError("duplicate spot minute closes")

    # A feature row indexed t contains the completed futures bar ending at t+5m.
    # Only the locked evaluation neighbourhood needs spot observations; the six-
    # month futures feature matrix remains intact so prior-only quantiles retain
    # their audited history. Missing spot values outside that neighbourhood are
    # intentionally allowed and can never create a v108 signal.
    availability = features.index + pd.Timedelta(minutes=5)
    aligned = spot["close"].reindex(availability)
    aligned.index = features.index

    evaluation_start = pd.Timestamp(start_day, tz="UTC")
    evaluation_end = evaluation_start + pd.Timedelta(days=7)
    required_feature_start = evaluation_start - pd.Timedelta(minutes=10)
    required_feature_end = evaluation_end
    required_mask = (
        (features.index >= required_feature_start)
        & (features.index <= required_feature_end)
    )
    missing_required = int(aligned.loc[required_mask].isna().sum())
    if missing_required:
        raise ValueError(
            "missing aligned spot closes inside locked evaluation neighbourhood: "
            f"{missing_required}"
        )

    features["spot_close_at_feature_availability"] = aligned.astype(float)
    features["spot_log_ret_5m"] = np.log(
        features["spot_close_at_feature_availability"]
        / features["spot_close_at_feature_availability"].shift(1)
    )
    features["perp_spot_log_basis"] = np.log(
        features["close"] / features["spot_close_at_feature_availability"]
    )
    features["perp_spot_basis_change_5m"] = features["perp_spot_log_basis"].diff()

    critical = ["spot_log_ret_5m", "perp_spot_log_basis", "perp_spot_basis_change_5m"]
    critical_missing = features.loc[required_mask, critical].isna().sum()
    if int(critical_missing.sum()):
        raise ValueError(
            "critical v108 cross-market gaps inside locked evaluation neighbourhood: "
            f"{critical_missing.to_dict()}"
        )

    values = features.to_numpy(dtype=np.float64, copy=True)
    # Preserve the original timestamp storage unit used by the audited loader.
    original = np.load(npz)["timestamps_ns"]
    tmp = npz.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, timestamps_ns=original, values=values)
    tmp.replace(npz)
    columns.write_text(
        json.dumps([str(column) for column in features.columns], indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "rows": len(features),
                "spot_first": spot.index.min().isoformat(),
                "spot_last": spot.index.max().isoformat(),
                "required_feature_first": features.index[required_mask].min().isoformat(),
                "required_feature_last": features.index[required_mask].max().isoformat(),
                "required_rows": int(required_mask.sum()),
                "missing_spot_outside_required_rows": int(aligned.loc[~required_mask].isna().sum()),
                "alignment": "feature open t uses spot close at t+5m only",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
