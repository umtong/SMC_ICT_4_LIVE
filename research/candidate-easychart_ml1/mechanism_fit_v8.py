#!/usr/bin/env python3
"""Exact-calendar wrapper for long continuous v7 evaluation."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

import mechanism_fit_v3 as fit
from mechanism_harvest_v7 import FEATURE_COLUMNS


def _calendar_days(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    if {"evaluation_start", "evaluation_end"}.issubset(frame.columns):
        periods = frame[["period", "evaluation_start", "evaluation_end"]].drop_duplicates("period")
        start = pd.to_datetime(periods["evaluation_start"], utc=True, errors="raise")
        end = pd.to_datetime(periods["evaluation_end"], utc=True, errors="raise")
        return int(((end.dt.floor("D") - start.dt.floor("D")).dt.days + 1).sum())
    dated = frame.assign(day=frame["entry_time"].dt.floor("D"))
    return int(dated.groupby("period")["day"].nunique().sum())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    fit.FEATURE_COLUMNS = FEATURE_COLUMNS
    fit._calendar_days = _calendar_days
    args = parse_args()
    fit.run(args.root, args.output)


if __name__ == "__main__":
    main()
