#!/usr/bin/env python3
"""Adapt hierarchical actions to the dependency-free liquidity-displacement SVG renderer."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from render_cases_svg import main as _unused  # ensure renderer imports in core-check
import render_cases_svg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warmup-days", type=int, default=45)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--actions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    frame = pd.read_csv(args.actions).copy()
    if frame.empty:
        raise RuntimeError("no hierarchical actions to render")

    # The generic renderer names the same causal geometry after the original
    # liquidity-displacement candidate.  Translate fields explicitly rather
    # than weakening either research schema.
    aliases = {
        "origin_kind": "setup_kind",
        "diagnostic_displacement_time_ns": "diagnostic_confirmation_time_ns",
        "diagnostic_origin_lower": "diagnostic_zone_lower",
        "diagnostic_origin_upper": "diagnostic_zone_upper",
        "event_penetration_bps": "manipulation_penetration_bps",
        "event_to_displacement_minutes": "manipulation_duration_minutes",
    }
    missing_sources = [source for source in aliases.values() if source not in frame.columns]
    if missing_sources:
        raise RuntimeError(f"hierarchical actions missing renderer source fields: {missing_sources}")
    translated = {target: frame[source] for target, source in aliases.items()}
    frame = pd.concat([frame, pd.DataFrame(translated, index=frame.index)], axis=1)

    # Keep the actual decision-stage language in the chart title.
    frame["event_type"] = frame["decision_stage"]
    adapted = args.output.parent / "hierarchical_actions_for_renderer.csv"
    frame.to_csv(adapted, index=False)

    import sys

    original = sys.argv
    try:
        sys.argv = [
            "render_cases_svg.py",
            "--start",
            args.start,
            "--end",
            args.end,
            "--warmup-days",
            str(args.warmup_days),
            "--cache",
            str(args.cache),
            "--actions",
            str(adapted),
            "--output",
            str(args.output),
        ]
        render_cases_svg.main()
    finally:
        sys.argv = original


if __name__ == "__main__":
    main()
