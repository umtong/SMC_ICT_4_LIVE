"""Collect the prospectively locked v48 training and first-test data.

The v48 architecture and 2025-03-03 first BTC week were committed before this
collector.  Historical feature labels are preserved for prior-only training;
the downstream evaluator must mask every test-week forward label.
"""
from __future__ import annotations
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import json, time, urllib.request, zipfile

import numpy as np
import pandas as pd
from huggingface_hub import hf_hub_download

DATASET = "ibrahimdaud/binance-btcusdt"
REVISION = "main"
FEATURE_START = date(2024, 9, 1)
FEATURE_END = date(2025, 3, 10)
TEST_WEEK = "2025-03-03"
RAW_START = date(2025, 3, 1)
RAW_END = date(2025, 3, 10)
ROOT = Path(".cache/candidate-02/v48-first-week")
OUT = Path("artifacts/candidate-02-v48-first-week")
BINANCE = "https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m"


def fetch_binance(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    last = None
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "candidate-02-research/1.0"})
            with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as stream:
                while chunk := response.read(1 << 20):
                    stream.write(chunk)
            with zipfile.ZipFile(tmp) as archive:
                members = [name for name in archive.namelist() if not name.endswith("/")]
                if len(members) != 1 or archive.getinfo(members[0]).file_size <= 0:
                    raise RuntimeError(f"invalid archive: {url}")
            tmp.replace(path)
            return
        except Exception as exc:
            last = exc
            tmp.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(min(20, 2**attempt))
    raise RuntimeError(f"failed {url}: {last}")


def main() -> None:
    feature_frames = []
    feature_files = []
    day = FEATURE_START
    while day <= FEATURE_END:
        relative = f"features/BTCUSDT/{day.isoformat()}.parquet"
        downloaded = Path(hf_hub_download(
            repo_id=DATASET,
            repo_type="dataset",
            revision=REVISION,
            filename=relative,
            local_dir=ROOT / "hf",
        ))
        frame = pd.read_parquet(downloaded)
        frame.columns = [str(column) for column in frame.columns]
        feature_frames.append(frame)
        feature_files.append({
            "date": day.isoformat(),
            "path": str(downloaded),
            "size": downloaded.stat().st_size,
            "sha256": sha256(downloaded.read_bytes()).hexdigest(),
        })
        print("feature", day.isoformat(), len(frame), flush=True)
        day += timedelta(days=1)

    data = pd.concat(feature_frames, ignore_index=True)
    if "bar_time_ms" not in data.columns:
        raise RuntimeError(f"bar_time_ms missing: {list(data.columns)}")
    timestamps_ns = pd.to_datetime(data.pop("bar_time_ms"), unit="ms", utc=True).astype("int64").to_numpy(dtype=np.int64)
    if "symbol" in data.columns:
        symbols = data.pop("symbol").astype(str)
        if set(symbols.dropna().unique()) != {"BTCUSDT"}:
            raise RuntimeError("unexpected symbols in feature set")
    data = data.replace([np.inf, -np.inf], np.nan)
    columns = [str(column) for column in data.columns]
    values = data.to_numpy(dtype=np.float64, copy=True)
    order = np.argsort(timestamps_ns, kind="stable")
    timestamps_ns = timestamps_ns[order]
    values = values[order]
    if np.any(timestamps_ns[1:] <= timestamps_ns[:-1]):
        raise RuntimeError("feature timestamps are not strictly increasing")

    OUT.mkdir(parents=True, exist_ok=True)
    npz = OUT / "v48_features.npz"
    np.savez_compressed(npz, timestamps_ns=timestamps_ns, values=values)
    (OUT / "columns.json").write_text(json.dumps(columns, indent=2), encoding="utf-8")

    raw_files = []
    day = RAW_START
    while day <= RAW_END:
        ds = day.isoformat()
        name = f"BTCUSDT-1m-{ds}.zip"
        url = f"{BINANCE}/{name}"
        path = ROOT / "binance_1m" / name
        fetch_binance(url, path)
        raw_files.append({"date": ds, "path": str(path), "size": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest(), "url": url})
        print("raw", ds, flush=True)
        day += timedelta(days=1)

    test_start_ns = pd.Timestamp(TEST_WEEK, tz="UTC").value
    test_end_ns = (pd.Timestamp(TEST_WEEK, tz="UTC") + pd.Timedelta(days=7)).value
    schema = {
        "candidate": "adaptive_price_discovery_or_toxic_exhaustion_v48",
        "test_week": TEST_WEEK,
        "feature_rows": int(values.shape[0]),
        "feature_columns": columns,
        "feature_start_utc": pd.Timestamp(timestamps_ns[0], unit="ns", tz="UTC").isoformat(),
        "feature_end_utc": pd.Timestamp(timestamps_ns[-1], unit="ns", tz="UTC").isoformat(),
        "test_rows": int(((timestamps_ns >= test_start_ns) & (timestamps_ns < test_end_ns)).sum()),
        "npz_size": npz.stat().st_size,
        "npz_sha256": sha256(npz.read_bytes()).hexdigest(),
        "feature_files": feature_files,
        "raw_files": raw_files,
    }
    (OUT / "manifest.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
    (OUT / "STATUS_READY_FOR_LOCKED_FIRST_WEEK_EVALUATION").write_text(TEST_WEEK + "\n", encoding="utf-8")
    print(json.dumps({key: schema[key] for key in ("candidate", "test_week", "feature_rows", "test_rows", "npz_size")}, indent=2))


if __name__ == "__main__":
    main()
