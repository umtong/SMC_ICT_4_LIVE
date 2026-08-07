#!/usr/bin/env python3
"""Replay V56 through ``nt_backtest`` using an existing Nautilus catalog.

This is an implementation adapter, not a backtest engine.  The exact trusted
``nt_backtest.py`` run path still constructs the BacktestNode, venue, fill,
latency and fee models, strategy, orders, reports, PnL and NAV.  The adapter
changes only data acquisition: instead of downloading the same Binance minute
archives again, it links the immutable ParquetDataCatalog already produced by a
prior official NautilusTrader run for the identical build interval.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Any

import pandas as pd

import nt_backtest as base
import nt_backtest_v56_prominence_state  # noqa: F401  (installs strategy/risk adapter)


def _source_catalog() -> Path:
    raw = os.environ.get("C04_SOURCE_CATALOG")
    if not raw:
        raise RuntimeError("C04_SOURCE_CATALOG is required")
    path = Path(raw).resolve()
    if not path.is_dir():
        raise RuntimeError(f"source Nautilus catalog does not exist: {path}")
    bars = list(path.glob("data/bar/BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL/*.parquet"))
    instruments = list(path.glob("data/crypto_perpetual/BTCUSDT-PERP.BINANCE/*.parquet"))
    if len(bars) != 1 or len(instruments) != 1:
        raise RuntimeError(
            f"invalid source catalog: bars={len(bars)} instruments={len(instruments)}"
        )
    return path


def _source_manifest() -> Path:
    raw = os.environ.get("C04_SOURCE_DATA_MANIFEST")
    if not raw:
        raise RuntimeError("C04_SOURCE_DATA_MANIFEST is required")
    path = Path(raw).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"source data manifest does not exist: {path}")
    return path


def catalog_load_week(*_args: Any, **_kwargs: Any) -> tuple[pd.DataFrame, list[Path]]:
    """Satisfy the runner acquisition interface without reading market data."""

    return pd.DataFrame(), []


def catalog_prepare(
    _frame: pd.DataFrame,
    _raw_files: list[Path],
    catalog_path: Path,
    _raw_cache: Path,
    output: Path,
    _config: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    source = _source_catalog()
    if catalog_path.exists() or catalog_path.is_symlink():
        if catalog_path.is_symlink() or catalog_path.is_file():
            catalog_path.unlink()
        else:
            shutil.rmtree(catalog_path)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.symlink_to(source, target_is_directory=True)

    source_manifest = _source_manifest()
    manifest = output / "data_manifest.json"
    shutil.copy2(source_manifest, manifest)
    return {}, manifest


base.load_week = catalog_load_week
base.prepare_catalog = catalog_prepare


if __name__ == "__main__":
    base.main()
