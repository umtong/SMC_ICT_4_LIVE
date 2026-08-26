#!/usr/bin/env python3
"""Materialize the corrected mechanism router without duplicating its large source."""
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
    old_acceptance = '''    acceptance = pd.to_numeric(frame.get("auction_acceptance_strength"), errors="coerce")
    failure = pd.to_numeric(frame.get("auction_failure_pressure"), errors="coerce")
    if acceptance is None or isinstance(acceptance, np.ndarray):
        acceptance = pd.Series(np.nan, index=frame.index)
    if failure is None or isinstance(failure, np.ndarray):
        failure = pd.Series(np.nan, index=frame.index)
'''
    new_acceptance = '''    acceptance = (
        pd.to_numeric(frame["auction_acceptance_strength"], errors="coerce")
        if "auction_acceptance_strength" in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype=float)
    )
    failure = (
        pd.to_numeric(frame["auction_failure_pressure"], errors="coerce")
        if "auction_failure_pressure" in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype=float)
    )
'''
    old_fraction = '''        extracted = data.get("source_window_file", "").astype(str).str.extract(
            r"fraction[-_/]?(\\d{3})", expand=False
        )
'''
    new_fraction = '''        source_window = (
            data["source_window_file"].astype(str)
            if "source_window_file" in data.columns
            else pd.Series("", index=data.index, dtype="object")
        )
        extracted = source_window.str.extract(
            r"fraction[-_/]?(\\d{3})", expand=False
        )
'''
    replacements = 0
    if old_acceptance in text:
        text = text.replace(old_acceptance, new_acceptance)
        replacements += 1
    if old_fraction in text:
        text = text.replace(old_fraction, new_fraction)
        replacements += 1
    if replacements != 2:
        raise RuntimeError(f"Expected two known router corrections, applied {replacements}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    compile(text, str(args.output), "exec")
    print(f"materialized corrected router at {args.output}")


if __name__ == "__main__":
    main()
