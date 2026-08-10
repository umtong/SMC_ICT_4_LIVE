"""Export the published BTCUSDT feature parquet files to a portable NPZ matrix.

All original columns are retained by name.  Downstream evaluation code must
explicitly exclude every forward target/label column from live inputs.  The NPZ
format permits deterministic local analysis without requiring a parquet engine.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import json

import numpy as np
import pandas as pd
from huggingface_hub import snapshot_download

REPO_ID = "ibrahimdaud/binance-btcusdt"
REVISION = "main"
ROOT = Path(".cache/candidate-02/hf-btc-features-portable")
OUT = Path("artifacts/candidate-02-hf-portable")


def main() -> None:
    local = Path(snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        revision=REVISION,
        allow_patterns=["features/BTCUSDT/*.parquet", "README.md"],
        local_dir=ROOT,
    ))
    parquet_files = sorted((local / "features" / "BTCUSDT").glob("*.parquet"))
    if not parquet_files:
        raise RuntimeError("no BTCUSDT parquet files downloaded")

    frames = []
    for path in parquet_files:
        frame = pd.read_parquet(path)
        frame.columns = [str(column) for column in frame.columns]
        frames.append(frame)
        print(path.name, len(frame), flush=True)
    data = pd.concat(frames, axis=0, ignore_index=False)

    if isinstance(data.index, pd.DatetimeIndex):
        timestamps = data.index.tz_convert("UTC").asi8 if data.index.tz is not None else data.index.tz_localize("UTC").asi8
    else:
        timestamp_column = next((column for column in ("timestamp", "open_time", "datetime", "date") if column in data.columns), None)
        if timestamp_column is None:
            raise RuntimeError(f"no timestamp found; index={type(data.index)}, columns={list(data.columns)}")
        parsed = pd.to_datetime(data.pop(timestamp_column), utc=True, errors="raise")
        timestamps = parsed.astype("int64").to_numpy()

    data = data.replace([np.inf, -np.inf], np.nan)
    non_numeric = [column for column in data.columns if not pd.api.types.is_numeric_dtype(data[column])]
    for column in non_numeric:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    columns = list(data.columns)
    values = data.to_numpy(dtype=np.float64, copy=True)
    order = np.argsort(timestamps, kind="stable")
    timestamps = np.asarray(timestamps, dtype=np.int64)[order]
    values = values[order]
    if np.any(timestamps[1:] <= timestamps[:-1]):
        raise RuntimeError("timestamps are not strictly increasing after concatenation")

    OUT.mkdir(parents=True, exist_ok=True)
    npz_path = OUT / "BTCUSDT_features_full.npz"
    np.savez_compressed(npz_path, timestamps_ns=timestamps, values=values)
    (OUT / "columns.json").write_text(json.dumps(columns, indent=2), encoding="utf-8")
    schema = {
        "repo_id": REPO_ID,
        "revision": REVISION,
        "rows": int(values.shape[0]),
        "columns": int(values.shape[1]),
        "start_utc": pd.Timestamp(timestamps[0], unit="ns", tz="UTC").isoformat(),
        "end_utc": pd.Timestamp(timestamps[-1], unit="ns", tz="UTC").isoformat(),
        "npz_size": npz_path.stat().st_size,
        "npz_sha256": sha256(npz_path.read_bytes()).hexdigest(),
        "columns_sha256": sha256((OUT / "columns.json").read_bytes()).hexdigest(),
        "non_numeric_coerced": non_numeric,
    }
    (OUT / "schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(json.dumps(schema, indent=2))


if __name__ == "__main__":
    main()
