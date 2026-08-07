#!/usr/bin/env python3
"""Index-, symbol- and rich-loader-correct V48 compiler entrypoint.

Rich feature frames use completed-minute labels while Nautilus kline frames use
exchange close timestamps. The base compiler requires positional equality, so
this wrapper aligns rich labels and applies the same integer positions to each
Nautilus frame.

The frozen Config loader and rich loader also contain BTC-first research guards.
For the four explicitly allowed experiment symbols, this wrapper sends an
otherwise identical BTC-symbol JSON through the original Config loader, changes
only the already-validated base symbol, and reads the symbol-matching rich files
with the original close-observed contract. No market-state, signal, target,
risk, cost or execution logic is changed.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile

import pandas as pd

import cross_market_information_transfer_compiler as base


CandidateError = base.v22.v9.CandidateError


def load_allowed_symbol_config(path: Path):
    values = json.loads(path.read_text(encoding="utf-8"))
    symbol = str(values.get("symbol", "BTCUSDT"))
    if symbol not in base.SYMBOLS:
        raise CandidateError(
            f"unsupported cross-market experiment symbol: {symbol}"
        )

    validated_values = dict(values)
    validated_values["symbol"] = "BTCUSDT"
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        encoding="utf-8",
    )
    temporary = Path(handle.name)
    try:
        json.dump(validated_values, handle)
        handle.close()
        validated = base.v22.Config.load(temporary)
    finally:
        try:
            handle.close()
        except Exception:
            pass
        temporary.unlink(missing_ok=True)

    actual_base = replace(validated.base, symbol=symbol)
    return replace(validated, base=actual_base)


def load_rich_for_symbol(directory: Path, symbol: str) -> pd.DataFrame:
    if symbol not in base.SYMBOLS:
        raise CandidateError(
            f"unsupported cross-market rich symbol: {symbol}"
        )
    paths = sorted(directory.glob(f"{symbol}-rich-*.csv.gz"))
    if not paths:
        raise CandidateError(f"no {symbol} rich features in {directory}")
    frame = pd.concat((pd.read_csv(path) for path in paths), ignore_index=True)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame["observed_time"] = pd.to_datetime(frame["observed_time"], utc=True)
    frame = frame.set_index("open_time").sort_index()
    if frame.index.has_duplicates:
        raise CandidateError("duplicate rich-feature timestamps")
    expected_observed = frame.index + pd.Timedelta(minutes=1)
    if not (frame["observed_time"].array == expected_observed.array).all():
        raise CandidateError("rich features violate the close-observed contract")
    return frame


def load_allowed_symbol_rich(directory: Path) -> pd.DataFrame:
    symbol = directory.name
    if symbol not in base.SYMBOLS:
        candidates = {
            path.name.split("-rich-", 1)[0]
            for path in directory.glob("*-rich-*.csv.gz")
            if "-rich-" in path.name
        }
        if len(candidates) != 1:
            raise CandidateError(
                f"cannot infer allowed rich symbol in {directory}: {sorted(candidates)}"
            )
        symbol = next(iter(candidates))
    return load_rich_for_symbol(directory, symbol)


# rich_signal_compiler_v22b calls this frozen BTC loader internally. Replace only
# that filename selector inside the V48 process; all preparation remains frozen.
base.v22.base.load_rich = load_allowed_symbol_rich


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
