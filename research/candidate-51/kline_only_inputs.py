"""Checksum-verified Binance kline-only input adapter for price-only policies.

The existing Candidate 05 downloader, checksum verifier and parser are reused.
Unused aggTrades, bookDepth, positioning and basis files are intentionally not
loaded.  A minimal causal feature clock is emitted because the shared execution
shell requires a feature path even when a policy never reads feature values.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE05_FEATURES = HERE.parent / "candidate-05" / "features.py"
_spec = importlib.util.spec_from_file_location(
    "candidate51_reused_candidate05_features",
    CANDIDATE05_FEATURES,
)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load Candidate 05 feature verifier: {CANDIDATE05_FEATURES}")
_base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _base
_spec.loader.exec_module(_base)


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> tuple[pd.DataFrame, Path, list[Path], list[Any]]:
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
