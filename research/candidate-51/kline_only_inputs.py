"""Checksum-verified Binance kline-only input adapter for price-only policies.

The existing Candidate 05 downloader and checksum verifier are reused.  The
kline parser is kept local because pandas 3 no longer reliably applies ``unit``
to numeric-looking string columns: passing Binance millisecond timestamps as
strings can be interpreted as calendar years and overflow.  Converting the two
timestamp columns to integers before ``to_datetime`` is an input-validity fix;
it does not alter any strategy rule, signal, fill, fee, risk, or accounting
policy.

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


def _timestamp_unit(values: pd.Series) -> str:
    """Return Binance archive timestamp unit after strict numeric conversion."""
    first = int(pd.to_numeric(values, errors="raise").iloc[0])
    return "us" if abs(first) > 10**14 else "ms"


def _read_kline(path: Path) -> pd.DataFrame:
    """Read one checked Binance 1-minute archive without pandas string-unit drift."""
    columns = list(_base.KLINE_COLUMNS)
    raw = pd.read_csv(path, compression="zip", header=None)
    if raw.shape[1] == len(columns):
        raw.columns = columns
        first = str(raw.iloc[0]["open_time"]).strip()
        if not first.lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    else:
        with_header = pd.read_csv(path, compression="zip")
        if not set(columns).issubset(with_header.columns):
            raise RuntimeError(f"unexpected kline schema in {path}: {list(with_header.columns)}")
        raw = with_header[columns].copy()

    numeric_columns = (
        "open_time",
        "close_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
    )
    for column in numeric_columns:
        raw[column] = pd.to_numeric(raw[column], errors="raise")

    open_values = raw["open_time"].astype("int64")
    close_values = raw["close_time"].astype("int64")
    raw["open_time_dt"] = pd.to_datetime(
        open_values,
        unit=_timestamp_unit(open_values),
        utc=True,
        errors="raise",
    )
    raw["close_time_dt"] = pd.to_datetime(
        close_values,
        unit=_timestamp_unit(close_values),
        utc=True,
        errors="raise",
    )
    frame = raw[
        [
            "open_time_dt",
            "close_time_dt",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
        ]
    ].copy()
    frame = frame.sort_values("close_time_dt")
    if frame["close_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate kline close times in {path}")
    if not frame["close_time_dt"].is_monotonic_increasing:
        raise RuntimeError(f"non-monotonic kline close times in {path}")
    return frame


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
        frames.append(_read_kline(archive))
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

    close_times = pd.DatetimeIndex(pd.to_datetime(klines["close_time_dt"], utc=True))
    expected_first = pd.Timestamp(start, tz="UTC") + pd.Timedelta(minutes=1) - pd.Timedelta(milliseconds=1)
    expected_last = pd.Timestamp(end + timedelta(days=1), tz="UTC") - pd.Timedelta(milliseconds=1)
    if close_times[0] != expected_first or close_times[-1] != expected_last:
        raise RuntimeError(
            f"unexpected kline clock for {symbol}: {close_times[0]}..{close_times[-1]} "
            f"expected {expected_first}..{expected_last}"
        )

    observed_time_ns = pd.Series(close_times.asi8, dtype="int64")
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
                "parser": "candidate51-strict-numeric-timestamp-pandas3-compatible",
                "symbol": symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "rows": len(klines),
                "first_close_time": close_times[0].isoformat(),
                "last_close_time": close_times[-1].isoformat(),
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
