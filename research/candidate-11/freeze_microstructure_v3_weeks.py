#!/usr/bin/env python3
"""Freeze three untouched BTC balance-acceptance weeks before market-data access.

The protocol is deliberately generated without reading market data.  Every
seven-day start in the admissible date range is shuffled once with a committed
seed, then the first three starts that do not overlap any previously frozen
Candidate 11 interval are selected.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import random
import subprocess
from typing import Any

SEED = 2026080817
START = date(2023, 1, 1)
LAST_START = date(2025, 12, 25)
DAYS = 7
TARGET_NAME = "microstructure_v3_protocol.json"


def overlaps(left: date, left_days: int, right: date, right_days: int) -> bool:
    return left < right + timedelta(days=right_days) and right < left + timedelta(days=left_days)


def interval_from_mapping(value: Any) -> tuple[date, int] | None:
    if not isinstance(value, dict):
        return None
    start = value.get("start")
    end = value.get("end_exclusive")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    begin = date.fromisoformat(start)
    finish = date.fromisoformat(end)
    if finish <= begin:
        raise SystemExit(f"invalid frozen interval: {value}")
    return begin, (finish - begin).days


def collect_intervals(root: Path) -> list[tuple[date, int]]:
    result: list[tuple[date, int]] = []

    config_path = root / "config.json"
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for value in config.get("selection", {}).get("weeks", {}).values():
            interval = interval_from_mapping(value)
            if interval is not None:
                result.append(interval)

    for path in sorted(root.glob("*protocol.json")):
        if path.name == TARGET_NAME:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        values: list[Any] = []
        weeks = payload.get("weeks")
        if isinstance(weeks, dict):
            values.extend(weeks.values())
        values.extend(
            value
            for key in ("interval", "evaluation_interval", "screening_interval")
            if (value := payload.get(key)) is not None
        )
        for value in values:
            interval = interval_from_mapping(value)
            if interval is not None:
                result.append(interval)
    return result


def main() -> None:
    root = Path(__file__).resolve().parent
    occupied = collect_intervals(root)
    candidates = [
        START + timedelta(days=offset)
        for offset in range((LAST_START - START).days + 1)
    ]
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
        raise SystemExit("unable to freeze three untouched balance-acceptance weeks")

    protocol = {
        "schema": "candidate-11-btc-microstructure-v3-protocol-v1",
        "seed": SEED,
        "selection_method": (
            "random.Random(seed).shuffle over every seven-day start from "
            "2023-01-01 through 2025-12-25; first three starts non-overlapping "
            "all previously frozen Candidate 11 intervals and each other"
        ),
        "symbol": "BTCUSDT",
        "dataset": "Binance USD-M daily aggTrades; causal one-second aggregation",
        "detector_family": "BALANCE_ACCEPTANCE_MEASURED_MOVE",
        "warmup_days": 1,
        "evaluation_days": DAYS,
        "source_commit_before_market_data": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "weeks": {
            f"M{index + 7}": {
                "start": value.isoformat(),
                "end_exclusive": (value + timedelta(days=DAYS)).isoformat(),
            }
            for index, value in enumerate(selected)
        },
        "market_data_opened": False,
        "success_claim": False,
    }

    path = root / TARGET_NAME
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        immutable_keys = (
            "seed", "selection_method", "symbol", "dataset",
            "detector_family", "warmup_days", "evaluation_days", "weeks",
        )
        immutable = {key: existing.get(key) for key in immutable_keys}
        proposed = {key: protocol.get(key) for key in immutable_keys}
        if immutable != proposed:
            raise SystemExit("frozen microstructure-v3 protocol changed")
        print("microstructure-v3 protocol already frozen")
        return

    path.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(protocol, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
