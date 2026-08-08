#!/usr/bin/env python3
"""Freeze BTC aggTrades research weeks before opening microstructure data."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import random
import subprocess

SEED = 2026080812
START = date(2023, 1, 1)
LAST_START = date(2025, 12, 25)
DAYS = 7


def overlaps(left: date, right: date) -> bool:
    return left < right + timedelta(days=DAYS) and right < left + timedelta(days=DAYS)


def main() -> None:
    root = Path(__file__).resolve().parent
    occupied: list[date] = []
    config_path = root / "config.json"
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        occupied.extend(
            date.fromisoformat(value["start"])
            for value in config.get("selection", {}).get("weeks", {}).values()
        )
    for name in ("irx_holdout_protocol.json", "microstructure_protocol.json"):
        path = root / name
        if path.is_file() and name != "microstructure_protocol.json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            occupied.extend(date.fromisoformat(value["start"]) for value in payload.get("weeks", {}).values())

    starts = [START + timedelta(days=offset) for offset in range((LAST_START - START).days + 1)]
    random.Random(SEED).shuffle(starts)
    selected: list[date] = []
    for candidate in starts:
        if any(overlaps(candidate, other) for other in occupied + selected):
            continue
        selected.append(candidate)
        if len(selected) == 3:
            break
    if len(selected) != 3:
        raise SystemExit("unable to freeze three microstructure weeks")

    protocol = {
        "schema": "candidate-11-btc-microstructure-protocol-v1",
        "seed": SEED,
        "selection_method": (
            "random.Random(seed).shuffle over every seven-day start from "
            "2023-01-01 through 2025-12-25; first three starts non-overlapping "
            "all prior Candidate 11 protocol weeks and each other"
        ),
        "symbol": "BTCUSDT",
        "dataset": "Binance USD-M daily aggTrades plus one-second causal aggregation",
        "warmup_days": 1,
        "evaluation_days": DAYS,
        "source_commit_before_market_data": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip(),
        "weeks": {
            f"M{index + 1}": {
                "start": value.isoformat(),
                "end_exclusive": (value + timedelta(days=DAYS)).isoformat(),
            }
            for index, value in enumerate(selected)
        },
        "market_data_opened": False,
        "success_claim": False,
    }
    path = root / "microstructure_protocol.json"
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        immutable = {key: existing.get(key) for key in ("seed", "selection_method", "symbol", "weeks")}
        proposed = {key: protocol.get(key) for key in immutable}
        if immutable != proposed:
            raise SystemExit("frozen microstructure protocol changed")
        print("microstructure protocol already frozen")
        return
    path.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(protocol, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
