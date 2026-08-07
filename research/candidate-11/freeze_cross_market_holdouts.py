#!/usr/bin/env python3
"""Freeze three cross-market holdouts only after C1-C3 approval."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import random
import subprocess

SEED = 2026080817
START = date(2023, 1, 1)
LAST_START = date(2025, 12, 25)
DAYS = 7


def overlaps(left: date, left_days: int, right: date, right_days: int) -> bool:
    return left < right + timedelta(days=right_days) and right < left + timedelta(days=left_days)


def fixed_intervals(root: Path) -> list[tuple[date, int]]:
    result: list[tuple[date, int]] = []
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    for value in config.get("selection", {}).get("weeks", {}).values():
        begin = date.fromisoformat(value["start"])
        end = date.fromisoformat(value["end_exclusive"])
        result.append((begin, (end - begin).days))
    for path in root.glob("*_protocol.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        values = list(payload.get("weeks", {}).values()) if isinstance(payload.get("weeks"), dict) else []
        if isinstance(payload.get("interval"), dict):
            values.append(payload["interval"])
        for value in values:
            if not isinstance(value, dict) or not {"start", "end_exclusive"}.issubset(value):
                continue
            begin = date.fromisoformat(value["start"])
            end = date.fromisoformat(value["end_exclusive"])
            result.append((begin, (end - begin).days))
    return result


def main() -> None:
    root = Path(__file__).resolve().parent
    summary_path = root / "results" / "CROSS_MARKET" / "summary.json"
    if not summary_path.is_file():
        print("cross-market holdouts not frozen: C1-C3 evidence unavailable")
        return
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("three_week_gate_passed") is not True:
        print("cross-market holdouts not frozen: C1-C3 gate failed")
        return
    occupied = fixed_intervals(root)
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
        raise SystemExit("unable to freeze three cross-market holdouts")
    protocol = {
        "schema": "candidate-11-cross-market-holdout-protocol-v1",
        "seed": SEED,
        "selection_method": (
            "random.Random(seed).shuffle over every seven-day start from "
            "2023-01-01 through 2025-12-25; first three starts non-overlapping "
            "all previously fixed Candidate 11 intervals and each other"
        ),
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        "dataset": "Binance USD-M one-minute klines with taker-buy volume",
        "warmup_days": 2,
        "evaluation_days": DAYS,
        "source_commit_before_market_data": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip(),
        "cross_market_summary_sha256": sha256(summary_path.read_bytes()).hexdigest(),
        "weeks": {
            f"C{index + 4}": {
                "start": value.isoformat(),
                "end_exclusive": (value + timedelta(days=DAYS)).isoformat(),
            }
            for index, value in enumerate(selected)
        },
        "market_data_opened": False,
        "success_claim": False,
    }
    path = root / "cross_market_holdout_protocol.json"
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        immutable = {
            key: existing.get(key)
            for key in (
                "seed", "selection_method", "symbols", "weeks",
                "cross_market_summary_sha256",
            )
        }
        proposed = {key: protocol.get(key) for key in immutable}
        if immutable != proposed:
            raise SystemExit("frozen cross-market holdout protocol changed")
        print("cross-market holdout protocol already frozen")
        return
    path.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(protocol, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
