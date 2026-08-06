#!/usr/bin/env python3
"""Warmup-aware entry point for the V22 causal signal compiler."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

import nt_backtest
import rich_signal_compiler_v22 as compiler


def _load_data_with_warmup(
    rich_dir: Path,
    kline_dir: Path,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: compiler.Config,
    *,
    download_klines: bool,
):
    build_start_text = os.environ.get("C04_BUILD_START")
    build_end_text = os.environ.get("C04_BUILD_END")
    if not build_start_text or not build_end_text:
        raise RuntimeError("C04_BUILD_START and C04_BUILD_END are required")
    build_start = pd.Timestamp(build_start_text, tz="UTC")
    build_end = pd.Timestamp(build_end_text, tz="UTC")
    if not build_start <= evaluation_start <= evaluation_end <= (
        build_end + pd.Timedelta(hours=23, minutes=59)
    ):
        raise RuntimeError("evaluation must be inside build range")

    if download_klines:
        kline_paths = compiler.base.ensure_klines(
            config.symbol,
            build_start.date(),
            build_end.date(),
            kline_dir,
        )
    else:
        kline_paths = sorted(kline_dir.glob(f"{config.symbol}-1m-*.zip"))
    rich = compiler.base.load_rich(rich_dir)
    klines = compiler.base.load_klines(kline_paths)
    required = pd.date_range(build_start.normalize(), build_end.normalize(), freq="1D").date
    present = set(klines.index.normalize().date)
    missing = [str(day) for day in required if day not in present]
    if missing:
        raise RuntimeError(f"missing build kline days: {missing}")
    data = compiler.base.prepare_data(rich, klines, config)

    nt_frames = [nt_backtest.read_daily_kline(path) for path in kline_paths]
    nt_frame = pd.concat(nt_frames).sort_index()
    if len(nt_frame) != len(data):
        raise RuntimeError(
            f"Nautilus/compiler row mismatch: nt={len(nt_frame)} data={len(data)}",
        )
    return data, nt_frame


compiler._load_data = _load_data_with_warmup


if __name__ == "__main__":
    compiler.main()
