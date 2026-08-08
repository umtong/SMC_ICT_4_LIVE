"""Checksum-verified multi-symbol chunk assembly for one continuous account."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class ChunkAssemblyError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def expected_grid(start: date, end: date) -> pd.DatetimeIndex:
    return pd.date_range(
        pd.Timestamp(start, tz="UTC"),
        pd.Timestamp(end + timedelta(days=1), tz="UTC") - pd.Timedelta(minutes=1),
        freq="1min",
    )


def _candidate_manifests(input_root: Path, symbol: str) -> list[tuple[dict[str, Any], Path]]:
    records: list[tuple[dict[str, Any], Path]] = []
    for path in sorted(input_root.rglob("chunk_manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("symbol") == symbol:
            records.append((manifest, path.parent))
    return sorted(records, key=lambda item: (item[0]["core_start"], item[0]["core_end"]))


def _validate_file(directory: Path, manifest: dict[str, Any], name: str) -> Path:
    path = directory / name
    if not path.is_file():
        raise ChunkAssemblyError(f"missing chunk file: {path}")
    expected = str(manifest["files"][name]["sha256"])
    actual = sha256_file(path)
    if actual != expected:
        raise ChunkAssemblyError(f"chunk hash mismatch for {path}: {actual} != {expected}")
    return path


def assemble_symbol(
    *,
    input_root: Path,
    symbol: str,
    start: date,
    end: date,
    workspace: Path,
) -> tuple[pd.DataFrame, Path, dict[str, Any]]:
    records = _candidate_manifests(input_root, symbol)
    if not records:
        raise ChunkAssemblyError(f"no chunk manifests for {symbol} under {input_root}")
    cursor = start
    kline_frames: list[pd.DataFrame] = []
    feature_frames: list[pd.DataFrame] = []
    chunks: list[dict[str, Any]] = []
    for manifest, directory in records:
        core_start = date.fromisoformat(str(manifest["core_start"]))
        core_end = date.fromisoformat(str(manifest["core_end"]))
        if core_end < start or core_start > end:
            continue
        if core_start != cursor:
            raise ChunkAssemblyError(
                f"{symbol} chunk boundary expected {cursor}, got {core_start} in {directory}",
            )
        if core_end > end:
            raise ChunkAssemblyError(f"{symbol} chunk exceeds requested end: {core_end} > {end}")
        kline_path = _validate_file(directory, manifest, "klines.csv.gz")
        feature_path = _validate_file(directory, manifest, "features.csv.gz")
        klines = pd.read_csv(kline_path, compression="infer")
        klines["open_time_dt"] = pd.to_datetime(klines["open_time_dt"], utc=True, errors="raise")
        klines["close_time_dt"] = pd.to_datetime(klines["close_time_dt"], utc=True, errors="raise")
        features = pd.read_csv(feature_path, compression="infer")
        features["observed_time_ns"] = pd.to_numeric(
            features["observed_time_ns"], errors="raise"
        ).astype("int64")
        if int(manifest["rows"]) != len(klines) or len(klines) != len(features):
            raise ChunkAssemblyError(
                f"{symbol} manifest rows differ in {directory}: "
                f"manifest={manifest['rows']} klines={len(klines)} features={len(features)}",
            )
        kline_frames.append(klines)
        feature_frames.append(features)
        chunks.append(
            {
                "core_start": core_start.isoformat(),
                "core_end": core_end.isoformat(),
                "rows": len(klines),
                "klines_sha256": manifest["files"]["klines.csv.gz"]["sha256"],
                "features_sha256": manifest["files"]["features.csv.gz"]["sha256"],
            },
        )
        cursor = core_end + timedelta(days=1)
    if cursor != end + timedelta(days=1):
        raise ChunkAssemblyError(
            f"{symbol} coverage ended {cursor - timedelta(days=1)}, expected {end}",
        )

    klines = pd.concat(kline_frames, ignore_index=True).sort_values("close_time_dt").reset_index(drop=True)
    features = pd.concat(feature_frames, ignore_index=True).sort_values("observed_time_ns").reset_index(drop=True)
    expected = expected_grid(start, end)
    actual = pd.DatetimeIndex(klines["close_time_dt"].dt.floor("min"))
    observed = pd.DatetimeIndex(
        pd.to_datetime(features["observed_time_ns"], unit="ns", utc=True).dt.floor("min")
    )
    if actual.has_duplicates or observed.has_duplicates:
        raise ChunkAssemblyError(f"{symbol} has duplicate assembled timestamps")
    if not actual.equals(expected) or not observed.equals(expected):
        raise ChunkAssemblyError(
            f"{symbol} assembled minute grid differs: "
            f"kline={len(actual)} feature={len(observed)} expected={len(expected)}",
        )
    kline_ns = np.fromiter(
        (pd.Timestamp(value).value for value in klines["close_time_dt"]),
        dtype=np.int64,
        count=len(klines),
    )
    feature_ns = features["observed_time_ns"].to_numpy(dtype=np.int64, copy=False)
    if not np.array_equal(kline_ns, feature_ns):
        mismatch = np.flatnonzero(kline_ns != feature_ns)[:5]
        raise ChunkAssemblyError(f"{symbol} feature/kline timestamps differ at {mismatch.tolist()}")

    destination = workspace / symbol
    destination.mkdir(parents=True, exist_ok=True)
    combined_features = destination / "features.csv.gz"
    combined_klines = destination / "klines.csv.gz"
    features.to_csv(combined_features, index=False, compression="gzip")
    klines.to_csv(combined_klines, index=False, compression="gzip")
    manifest = {
        "symbol": symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_days": (end - start).days + 1,
        "minute_rows": len(klines),
        "chunk_count": len(chunks),
        "account_restarts": 0,
        "strategy_restarts": 0,
        "assembled_klines_sha256": sha256_file(combined_klines),
        "assembled_features_sha256": sha256_file(combined_features),
        "chunks": chunks,
    }
    return klines, combined_features, manifest


def assemble_universe(
    *,
    input_root: Path,
    start: date,
    end: date,
    workspace: Path,
    symbols: Iterable[str] = SYMBOLS,
) -> tuple[dict[str, pd.DataFrame], dict[str, Path], dict[str, Any]]:
    frames: dict[str, pd.DataFrame] = {}
    feature_paths: dict[str, Path] = {}
    symbol_manifests: dict[str, Any] = {}
    reference: pd.DatetimeIndex | None = None
    for symbol in tuple(symbols):
        frame, feature_path, manifest = assemble_symbol(
            input_root=input_root,
            symbol=symbol,
            start=start,
            end=end,
            workspace=workspace,
        )
        clock = pd.DatetimeIndex(frame["close_time_dt"])
        if reference is None:
            reference = clock
        elif not clock.equals(reference):
            raise ChunkAssemblyError(f"cross-symbol clock differs for {symbol}")
        frames[symbol] = frame
        feature_paths[symbol] = feature_path
        symbol_manifests[symbol] = manifest
    universe_manifest = {
        "schema_version": 1,
        "candidate": "candidate-35-clock-phase-auction-router",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_days": (end - start).days + 1,
        "minute_rows_per_symbol": len(reference) if reference is not None else 0,
        "symbols": symbol_manifests,
        "single_continuous_account": True,
        "single_strategy_process": True,
        "account_restarts": 0,
        "strategy_restarts": 0,
    }
    return frames, feature_paths, universe_manifest
