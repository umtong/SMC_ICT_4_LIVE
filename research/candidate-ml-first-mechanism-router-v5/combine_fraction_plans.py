#!/usr/bin/env python3
"""Combine candidate plans from alternate reachable-frontier actions."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

FRACTION_RE = re.compile(r"fraction[-_/]?(\d{3})", re.I)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def fraction_from_path(path: Path) -> float:
    for part in reversed(path.parts):
        match = FRACTION_RE.search(part)
        if match:
            return int(match.group(1)) / 100.0
    raise ValueError(f"No fraction label in {path}")


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan_files = sorted(root.rglob("all_candidate_plans.csv.gz"))
    if not plan_files:
        plan_files = sorted(root.rglob("all_candidate_plans.csv"))
    if not plan_files:
        raise FileNotFoundError(f"No aggregated fraction plans below {root}")

    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, object]] = []
    for path in plan_files:
        fraction = fraction_from_path(path)
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        frame = frame.copy()
        frame["target_fraction"] = fraction
        frame["fraction_plan_file"] = str(path.relative_to(root))
        frames.append(frame)
        manifest.append(
            {
                "path": str(path.relative_to(root)),
                "target_fraction": fraction,
                "rows": int(len(frame)),
            }
        )
    if not frames:
        raise RuntimeError("Every aggregated fraction-plan file was empty")

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined.to_csv(output / "all_fraction_candidate_plans.csv.gz", index=False, compression="gzip")
    summary = {
        "files": manifest,
        "rows": int(len(combined)),
        "fraction_rows": {
            str(key): int(value)
            for key, value in combined.groupby("target_fraction", dropna=False).size().to_dict().items()
        },
        "columns": list(combined.columns),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
