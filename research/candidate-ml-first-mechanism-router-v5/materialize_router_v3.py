#!/usr/bin/env python3
"""Materialize the robust router and broaden semantic schema resolution."""
from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"{label} patch point not found")
    return text.replace(old, new, 1)


def main() -> None:
    args = parse_args()
    text = args.source.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''    acceptance = pd.to_numeric(frame.get("auction_acceptance_strength"), errors="coerce")
    failure = pd.to_numeric(frame.get("auction_failure_pressure"), errors="coerce")
    if acceptance is None or isinstance(acceptance, np.ndarray):
        acceptance = pd.Series(np.nan, index=frame.index)
    if failure is None or isinstance(failure, np.ndarray):
        failure = pd.Series(np.nan, index=frame.index)
''',
        '''    acceptance = (
        pd.to_numeric(frame["auction_acceptance_strength"], errors="coerce")
        if "auction_acceptance_strength" in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype=float)
    )
    failure = (
        pd.to_numeric(frame["auction_failure_pressure"], errors="coerce")
        if "auction_failure_pressure" in frame.columns
        else pd.Series(np.nan, index=frame.index, dtype=float)
    )
''',
        "control-strength",
    )
    text = replace_once(
        text,
        '''        extracted = data.get("source_window_file", "").astype(str).str.extract(
            r"fraction[-_/]?(\\d{3})", expand=False
        )
''',
        '''        source_window = (
            data["source_window_file"].astype(str)
            if "source_window_file" in data.columns
            else pd.Series("", index=data.index, dtype="object")
        )
        extracted = source_window.str.extract(
            r"fraction[-_/]?(\\d{3})", expand=False
        )
''',
        "fraction-source",
    )
    text = replace_once(
        text,
        '''    win_r = first_existing(columns, WIN_R_CANDIDATES) or token_column(
        columns, (("gross", "rr"), ("route", "rr"), ("target", "rr"))
    )
''',
        '''    win_r = first_existing(columns, WIN_R_CANDIDATES) or token_column(
        columns,
        (
            ("planned", "net", "rr"),
            ("planned", "rr"),
            ("gross", "rr"),
            ("route", "rr"),
            ("target", "rr"),
            ("reward", "risk"),
        ),
    )
''',
        "planned-rr-schema",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    compile(text, str(args.output), "exec")
    print(f"materialized robust router v3 at {args.output}")


if __name__ == "__main__":
    main()
