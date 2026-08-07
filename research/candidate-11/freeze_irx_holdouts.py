#!/usr/bin/env python3
"""Freeze three non-overlapping seven-day IRX holdouts before data access."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import random
import subprocess

SEED = 2026080811
START = date(2023, 1, 1)
LAST_START = date(2025, 12, 25)
WARMUP_DAYS = 3
EVALUATION_DAYS = 7


def overlap(a: date, b: date) -> bool:
    return a < b + timedelta(days=EVALUATION_DAYS) and b < a + timedelta(days=EVALUATION_DAYS)


def main() -> None:
    root = Path(__file__).resolve().parent
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    occupied = [date.fromisoformat(value["start"]) for value in config["selection"]["weeks"].values()]
    candidates = [START + timedelta(days=offset) for offset in range((LAST_START - START).days + 1)]
    rng = random.Random(SEED)
    rng.shuffle(candidates)
    selected: list[date] = []
    for candidate in candidates:
        if any(overlap(candidate, other) for other in occupied + selected):
            continue
        selected.append(candidate)
        if len(selected) == 3:
            break
    if len(selected) != 3:
        raise SystemExit("could not freeze three non-overlapping holdouts")

    summary_path = root / "results" / "IRX_MATRIX" / "summary.json"
    matrix = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    protocol = {
        "schema": "candidate-11-irx-holdout-protocol-v1",
        "seed": SEED,
        "selection_method": "random.Random(seed).shuffle over every seven-day start from 2023-01-01 through 2025-12-25; first three starts non-overlapping with all config weeks and each other",
        "warmup_days": WARMUP_DAYS,
        "evaluation_days": EVALUATION_DAYS,
        "source_commit_before_market_data": commit,
        "matrix_selected_variant_at_freeze": matrix.get("selected_variant"),
        "weeks": {
            f"W{10 + index}": {
                "start": value.isoformat(),
                "end_exclusive": (value + timedelta(days=EVALUATION_DAYS)).isoformat(),
            }
            for index, value in enumerate(selected)
        },
        "market_data_opened": False,
        "success_claim": False,
    }
    path = root / "irx_holdout_protocol.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        immutable = {key: existing.get(key) for key in ("seed", "selection_method", "weeks")}
        proposed = {key: protocol.get(key) for key in ("seed", "selection_method", "weeks")}
        if immutable != proposed:
            raise SystemExit("frozen IRX holdout protocol changed")
        print("IRX holdout protocol already frozen")
        return
    path.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(protocol, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
