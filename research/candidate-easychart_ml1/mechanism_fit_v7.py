#!/usr/bin/env python3
"""Fit the shared selective policy with the runtime-stable v7 manifest."""
from __future__ import annotations

import argparse
from pathlib import Path

import mechanism_fit_v3 as fit
from mechanism_harvest_v7 import FEATURE_COLUMNS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    fit.FEATURE_COLUMNS = FEATURE_COLUMNS
    args = parse_args()
    fit.run(args.root, args.output)


if __name__ == "__main__":
    main()
