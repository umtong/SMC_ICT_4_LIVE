#!/usr/bin/env python3
"""Candidate 14 v10 protocol runner with complete source provenance."""
from __future__ import annotations

import argparse
from pathlib import Path

import candidate14_runner as base


V10_LOCKED_FILES = (
    "auction_origin_ownership.py",
    "confirmed_acceptance_failure_v9.py",
    "failure_leg_leadership_materializer.py",
    "candidate14_v10_runner.py",
    "diagnostic_continuous_aggregate.py",
    "run_week.sh",
)
base.LOCKED_FILES = tuple(dict.fromkeys((*base.LOCKED_FILES, *V10_LOCKED_FILES)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("week")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    base.execute(args.week, args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
