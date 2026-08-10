"""Causally merge v66 direct minute features with original five-minute OI data.

Original feature timestamps identify five-minute feature bars. Their OI and
positioning values become available only after adding five minutes; a backward
as-of join then carries the last completed value into the direct minute matrix.
This is a data transformation only, never a fill or PnL simulator.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import argparse
import json

import numpy as np
import pandas as pd


def _load(root: Path) -> tuple[pd.DatetimeIndex, pd.DataFrame]:
    npz = next(root.rglob("v48_features.npz"))
    columns_path = next(root.rglob("columns.json"))
    columns = json.loads(columns_path.read_text(encoding="utf-8"))
    values = np.load(npz)
    timestamps = values["timestamps_ns"].astype(np.int64)
    median = int(np.median(timestamps))
    if median < 10_000_000_000_000_000:
        timestamps = timestamps * 1_000_000
    index = pd.to_datetime(timestamps, unit="ns", utc=True)
    frame = pd.DataFrame(values["values"], index=index, columns=columns)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frame.index, frame


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct-root", type=Path, required=True)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    _, direct = _load(args.direct_root)
    _, original = _load(args.original_root)
    required_original = ["oi_btc", "oi_change_1h", "ls_count_ratio", "taker_ls_vol_ratio"]
    missing = [name for name in required_original if name not in original]
    if missing:
        raise ValueError(f"original OI fields missing: {missing}")
    available = original[required_original].copy()
    available.index = available.index + pd.Timedelta(minutes=5)
    available.columns = [f"original_{name}" for name in available.columns]

    left = direct.reset_index(names="timestamp")
    right = available.reset_index(names="timestamp")
    merged = pd.merge_asof(
        left.sort_values("timestamp"),
        right.sort_values("timestamp"),
        on="timestamp",
        direction="backward",
        allow_exact_matches=True,
    ).set_index("timestamp")
    merged["oi_btc"] = merged.pop("original_oi_btc")
    merged["oi_change_1h"] = merged.pop("original_oi_change_1h")
    merged["ls_count_ratio"] = merged.pop("original_ls_count_ratio")
    merged["taker_ls_vol_ratio"] = merged.pop("original_taker_ls_vol_ratio")
    merged = merged.replace([np.inf, -np.inf], np.nan)
    critical = [
        "close",
        "aggressive_signed_quote_1m",
        "aggressive_total_quote_1m",
        "signed_flow_ratio_1m",
        "ask_depth_1pct_end",
        "bid_depth_1pct_end",
        "oi_btc",
        "oi_change_1h",
    ]
    if merged[critical].isna().any().any():
        rows = int(merged[critical].isna().any(axis=1).sum())
        raise ValueError(f"causal merged feature gaps: {rows}")
    if float(merged["oi_change_1h"].abs().max()) <= 0:
        raise ValueError("OI change remained zero after merge")

    args.output_root.mkdir(parents=True, exist_ok=True)
    columns = [str(column) for column in merged.columns]
    timestamps_ns = merged.index.astype("int64").to_numpy(dtype=np.int64)
    values = merged.to_numpy(dtype=np.float64, copy=True)
    npz_path = args.output_root / "v48_features.npz"
    np.savez_compressed(npz_path, timestamps_ns=timestamps_ns, values=values)
    (args.output_root / "columns.json").write_text(
        json.dumps(columns, indent=2), encoding="utf-8"
    )
    manifest = {
        "rows": len(merged),
        "first_available_utc": merged.index[0].isoformat(),
        "last_available_utc": merged.index[-1].isoformat(),
        "columns": columns,
        "oi_availability_rule": "original five-minute timestamp plus five minutes, then backward as-of join",
        "npz_sha256": sha256(npz_path.read_bytes()).hexdigest(),
        "npz_size": npz_path.stat().st_size,
        "oi_change_min": float(merged["oi_change_1h"].min()),
        "oi_change_max": float(merged["oi_change_1h"].max()),
    }
    (args.output_root / "v73_merge_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
