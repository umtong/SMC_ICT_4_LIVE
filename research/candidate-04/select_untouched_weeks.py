#!/usr/bin/env python3
"""Select sequential untouched BTC weeks before any data is opened.

Selection is deterministic from the frozen candidate commit and stage name. The
eligible calendar is constructed without reading market data or results, and
all development/build windows used by candidate-04 are excluded first. Each
selected seven-day evaluation receives two prior warm-up days. Later selections
also exclude every earlier selected build window, preventing overlapping
observations across the three sequential gates.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import json
import re
from typing import Iterable


COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EVALUATION_DAYS = 7
WARMUP_DAYS = 2
DEFAULT_CALENDAR_START = date(2023, 1, 2)
DEFAULT_CALENDAR_END = date(2025, 12, 29)
DEFAULT_EXCLUDED_BUILD_WINDOWS = (
    (date(2023, 8, 2), date(2023, 8, 10)),
    (date(2023, 12, 16), date(2023, 12, 24)),
    (date(2024, 5, 25), date(2024, 6, 2)),
    (date(2024, 8, 5), date(2024, 8, 13)),
    (date(2024, 12, 25), date(2025, 1, 2)),
    (date(2025, 3, 22), date(2025, 3, 30)),
    (date(2025, 10, 18), date(2025, 10, 26)),
)


@dataclass(frozen=True, slots=True)
class Week:
    evaluation_start: date
    evaluation_end: date
    build_start: date
    build_end: date

    @classmethod
    def from_monday(cls, monday: date) -> "Week":
        if monday.weekday() != 0:
            raise ValueError("evaluation start must be Monday")
        evaluation_end = monday + timedelta(days=EVALUATION_DAYS - 1)
        return cls(
            evaluation_start=monday,
            evaluation_end=evaluation_end,
            build_start=monday - timedelta(days=WARMUP_DAYS),
            build_end=evaluation_end,
        )

    def serializable(self) -> dict[str, str]:
        return {key: value.isoformat() for key, value in asdict(self).items()}


def windows_overlap(
    first_start: date,
    first_end: date,
    second_start: date,
    second_end: date,
) -> bool:
    return first_start <= second_end and second_start <= first_end


def eligible_weeks(
    calendar_start: date = DEFAULT_CALENDAR_START,
    calendar_end: date = DEFAULT_CALENDAR_END,
    excluded_build_windows: Iterable[tuple[date, date]] = (
        DEFAULT_EXCLUDED_BUILD_WINDOWS
    ),
) -> list[Week]:
    """Return Monday evaluation weeks whose complete build window is untouched."""

    if calendar_start.weekday() != 0 or calendar_end.weekday() != 0:
        raise ValueError("calendar bounds must be Mondays")
    if calendar_end < calendar_start:
        raise ValueError("calendar end precedes start")
    excluded = tuple(excluded_build_windows)
    result: list[Week] = []
    current = calendar_start
    while current <= calendar_end:
        week = Week.from_monday(current)
        if not any(
            windows_overlap(
                week.build_start,
                week.build_end,
                excluded_start,
                excluded_end,
            )
            for excluded_start, excluded_end in excluded
        ):
            result.append(week)
        current += timedelta(days=7)
    return result


def select_sequential_weeks(
    frozen_commit: str,
    count: int = 3,
    stage_prefix: str = "untouched-btc",
    candidates: Iterable[Week] | None = None,
) -> list[tuple[str, Week, str, int]]:
    """Select non-overlapping weeks from commit-derived hashes."""

    commit = frozen_commit.lower()
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("frozen_commit must be a full lowercase SHA-1")
    if count <= 0:
        raise ValueError("count must be positive")
    available = sorted(
        list(candidates if candidates is not None else eligible_weeks()),
        key=lambda item: item.evaluation_start,
    )
    selected: list[tuple[str, Week, str, int]] = []
    for ordinal in range(1, count + 1):
        if not available:
            raise ValueError("not enough non-overlapping eligible weeks")
        stage = f"{stage_prefix}-{ordinal}"
        digest = hashlib.sha256(
            f"candidate-04|{commit}|{stage}".encode("utf-8")
        ).hexdigest()
        index = int(digest, 16) % len(available)
        week = available[index]
        selected.append((stage, week, digest, index))
        available = [
            candidate
            for candidate in available
            if not windows_overlap(
                candidate.build_start,
                candidate.build_end,
                week.build_start,
                week.build_end,
            )
        ]
    return selected


def selection_record(frozen_commit: str, count: int = 3) -> dict[str, object]:
    eligible = eligible_weeks()
    selected = select_sequential_weeks(
        frozen_commit,
        count=count,
        candidates=eligible,
    )
    return {
        "candidate": "candidate-04-validated-boundary-negotiation-core",
        "frozen_commit": frozen_commit.lower(),
        "selection_contract": {
            "market_data_read_before_selection": False,
            "result_data_read_before_selection": False,
            "calendar_start": DEFAULT_CALENDAR_START.isoformat(),
            "calendar_end": DEFAULT_CALENDAR_END.isoformat(),
            "evaluation_days": EVALUATION_DAYS,
            "warmup_days": WARMUP_DAYS,
            "hash": "sha256(candidate-04|frozen_commit|stage)",
            "excluded_build_windows": [
                {"start": start.isoformat(), "end": end.isoformat()}
                for start, end in DEFAULT_EXCLUDED_BUILD_WINDOWS
            ],
            "eligible_weeks_before_sequential_exclusion": len(eligible),
        },
        "selected": [
            {
                "stage": stage,
                "hash_digest": digest,
                "index_within_current_eligible_set": index,
                **week.serializable(),
            }
            for stage, week, digest, index in selected
        ],
        "sequential_gate": (
            "open stage N+1 only after stage N passes the frozen cost, risk, "
            "frequency, win-rate, drawdown and NAV-growth assessment"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-commit", required=True)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--output")
    args = parser.parse_args()
    record = selection_record(args.frozen_commit, args.count)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.output:
        from pathlib import Path

        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
