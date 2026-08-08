"""Candidate 16 v4 feature extension using an immutable L1 Parquet.

Candidate 05 continues to own completed bars, aggregate-trade features, raw
Binance checksums, and the NautilusTrader catalog. This module only downloads,
verifies, and joins an existing one-minute bookTicker feature artifact. It does
not build a new data framework or simulate execution.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import shutil
import time
from typing import Any
import urllib.error
import urllib.request

import pandas as pd

import features as candidate05_features


DATASET_COMMIT = "2c8dce40261855c7b57113f5a157bbeb82280bb8"
DATASET_SHA256 = "274eb8e87c7d7185a0162271144b30a0e387ae496fe657c6af83833448f08624"
DATASET_SIZE = 28_423_067
DATASET_ROWS = 460_265
DATASET_URL = (
    "https://huggingface.co/datasets/Mindbyte-89/"
    "btcusdt_perp_bookticker_features_1m_05_2023_to_03_2024/resolve/"
    f"{DATASET_COMMIT}/data/train-00000-of-00001.parquet?download=true"
)
NS_PER_MINUTE = 60_000_000_000
L1_COLUMNS = (
    "timestamp",
    "bt_spread_bps_close",
    "bt_spread_bps_twap",
    "bt_bid_qty_close",
    "bt_ask_qty_close",
    "bt_imbalance_close",
    "bt_imbalance_twap",
    "bt_microprice_close",
    "bt_microprice_premium_close",
    "bt_update_rate",
)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _download_verified(destination: Path, attempts: int = 5) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if (
            destination.stat().st_size == DATASET_SIZE
            and _sha256_file(destination) == DATASET_SHA256
        ):
            return destination
        destination.unlink()

    temporary = destination.with_suffix(".parquet.tmp")
    last_error: Exception | None = None
    for attempt in range(attempts):
        temporary.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(
                DATASET_URL,
                headers={"User-Agent": "SMC-ICT-4-research"},
            )
            with (
                urllib.request.urlopen(request, timeout=180) as response,
                temporary.open("wb") as target,
            ):
                shutil.copyfileobj(response, target, length=1024 * 1024)
            if temporary.stat().st_size != DATASET_SIZE:
                raise RuntimeError(
                    "L1 dataset size mismatch: "
                    f"{temporary.stat().st_size} != {DATASET_SIZE}",
                )
            actual = _sha256_file(temporary)
            if actual != DATASET_SHA256:
                raise RuntimeError(
                    f"L1 dataset checksum mismatch: {actual}",
                )
            temporary.replace(destination)
            return destination
        except (
            OSError,
            TimeoutError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            RuntimeError,
        ) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError("failed to obtain immutable L1 dataset") from last_error


def _load_l1(path: Path, start: date, end: date) -> pd.DataFrame:
    frame = pd.read_parquet(path, columns=list(L1_COLUMNS))
    if tuple(frame.columns) != L1_COLUMNS:
        raise RuntimeError(f"unexpected L1 columns: {list(frame.columns)}")
    if len(frame.index) != DATASET_ROWS:
        raise RuntimeError(
            f"unexpected L1 row count: {len(frame.index)} != {DATASET_ROWS}",
        )
    timestamp = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    if timestamp.duplicated().any() or not timestamp.is_monotonic_increasing:
        order = timestamp.argsort(kind="stable")
        frame = frame.iloc[order].reset_index(drop=True)
        timestamp = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    if timestamp.duplicated().any() or not timestamp.is_monotonic_increasing:
        raise RuntimeError("L1 timestamps are duplicated or non-monotonic")
    frame["minute_start_ns"] = timestamp.astype("int64")

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    selected = frame[(timestamp >= start_ts) & (timestamp < end_ts)].copy()
    if selected.empty:
        raise RuntimeError("no L1 pressure rows in requested build interval")
    numeric = [column for column in L1_COLUMNS if column != "timestamp"]
    for column in numeric:
        selected[column] = pd.to_numeric(selected[column], errors="raise")
    required = (
        "bt_spread_bps_close",
        "bt_spread_bps_twap",
        "bt_imbalance_close",
        "bt_imbalance_twap",
        "bt_microprice_premium_close",
        "bt_update_rate",
    )
    selected["l1_pressure_feature_ready"] = (
        selected[list(required)].notna().all(axis=1)
        & selected["bt_spread_bps_close"].gt(0.0)
        & selected["bt_spread_bps_twap"].gt(0.0)
        & selected["bt_update_rate"].gt(0.0)
    )
    return selected.drop(columns=["timestamp"])


def _as_bool(values: pd.Series) -> pd.Series:
    if values.dtype == bool:
        return values
    return values.astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes"},
    )


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> tuple[pd.DataFrame, Path, list[Path], list[Any]]:
    if symbol != "BTCUSDT":
        raise ValueError("the frozen L1 dataset covers BTCUSDT only")
    klines, feature_path, raw_files, evidence = candidate05_features.load_range(
        symbol=symbol,
        start=start,
        end=end,
        cache=cache,
        output=output,
    )
    dataset_path = _download_verified(
        cache / "external" / "btcusdt_bookticker_l1_1m.parquet",
    )
    l1 = _load_l1(dataset_path, start, end)

    base = pd.read_csv(feature_path, compression="infer")
    if "observed_time_ns" not in base or "feature_ready" not in base:
        raise RuntimeError("Candidate 05 feature contract drifted")
    observed_ns = pd.to_numeric(
        base["observed_time_ns"],
        errors="raise",
    ).astype("int64")
    base["minute_start_ns"] = (
        observed_ns // NS_PER_MINUTE * NS_PER_MINUTE
    )
    merged = base.merge(
        l1,
        on="minute_start_ns",
        how="left",
        validate="one_to_one",
        sort=False,
    )
    l1_ready = _as_bool(
        merged["l1_pressure_feature_ready"].fillna(False),
    )
    merged["feature_ready"] = _as_bool(merged["feature_ready"]) & l1_ready
    if merged["observed_time_ns"].duplicated().any():
        raise RuntimeError("L1 join duplicated feature observations")
    merged.to_csv(feature_path, index=False, compression="gzip")

    raw_files.append(dataset_path)
    raw_evidence_path = output / "raw_evidence.json"
    raw_evidence = json.loads(raw_evidence_path.read_text(encoding="utf-8"))
    raw_evidence.append(
        {
            "endpoint": "immutable_external_bookticker_1m",
            "dataset": (
                "Mindbyte-89/"
                "btcusdt_perp_bookticker_features_1m_05_2023_to_03_2024"
            ),
            "dataset_commit": DATASET_COMMIT,
            "source_url": DATASET_URL,
            "local_path": str(dataset_path),
            "sha256": DATASET_SHA256,
            "size_bytes": DATASET_SIZE,
            "rows": DATASET_ROWS,
            "coverage": "2023-05-16 11:49 through 2024-03-31 23:59 UTC",
            "cadence": "one completed minute",
            "license": "MIT dataset card; upstream data Binance",
            "role": (
                "observational L1 pressure only; not matching, fills, "
                "portfolio, PnL, or NAV"
            ),
        },
    )
    raw_evidence_path.write_text(
        json.dumps(raw_evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return klines, feature_path, raw_files, evidence


__all__ = [
    "DATASET_COMMIT",
    "DATASET_ROWS",
    "DATASET_SHA256",
    "DATASET_SIZE",
    "DATASET_URL",
    "L1_COLUMNS",
    "load_range",
]
