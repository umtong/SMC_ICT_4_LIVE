#!/usr/bin/env python3
"""Build one checksum-verified Candidate 29-compatible input chunk."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
CANDIDATE29 = HERE.parent / "candidate-29"
sys.path.insert(0, str(CANDIDATE29))

from build_chunk import build_chunk as build_verified_chunk


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--core-start", required=True)
    parser.add_argument("--core-end", required=True)
    parser.add_argument("--warmup-days", type=int, default=3)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_verified_chunk(
        symbol=args.symbol,
        core_start=date.fromisoformat(args.core_start),
        core_end=date.fromisoformat(args.core_end),
        warmup_days=args.warmup_days,
        cache=args.cache.resolve(),
        output=args.output.resolve(),
    )
    result["consumer"] = "candidate-37-burst-shape-propagation-router"
    (args.output.resolve() / "candidate37_chunk.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
