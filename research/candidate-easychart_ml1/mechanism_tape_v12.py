#!/usr/bin/env python3
"""Memory-bounded parsed-day cache for the exact-tape harvester."""
from __future__ import annotations

import argparse
from datetime import date
from functools import lru_cache
from pathlib import Path

import mechanism_tape_v10 as v10
import mechanism_tape_v11 as v11

FEATURE_COLUMNS = v11.FEATURE_COLUMNS
TAPE_FEATURE_COLUMNS = v11.TAPE_FEATURE_COLUMNS
SYMBOLS = v11.SYMBOLS


@lru_cache(maxsize=8)
def _cached_day(symbol: str, day: date, cache_text: str) -> v10.TapeStore:
    return v11.load_aggtrades_day(symbol, day, Path(cache_text))


def load_aggtrades_day(symbol: str, day: date, cache: Path) -> v10.TapeStore:
    return _cached_day(symbol, day, str(cache.resolve()))


def _install() -> None:
    v11._install()
    v10.load_aggtrades_day = load_aggtrades_day


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v12"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    _install()
    args = parse_args()
    v10.harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
