#!/usr/bin/env python3
"""Deterministically select ADSE untouched BTC validation weeks."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json

SALT = "candidate-03|ADSE-v1|BTCUSDT"
START = date.fromisoformat("2021-01-04")
END = date.fromisoformat("2025-12-22")
MIN_SEPARATION_DAYS = 180
EXCLUDED = {
    "2021-01-11", "2021-12-13", "2022-03-07", "2022-07-18",
    "2023-04-10", "2023-08-28", "2025-02-03", "2025-03-17", "2025-10-06",
}
FROZEN = ("2025-05-05", "2022-09-19", "2023-06-05")


def select() -> tuple[str, ...]:
    candidates: list[tuple[str, date]] = []
    cursor = START
    while cursor <= END:
        stamp = cursor.isoformat()
        if stamp not in EXCLUDED:
            candidates.append((sha256(f"{SALT}|{stamp}".encode()).hexdigest(), cursor))
        cursor += timedelta(days=7)
    candidates.sort()
    selected: list[date] = []
    for _, week in candidates:
        if all(abs((week - prior).days) >= MIN_SEPARATION_DAYS for prior in selected):
            selected.append(week)
            if len(selected) == 3: break
    return tuple(week.isoformat() for week in selected)


def main() -> int:
    selected = select()
    if selected != FROZEN:
        raise RuntimeError(f"selection drifted: {selected} != {FROZEN}")
    print(json.dumps({
        "salt": SALT,
        "candidate_range": [START.isoformat(), END.isoformat()],
        "minimum_separation_days": MIN_SEPARATION_DAYS,
        "excluded": sorted(EXCLUDED),
        "selected": list(selected),
    }, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
