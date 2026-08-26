#!/usr/bin/env python3
"""Combine independently harvested causal-plan windows without assuming artifact layout."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

PERIOD_RE = re.compile(r"20\d{2}-(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)
PLAN_PATTERNS = ("*candidate*plan*.csv.gz", "*candidate*plan*.csv")
TIME_CANDIDATES = (
    "decision_time",
    "decision_ts",
    "signal_time",
    "entry_time",
    "event_time",
    "timestamp",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def period_from_path(path: Path) -> str:
    for part in reversed(path.parts):
        match = PERIOD_RE.search(part)
        if match:
            return match.group(0).lower()
    return path.parent.name


def role_for_period(period: str) -> str:
    return "fresh" if period in {"2025-nov", "2026-jan", "2026-mar", "2026-apr"} else "dev"


def discover_plans(root: Path) -> list[Path]:
    found: dict[Path, None] = {}
    for pattern in PLAN_PATTERNS:
        for path in root.rglob(pattern):
            if path.is_file() and "exact-aggregate" not in path.parts:
                found[path.resolve()] = None
    return sorted(found)


def load_json(path: Path) -> dict[str, Any] | list[Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    plan_files = discover_plans(root)
    if not plan_files:
        candidates = sorted(str(p.relative_to(root)) for p in root.rglob("*.csv*"))
        raise FileNotFoundError(
            "No candidate-plan CSV found. CSV inventory:\n" + "\n".join(candidates[:200])
        )

    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    for path in plan_files:
        period = period_from_path(path)
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        frame = frame.copy()
        if "period" not in frame.columns:
            frame["period"] = period
        else:
            frame["period"] = frame["period"].fillna(period).astype(str)
        if "role" not in frame.columns:
            frame["role"] = role_for_period(period)
        else:
            frame["role"] = frame["role"].fillna(role_for_period(period)).astype(str)
        frame["source_window_file"] = str(path.relative_to(root))
        frames.append(frame)
        manifest.append(
            {
                "period": period,
                "role": role_for_period(period),
                "path": str(path.relative_to(root)),
                "rows": int(len(frame)),
                "columns": int(len(frame.columns)),
            }
        )

    if not frames:
        raise RuntimeError("Candidate-plan files were present but all were empty")

    combined = pd.concat(frames, ignore_index=True, sort=False)
    sort_columns = [name for name in TIME_CANDIDATES if name in combined.columns]
    if sort_columns:
        combined = combined.sort_values(sort_columns + ["period"], kind="mergesort").reset_index(drop=True)

    plans_out = output / "all_candidate_plans.csv.gz"
    combined.to_csv(plans_out, index=False, compression="gzip")

    summaries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("summary.json")):
        payload = load_json(path)
        if isinstance(payload, dict):
            summaries.append(
                {
                    "path": str(path.relative_to(root)),
                    "period": period_from_path(path),
                    "payload": payload,
                }
            )

    summary = {
        "plan_files": manifest,
        "plan_file_count": len(manifest),
        "rows": int(len(combined)),
        "columns": list(combined.columns),
        "period_rows": {
            str(key): int(value)
            for key, value in combined.groupby("period", dropna=False).size().to_dict().items()
        },
        "role_rows": {
            str(key): int(value)
            for key, value in combined.groupby("role", dropna=False).size().to_dict().items()
        },
        "window_summaries": summaries,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"plans": str(plans_out), "rows": len(combined), "files": len(manifest)}))


if __name__ == "__main__":
    main()
