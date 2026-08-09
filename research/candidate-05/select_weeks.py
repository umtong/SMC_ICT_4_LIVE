#!/usr/bin/env python3
"""Reproduce the frozen Candidate 05 week draw without reading market data."""
from datetime import date, timedelta
import random

start = date(2023, 1, 1)
last_start = date(2025, 12, 25)
candidates = [start + timedelta(days=i) for i in range((last_start - start).days + 1)]
rng = random.Random(5005)
rng.shuffle(candidates)
chosen: list[date] = []
for candidate in candidates:
    if all(abs((candidate - existing).days) >= 28 for existing in chosen):
        chosen.append(candidate)
    if len(chosen) == 3:
        break
for index, selected in enumerate(chosen, start=1):
    print(f"week-{index}: {selected} through {selected + timedelta(days=6)}")
