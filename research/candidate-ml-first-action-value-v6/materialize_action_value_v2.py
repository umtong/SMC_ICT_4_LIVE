#!/usr/bin/env python3
"""Materialize chronological action-value research from the shared source."""
from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = args.source.read_text(encoding="utf-8")
    old = '    periods = sorted(variant_data["_period"].unique())\n'
    new = '''    periods = (
        variant_data.groupby("_period", dropna=False)["_decision_time"]
        .min()
        .sort_values(kind="mergesort")
        .index.astype(str)
        .tolist()
    )
'''
    if old not in text:
        raise RuntimeError("Chronology patch point not found")
    text = text.replace(old, new, 1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    compile(text, str(args.output), "exec")
    print(f"materialized chronological action-value router at {args.output}")


if __name__ == "__main__":
    main()
