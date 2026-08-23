#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from auction_episode_research import run_research


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.end < args.start:
        raise SystemExit("--end must be >= --start")
    if args.warmup_days < 2:
        raise SystemExit("--warmup-days must be at least two")
    summary = run_research(
        start=args.start,
        end=args.end,
        warmup_days=args.warmup_days,
        symbols=tuple(args.symbols),
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
