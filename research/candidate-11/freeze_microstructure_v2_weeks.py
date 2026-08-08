#!/usr/bin/env python3
"""Freeze three new BTC microstructure-v2 weeks before data access."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import random
import subprocess

SEED = 2026080814
START = date(2023, 1, 1)
LAST_START = date(2025, 12, 25)
DAYS = 7


def overlaps(left: date, left_days: int, right: date, right_days: int) -> bool:
    return left < right + timedelta(days=right_days) and right < left + timedelta(days=left_days)


def collect_intervals(root: Path) -> list[tuple[date, int]]:
    result: list[tuple[date, int]] = []
    config_path = root / "config.json"
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for value in config.get("selection", {}).get("weeks", {}).values():
            begin = date.fromisoformat(value["start"])
            end = date.fromisoformat(value["end_exclusive"])
            result.append((begin, (end - begin).days))
    for name in (
        "irx_holdout_protocol.json", "microstructure_protocol.json",
        "irx_long_protocol.json",
    ):
        path = root / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = list(payload.get("weeks", {}).values())
        if isinstance(payload.get("interval"), dict):
            values.append(payload["interval"])
        for value in values:
            begin = date.fromisoformat(value["start"])
            end = date.fromisoformat(value["end_exclusive"])
            result.append((begin, (end - begin).days))
    return result


def main() -> None:
    root = Path(__file__).resolve().parent
    occupied = collect_intervals(root)
    candidates = [START + timedelta(days=offset) for offset in range((LAST_START - START).days + 1)]
    random.Random(SEED).shuffle(candidates)
    selected: list[date] = []
    for candidate in candidates:
        if any(overlaps(candidate, DAYS, other, length) for other, length in occupied):
            continue
        if any(overlaps(candidate, DAYS, other, DAYS) for other in selected):
            continue
        selected.append(candidate)
        if len(selected) == 3:
            break
    if len(selected) != 3:
        raise SystemExit("unable to freeze three microstructure-v2 weeks")
    protocol = {
        "schema": "candidate-11-btc-microstructure-v2-protocol-v1",
        "seed": SEED,
        "selection_method": (
            "random.Random(seed).shuffle over every seven-day start from "
            "2023-01-01 through 2025-12-25; first three starts non-overlapping "
            "all previously fixed Candidate 11 intervals and each other"
        ),
        "symbol": "BTCUSDT",
        "dataset": "Binance USD-M daily aggTrades; causal one-second aggregation",
        "warmup_days": 1,
        "evaluation_days": DAYS,
        "source_commit_before_market_data": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip(),
        "weeks": {
            f"M{index + 4}": {
                "start": value.isoformat(),
                "end_exclusive": (value + timedelta(days=DAYS)).isoformat(),
            }
            for index, value in enumerate(selected)
        },
        "market_data_opened": False,
        "success_claim": False,
    }
    path = root / "microstructure_v2_protocol.json"
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        immutable = {key: existing.get(key) for key in ("seed", "selection_method", "symbol", "weeks")}
        proposed = {key: protocol.get(key) for key in immutable}
        if immutable != proposed:
            raise SystemExit("frozen microstructure-v2 protocol changed")
        print("microstructure-v2 protocol already frozen")
        return
    path.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(protocol, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
