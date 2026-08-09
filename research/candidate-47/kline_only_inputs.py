"""Checksum-verified Binance kline-only input adapter for price-only policies.

Candidate 05's public-data functions are reused for archive naming, download,
checksum verification and schema parsing.  This adapter intentionally omits
aggTrades, bookDepth, positioning and basis archives when a strategy consumes
only completed OHLCV bars.  It creates the minimal causal feature file required
by the reused Candidate 35 StrategyConfig; price-only strategies never read a
feature value from that file.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import pandas as pd

import features as _base


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> tuple[pd.DataFrame, Path, list[Path], list[Any]]:
    """Download and verify only one-minute klines for a contiguous range."""
    if end < start:
        raise ValueError("end precedes start")
    output.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    manifest_files: list[Path] = []
    evidence: list[Any] = []
    day = start
    while day <= end:
        archive, checksum, item = _base.download_checked(
            "klines",
            symbol,
            day,
            cache,
        )
        frames.append(_base.read_kline(archive))
        manifest_files.extend([archive, checksum])
        evidence.append(item)
        day += timedelta(days=1)

    klines = pd.concat(frames, ignore_index=True).sort_values("close_time_dt")
    if klines["close_time_dt"].duplicated().any():
        raise RuntimeError("duplicate klines across daily files")
    expected_days = (end - start).days + 1
    expected_rows = expected_days * 1_440
    if len(klines) != expected_rows:
        raise RuntimeError(
            f"incomplete minute data: {len(klines)} rows for {expected_days} days; "
            f"expected {expected_rows}"
        )

    close_times = pd.to_datetime(klines["close_time_dt"], utc=True)
    observed_time_ns = close_times.astype("int64")
    if observed_time_ns.duplicated().any() or not observed_time_ns.is_monotonic_increasing:
        raise RuntimeError("kline observation timestamps must be unique and monotonic")

    feature_path = output / "features.csv.gz"
    pd.DataFrame(
        {
            "observed_time_ns": observed_time_ns,
            "feature_ready": True,
        }
    ).to_csv(feature_path, index=False, compression="gzip")
    (output / "raw_evidence.json").write_text(
        json.dumps([asdict(item) for item in evidence], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "input_mode.json").write_text(
        json.dumps(
            {
                "mode": "checksum-verified-binance-kline-only",
                "symbol": symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "rows": len(klines),
                "consumed_endpoints": ["klines"],
                "omitted_unused_endpoints": [
                    "aggTrades",
                    "bookDepth",
                    "metrics",
                    "premiumIndexKlines",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return klines, feature_path, manifest_files, evidence


__all__ = ["load_range"]
