#!/usr/bin/env python3
"""Index- and symbol-contract-correct V48 compiler entrypoint.

Rich feature frames use completed-minute labels while Nautilus kline frames use
exchange close timestamps. The base compiler requires positional equality, so
this wrapper aligns rich labels and applies the same integer positions to each
Nautilus frame.

The frozen Config class also contains a BTC-first research guard. For the four
explicitly allowed experiment symbols, this wrapper instantiates the unchanged
configuration and validates an otherwise identical BTC-symbol clone. Thus every
risk, cost and structural validation remains active; only the obsolete
single-symbol research guard is bypassed.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pandas as pd

import cross_market_information_transfer_compiler as base


def load_allowed_symbol_config(path: Path):
    values = json.loads(path.read_text(encoding="utf-8"))
    if "funding_hours_utc" in values:
        values["funding_hours_utc"] = tuple(
            int(value) for value in values["funding_hours_utc"]
        )
    allowed = set(base.v22.Config.__dataclass_fields__)  # type: ignore[attr-defined]
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise base.v22.CandidateError(f"unknown config keys: {unknown}")
    result = base.v22.Config(**values)
    if result.symbol not in base.SYMBOLS:
        raise base.v22.CandidateError(
            f"unsupported cross-market experiment symbol: {result.symbol}"
        )
    replace(result, symbol="BTCUSDT").validate()
    return result


def load_frames(
    rich_root: Path,
    config_root: Path,
    kline_root: Path,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    frames: dict[str, pd.DataFrame] = {}
    nt_frames: dict[str, pd.DataFrame] = {}
    for symbol in base.SYMBOLS:
        config = load_allowed_symbol_config(config_root / f"{symbol}.json")
        if config.symbol != symbol:
            raise RuntimeError(
                f"config symbol/path mismatch: expected {symbol}, got {config.symbol}"
            )
        data, nt_frame = base.v22._load_data(
            rich_root / symbol,
            kline_root / symbol,
            evaluation_start,
            evaluation_end,
            config,
            download_klines=True,
        )
        if len(data) != len(nt_frame):
            raise RuntimeError(
                f"{symbol}: compiler/Nautilus row mismatch {len(data)} != {len(nt_frame)}"
            )
        frames[symbol] = data
        nt_frames[symbol] = nt_frame

    common = frames["BTCUSDT"].index
    for symbol in base.SYMBOLS[1:]:
        common = common.intersection(frames[symbol].index)
    if common.empty:
        raise RuntimeError("four-symbol rich streams have no common timestamps")
    common = common.sort_values()

    for symbol in base.SYMBOLS:
        positions = frames[symbol].index.get_indexer(common)
        if (positions < 0).any():
            raise RuntimeError(f"{symbol}: common timestamp alignment failed")
        frames[symbol] = frames[symbol].iloc[positions].copy()
        nt_frames[symbol] = nt_frames[symbol].iloc[positions].copy()
        if len(frames[symbol]) != len(nt_frames[symbol]):
            raise RuntimeError(f"{symbol}: aligned row mismatch")
    return frames, nt_frames


base.load_frames = load_frames


if __name__ == "__main__":
    base.main()
