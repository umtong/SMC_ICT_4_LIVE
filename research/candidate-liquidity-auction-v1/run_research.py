#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import json

from liquidity_auction_system import run_research


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=45)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_research(
        start=args.start,
        end=args.end,
        warmup_days=args.warmup_days,
        symbols=tuple(args.symbols),
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
