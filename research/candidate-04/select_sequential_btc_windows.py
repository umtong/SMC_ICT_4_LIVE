#!/usr/bin/env python3
"""Preselect three unseen BTC weeks and a disjoint 91-day block."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

from select_cross_asset_windows import anchors_absent
from select_cross_asset_windows import corpus
from select_cross_asset_windows import overlaps
from select_cross_asset_windows import recorded_intervals


def select(
    repository_root: Path,
    evidence_root: Path,
    candidate: str,
    family: str,
    route: str,
    source_commit: str,
) -> dict[str, Any]:
    intervals = recorded_intervals(evidence_root)
    text = corpus(repository_root)
    seed = hashlib.sha256(
        f"{candidate}:{family}:{route}:{source_commit}:sequential-btc".encode()
    ).hexdigest()
    weeks: list[tuple[str, date, date, date]] = []
    cursor = date(2023, 1, 2)
    while cursor <= date(2025, 12, 1):
        end = cursor + timedelta(days=6)
        if not overlaps(cursor, end, intervals) and anchors_absent(
            text, cursor, end
        ):
            weeks.append(
                (
                    hashlib.sha256(
                        f"{seed}:week:{cursor}:{end}".encode()
                    ).hexdigest(),
                    cursor - timedelta(days=2),
                    cursor,
                    end,
                )
            )
        cursor += timedelta(days=7)
    weeks.sort()
    if len(weeks) < 3:
        raise RuntimeError("fewer than three unseen weekly BTC windows")
    selected_weeks = weeks[:3]
    occupied = [
        *intervals,
        *((item[2], item[3]) for item in selected_weeks),
    ]
    blocks: list[tuple[str, date, date, date]] = []
    cursor = date(2023, 2, 1)
    while cursor <= date(2025, 9, 1):
        end = cursor + timedelta(days=90)
        if not overlaps(cursor, end, occupied) and anchors_absent(
            text, cursor, end
        ):
            blocks.append(
                (
                    hashlib.sha256(
                        f"{seed}:long:{cursor}:{end}".encode()
                    ).hexdigest(),
                    cursor - timedelta(days=2),
                    cursor,
                    end,
                )
            )
        cursor += timedelta(days=7)
    blocks.sort()
    if not blocks:
        raise RuntimeError("no disjoint unseen 91-day BTC block")
    block = blocks[0]
    return {
        "candidate": candidate,
        "family": family,
        "route": route,
        "source_commit": source_commit,
        "selection_time": "before_market_data_access",
        "selection_method": (
            "SHA256 rank after excluding committed evaluation intervals; "
            "window anchors absent from repository text"
        ),
        "weeks": [
            {
                "build_start": str(item[1]),
                "evaluation_start": str(item[2]),
                "evaluation_end": str(item[3]),
            }
            for item in selected_weeks
        ],
        "long_evaluation": {
            "build_start": str(block[1]),
            "evaluation_start": str(block[2]),
            "evaluation_end": str(block[3]),
        },
        "recorded_interval_count": len(intervals),
        "market_or_performance_data_used_for_selection": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = select(
        args.repository_root,
        args.evidence_root,
        args.candidate,
        args.family,
        args.route,
        args.source_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
