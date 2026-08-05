#!/usr/bin/env python3
"""Reproduce LCPT-v1's untouched, separated BTC validation-week order."""

from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import random


SALT = "candidate-03|LCPT-v1|BTCUSDT"
UNIVERSE_START = date(2021, 1, 4)
UNIVERSE_END = date(2025, 12, 22)
MINIMUM_SEPARATION_DAYS = 180

# Exclude every week opened or reserved before LCPT-v1 was frozen. FAR-v2 weeks
# two and three were never evaluated, but they were already publicly reserved,
# so LCPT does not recycle them as apparently fresh validation periods.
EXCLUDED_WEEKS = {
    date(2022, 3, 7),
    date(2025, 3, 17),
    date(2023, 8, 28),
    date(2022, 7, 18),
    date(2021, 12, 13),
    date(2021, 1, 11),
}
EXPECTED = (
    date(2023, 4, 10),
    date(2025, 2, 3),
    date(2025, 10, 6),
)


def select_validation_weeks(count: int = 3) -> tuple[date, ...]:
    weeks: list[date] = []
    current = UNIVERSE_START
    while current <= UNIVERSE_END:
        if current not in EXCLUDED_WEEKS:
            weeks.append(current)
        current += timedelta(days=7)

    seed = int.from_bytes(sha256(SALT.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    pool = weeks[:]
    selected: list[date] = []

    while pool and len(selected) < count:
        week = rng.choice(pool)
        selected.append(week)
        pool = [
            candidate
            for candidate in pool
            if abs((candidate - week).days) >= MINIMUM_SEPARATION_DAYS
        ]

    if len(selected) != count:
        raise RuntimeError("validation universe cannot produce separated weeks")
    return tuple(selected)


def main() -> int:
    selected = select_validation_weeks()
    if selected != EXPECTED:
        raise RuntimeError(f"selection drift: {selected!r} != {EXPECTED!r}")
    for week in selected:
        print(week.isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
