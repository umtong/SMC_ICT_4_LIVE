#!/usr/bin/env python3
"""Reproduce the prospectively locked v104 BTC week selections."""
from __future__ import annotations

from datetime import date, timedelta
import json
import random

START = date(2024, 1, 1)
END = date(2025, 12, 29)
EXCLUDED = {date(2025, 10, 6), date(2025, 11, 17)}
SEEDS = (20260807104, 20260807105, 20260807106)


def population() -> list[date]:
    values: list[date] = []
    current = START
    while current <= END:
        if current.weekday() == 0 and current not in EXCLUDED:
            values.append(current)
        current += timedelta(days=1)
    return values


def selections() -> list[dict[str, object]]:
    values = population()
    return [
        {
            "seed": seed,
            "start_utc": random.Random(seed).choice(values).isoformat() + "T00:00:00Z",
        }
        for seed in SEEDS
    ]


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "population_start": START.isoformat(),
                "population_end": END.isoformat(),
                "excluded": sorted(value.isoformat() for value in EXCLUDED),
                "population_size": len(population()),
                "selections": selections(),
            },
            indent=2,
            sort_keys=True,
        )
    )
