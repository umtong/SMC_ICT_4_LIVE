#!/usr/bin/env python3
"""Combine per-segment forward-path diagnostics without shell heredocs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def run(root: Path) -> None:
    frames: list[pd.DataFrame] = []
    for label in ("discovery", "confirmation-1", "confirmation-2"):
        frame = pd.read_csv(root / label / "forward_paths.csv")
        frame.insert(0, "segment", label)
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    combined.to_csv(root / "combined_forward_paths.csv", index=False)
    summary = {
        "plans": int(len(combined)),
        "by_segment": combined.groupby("segment").size().astype(int).to_dict(),
        "by_response": combined.groupby("response").size().astype(int).to_dict(),
    }
    (root / "combined_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    run(args.root)


if __name__ == "__main__":
    main()
