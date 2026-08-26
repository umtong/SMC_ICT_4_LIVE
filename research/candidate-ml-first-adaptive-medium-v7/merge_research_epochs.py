#!/usr/bin/env python3
"""Merge previously inspected short epochs as development with untouched evaluation epochs."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

FRACTION_RE = re.compile(r"fraction[-_/]?(\d{3})", re.I)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", required=True, type=Path)
    parser.add_argument("--evaluation-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def fraction(path: Path) -> float:
    for part in reversed(path.parts):
        match = FRACTION_RE.search(part)
        if match:
            return int(match.group(1)) / 100.0
    raise ValueError(f"Missing fraction label: {path}")


def load(root: Path, role: str, epoch: str) -> tuple[list[pd.DataFrame], list[dict[str, object]]]:
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, object]] = []
    files = sorted(root.rglob("all_candidate_plans.csv.gz"))
    if not files:
        files = sorted(root.rglob("all_candidate_plans.csv"))
    for path in files:
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        value = fraction(path)
        frame = frame.copy()
        frame["role"] = role
        frame["research_epoch"] = epoch
        frame["target_fraction"] = value
        frame["epoch_plan_file"] = str(path.relative_to(root))
        frames.append(frame)
        manifest.append(
            {
                "epoch": epoch,
                "role": role,
                "target_fraction": value,
                "path": str(path.relative_to(root)),
                "rows": int(len(frame)),
            }
        )
    return frames, manifest


def main() -> None:
    args = parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    development, dev_manifest = load(args.development_root.resolve(), "dev", "prior_short")
    evaluation, eval_manifest = load(args.evaluation_root.resolve(), "fresh", "untouched_medium")
    if not development or not evaluation:
        raise RuntimeError(
            f"Missing epoch plans: development_files={len(development)} evaluation_files={len(evaluation)}"
        )
    combined = pd.concat([*development, *evaluation], ignore_index=True, sort=False)
    combined.to_csv(output / "all_epoch_candidate_plans.csv.gz", index=False, compression="gzip")
    summary = {
        "files": [*dev_manifest, *eval_manifest],
        "rows": int(len(combined)),
        "role_rows": {
            str(key): int(value)
            for key, value in combined.groupby("role", dropna=False).size().to_dict().items()
        },
        "period_rows": {
            str(key): int(value)
            for key, value in combined.groupby("period", dropna=False).size().to_dict().items()
        },
        "fraction_rows": {
            str(key): int(value)
            for key, value in combined.groupby("target_fraction", dropna=False).size().to_dict().items()
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
