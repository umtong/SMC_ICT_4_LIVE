#!/usr/bin/env python3
"""Export compact one-minute price/volume universes for trade-by-trade chart clinics.

This is an offline research aid, not a runtime feature path.  It downloads the same
checksum-verified Binance USD-M kline source used by the existing EasyChart research
and preserves the raw OHLC, quote volume, trade count and taker-buy fields.  A few
transparent price/flow transforms are added only to make chart inspection faster.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RE1 = ROOT / "research" / "candidate-easychart_re1"
if str(RE1) not in sys.path:
    sys.path.insert(0, str(RE1))

from data_re1_flow import load_range_flow  # noqa: E402


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


def _derive(frame: pd.DataFrame, symbol: str, period: str) -> pd.DataFrame:
    out = frame.copy()
    out.insert(0, "period", period)
    out.insert(1, "symbol", symbol)
    quote = out["quote_volume"].astype(float).clip(lower=1e-12)
    signed_quote = 2.0 * out["taker_buy_quote_volume"].astype(float) - quote
    prior_close = out["close"].shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prior_close).abs(),
            (out["low"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["signed_quote_flow"] = signed_quote
    out["taker_imbalance"] = (signed_quote / quote).clip(-1.0, 1.0)
    out["log_return_1m"] = np.log(out["close"].clip(lower=1e-12)).diff()
    out["true_range"] = true_range
    out["close_location"] = (
        (out["close"] - out["low"])
        / (out["high"] - out["low"]).clip(lower=1e-12)
    )
    out["body_fraction"] = (
        (out["close"] - out["open"]).abs()
        / (out["high"] - out["low"]).clip(lower=1e-12)
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before-days", type=int, default=3)
    parser.add_argument("--after-days", type=int, default=2)
    args = parser.parse_args()

    if args.end < args.start:
        raise SystemExit("end must not precede start")
    if args.before_days < 0 or args.after_days < 0:
        raise SystemExit("context days must be nonnegative")

    load_start = args.start - timedelta(days=args.before_days)
    load_end = args.end + timedelta(days=args.after_days)
    pieces: list[pd.DataFrame] = []
    per_symbol: dict[str, dict[str, object]] = {}
    for symbol in SYMBOLS:
        frame = load_range_flow(symbol, load_start, load_end, args.cache)
        derived = _derive(frame, symbol, args.period)
        pieces.append(derived)
        per_symbol[symbol] = {
            "rows": int(len(derived)),
            "first_open_time": str(derived["open_time_dt"].iloc[0]),
            "last_open_time": str(derived["open_time_dt"].iloc[-1]),
        }

    universe = pd.concat(pieces, ignore_index=True, sort=False)
    universe = universe.sort_values(["open_time_dt", "symbol"]).reset_index(drop=True)
    args.output.mkdir(parents=True, exist_ok=True)
    universe.to_csv(args.output / "raw_universe_1m.csv.gz", index=False, compression="gzip")
    metadata = {
        "period": args.period,
        "decision_start": args.start.isoformat(),
        "decision_end": args.end.isoformat(),
        "raw_start": load_start.isoformat(),
        "raw_end": load_end.isoformat(),
        "symbols": list(SYMBOLS),
        "rows": int(len(universe)),
        "source": "checksum-verified Binance Vision USD-M one-minute klines",
        "columns": list(universe.columns),
        "per_symbol": per_symbol,
        "purpose": "offline price-volume and trade-by-trade chart clinic; never a runtime feature artifact",
    }
    (args.output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
