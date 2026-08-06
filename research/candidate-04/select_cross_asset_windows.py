#!/usr/bin/env python3
"""Select integrated-validation windows before opening market observations.

Selection excludes every evaluation interval already recorded in committed JSON
evidence, then ranks remaining windows by a deterministic SHA256 seed.  It does
not inspect price, order-flow, signal or PnL data.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def recorded_intervals(root: Path) -> list[tuple[date, date]]:
    intervals: set[tuple[date, date]] = set()
    if not root.exists():
        return []
    for path in root.rglob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        for item in walk(value):
            start = parse_date(item.get("evaluation_start"))
            end = parse_date(item.get("evaluation_end"))
            if start is not None and end is not None and start <= end:
                intervals.add((start, end))
    return sorted(intervals)


def overlaps(
    start: date,
    end: date,
    intervals: Iterable[tuple[date, date]],
) -> bool:
    return any(start <= other_end and end >= other_start for other_start, other_end in intervals)


def corpus(root: Path) -> str:
    text = ""
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text += path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return text


def anchors_absent(text: str, start: date, end: date) -> bool:
    duration = (end - start).days
    anchors = {
        start,
        end,
        start + timedelta(days=duration // 3),
        start + timedelta(days=2 * duration // 3),
    }
    return all(str(value) not in text for value in anchors)


def ranked_windows(
    seed: str,
    prefix: str,
    days: int,
    intervals: list[tuple[date, date]],
    text: str,
    lower: date = date(2023, 1, 2),
    upper: date = date(2025, 9, 1),
) -> list[tuple[str, date, date, date]]:
    rows: list[tuple[str, date, date, date]] = []
    cursor = lower
    while cursor <= upper:
        end = cursor + timedelta(days=days - 1)
        if end > date(2025, 12, 31):
            break
        if not overlaps(cursor, end, intervals) and anchors_absent(text, cursor, end):
            rank = hashlib.sha256(
                f"{seed}:{prefix}:{cursor}:{end}".encode()
            ).hexdigest()
            rows.append((rank, cursor - timedelta(days=2), cursor, end))
        cursor += timedelta(days=7)
    rows.sort()
    return rows


def select(
    repository_root: Path,
    evidence_root: Path,
    candidates: dict[str, Any],
    origin_run_id: int,
) -> dict[str, Any]:
    successful = candidates.get("successful_btc_long_candidates")
    if not isinstance(successful, list) or not successful:
        raise ValueError("no successful BTC-long candidates")
    seed_payload = json.dumps(successful, sort_keys=True, separators=(",", ":"))
    seed = hashlib.sha256(
        f"{origin_run_id}:{seed_payload}:cross-asset".encode()
    ).hexdigest()
    intervals = recorded_intervals(evidence_root)
    text = corpus(repository_root)
    screens = ranked_windows(seed, "screen", 28, intervals, text)
    if not screens:
        raise RuntimeError("no nonoverlapping 28-day screen window")
    screen = screens[0]
    occupied = [*intervals, (screen[2], screen[3])]
    longs = ranked_windows(seed, "long", 91, occupied, text)
    if not longs:
        raise RuntimeError("no nonoverlapping 91-day long window")
    long_block = longs[0]
    return {
        "selection_time": "before_cross_asset_market_data_access",
        "selection_method": (
            "SHA256 rank after excluding every committed evaluation interval; "
            "four internal date anchors must also be absent from repository text"
        ),
        "seed_sha256": seed,
        "origin_run_id": origin_run_id,
        "candidate_ids": [
            str(item["candidate_id"]) for item in successful
        ],
        "screen": {
            "build_start": str(screen[1]),
            "evaluation_start": str(screen[2]),
            "evaluation_end": str(screen[3]),
            "calendar_days": 28,
        },
        "long_evaluation": {
            "build_start": str(long_block[1]),
            "evaluation_start": str(long_block[2]),
            "evaluation_end": str(long_block[3]),
            "calendar_days": 91,
        },
        "recorded_intervals_excluded": [
            {"evaluation_start": str(start), "evaluation_end": str(end)}
            for start, end in intervals
        ],
        "performance_or_market_data_used_for_selection": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--origin-run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    result = select(
        args.repository_root,
        args.evidence_root,
        candidates,
        args.origin_run_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
