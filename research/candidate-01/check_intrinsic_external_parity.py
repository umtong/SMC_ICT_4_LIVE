#!/usr/bin/env python3
"""Assert exact strategy-output parity between two weekly artifact directories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal


SUMMARY_KEYS = (
    "evaluation_event_bars",
    "evaluation_retest_signals",
    "routed_plans",
    "decision_counts",
    "detector_counts",
    "metrics",
)
CSV_FILES = (
    "trades.csv",
    "daily_nav.csv",
    "rejections.csv",
    "routing_decisions.csv",
)


def read_frame(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return None


def run(args: argparse.Namespace) -> int:
    left_summary = json.loads((args.left / args.left_summary).read_text(encoding="utf-8"))
    right_summary = json.loads((args.right / args.right_summary).read_text(encoding="utf-8"))
    for key in SUMMARY_KEYS:
        if left_summary[key] != right_summary[key]:
            raise AssertionError(
                f"summary parity failed for {key}: "
                f"{left_summary[key]!r} != {right_summary[key]!r}",
            )

    for name in CSV_FILES:
        left = read_frame(args.left / name)
        right = read_frame(args.right / name)
        if left is None or right is None:
            if left is not None or right is not None:
                raise AssertionError(f"only one side of {name} is empty")
            if (args.left / name).read_text(encoding="utf-8") != (
                args.right / name
            ).read_text(encoding="utf-8"):
                raise AssertionError(f"empty-file bytes differ for {name}")
            continue
        assert_frame_equal(left, right, check_dtype=False, check_exact=True)

    report = {
        "label": args.label,
        "week": args.week,
        "parity": True,
        "metrics": right_summary["metrics"],
    }
    destination = args.right / "parity_report.json"
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument(
        "--left-summary",
        default="intrinsic_external_liquidity_v2_daily_week_summary.json",
    )
    parser.add_argument(
        "--right-summary",
        default="intrinsic_external_liquidity_v3_daily_week_summary.json",
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--week", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
