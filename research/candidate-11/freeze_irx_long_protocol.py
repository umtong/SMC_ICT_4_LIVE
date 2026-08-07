#!/usr/bin/env python3
"""Freeze a continuous 90-day IRX interval only after untouched-week approval."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import random
import subprocess

SEED = 2026080813
START = date(2023, 1, 1)
LAST_START = date(2025, 9, 30)
DAYS = 90


def overlaps(left: date, left_days: int, right: date, right_days: int) -> bool:
    return left < right + timedelta(days=right_days) and right < left + timedelta(days=left_days)


def main() -> None:
    root = Path(__file__).resolve().parent
    holdout_path = root / "results" / "IRX_HOLDOUT" / "summary.json"
    binding_path = root / "irx_holdout_candidate.json"
    if not holdout_path.is_file() or not binding_path.is_file():
        print("IRX long protocol not frozen: untouched holdout evidence is unavailable")
        return
    holdout = json.loads(holdout_path.read_text(encoding="utf-8"))
    if holdout.get("holdout_gate_passed") is not True:
        print("IRX long protocol not frozen: untouched holdout gate failed")
        return

    occupied: list[tuple[date, int]] = []
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    for value in config.get("selection", {}).get("weeks", {}).values():
        begin = date.fromisoformat(value["start"])
        end = date.fromisoformat(value["end_exclusive"])
        occupied.append((begin, (end - begin).days))
    for name in ("irx_holdout_protocol.json", "microstructure_protocol.json"):
        path = root / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for value in payload.get("weeks", {}).values():
            begin = date.fromisoformat(value["start"])
            end = date.fromisoformat(value["end_exclusive"])
            occupied.append((begin, (end - begin).days))

    candidates = [START + timedelta(days=offset) for offset in range((LAST_START - START).days + 1)]
    random.Random(SEED).shuffle(candidates)
    selected = next(
        (
            candidate for candidate in candidates
            if all(not overlaps(candidate, DAYS, other, length) for other, length in occupied)
        ),
        None,
    )
    if selected is None:
        raise SystemExit("unable to freeze a non-overlapping 90-day interval")
    protocol = {
        "schema": "candidate-11-irx-long-protocol-v1",
        "seed": SEED,
        "selection_method": (
            "random.Random(seed).shuffle over every 90-day start from 2023-01-01 "
            "through 2025-09-30; first interval non-overlapping all previously "
            "opened Candidate 11 protocol intervals"
        ),
        "warmup_days": 3,
        "evaluation_days": DAYS,
        "interval": {
            "start": selected.isoformat(),
            "end_exclusive": (selected + timedelta(days=DAYS)).isoformat(),
        },
        "selected_variant": json.loads(binding_path.read_text(encoding="utf-8"))["selected_variant"],
        "holdout_summary_sha256": __import__("hashlib").sha256(holdout_path.read_bytes()).hexdigest(),
        "source_commit_before_market_data": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip(),
        "market_data_opened": False,
        "success_claim": False,
    }
    path = root / "irx_long_protocol.json"
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        immutable = {key: existing.get(key) for key in ("seed", "selection_method", "interval", "selected_variant")}
        proposed = {key: protocol.get(key) for key in immutable}
        if immutable != proposed:
            raise SystemExit("frozen IRX long protocol changed")
        print("IRX long protocol already frozen")
        return
    path.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(protocol, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
