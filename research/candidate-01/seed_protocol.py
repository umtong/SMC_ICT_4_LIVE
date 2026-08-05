#!/usr/bin/env python3
"""Reproduce the untouched random-week ordering used by candidate 01."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import random


def monday_pool(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end must not precede start")
    current = start + timedelta(days=(7 - start.weekday()) % 7)
    values: list[date] = []
    while current <= end:
        values.append(current)
        current += timedelta(days=7)
    return values


def seeded_weeks(*, seed: int, start: date, end: date, count: int) -> list[date]:
    pool = monday_pool(start, end)
    if not 0 < count <= len(pool):
        raise ValueError("count must fit within the Monday pool")
    return random.Random(seed).sample(pool, count)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=4_012_026)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2022, 1, 3))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 22))
    parser.add_argument("--count", type=int, default=6)
    args = parser.parse_args()
    for index, value in enumerate(
        seeded_weeks(seed=args.seed, start=args.start, end=args.end, count=args.count),
        start=1,
    ):
        print(f"{index}\t{value.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
