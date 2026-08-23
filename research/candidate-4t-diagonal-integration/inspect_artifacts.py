#!/usr/bin/env python3
"""Inspect artifacts produced by the existing diagonal/channel workflow."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

C4T_REQUIRED = {"action_id", "state_id", "episode_id", "order_time_ns"}
ALIASES = {
    "order_time_ns": ("order_time_ns", "decision_time_ns", "signal_time_ns", "emission_time_ns"),
    "entry": ("entry", "entry_price", "planned_entry"),
    "stop": ("stop", "stop_price", "planned_stop"),
    "target": ("target", "target_price", "planned_target"),
    "side": ("side", "direction"),
    "family": ("family", "event_type", "setup_family"),
    "action_id": ("action_id", "plan_id", "trade_id"),
    "state_id": ("state_id", "decision_id", "signal_id"),
    "episode_id": ("episode_id", "event_id", "boundary_id"),
}


def alias_map(columns: set[str]) -> dict[str, str | None]:
    return {
        canonical: next((candidate for candidate in candidates if candidate in columns), None)
        for canonical, candidates in ALIASES.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report: dict[str, Any] = {"root": str(args.root), "tables": []}
    for path in sorted(args.root.rglob("*.csv")):
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception as exc:
            report["tables"].append({"path": str(path), "error": repr(exc)})
            continue
        columns = set(frame.columns)
        mapping = alias_map(columns)
        has_geometry = all(mapping[name] is not None for name in ("entry", "stop", "target", "side"))
        report["tables"].append({
            "path": str(path.relative_to(args.root)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "rows": int(len(frame)),
            "columns": list(frame.columns),
            "dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()},
            "candidate4t_required_present": C4T_REQUIRED.issubset(columns),
            "alias_map": mapping,
            "has_executable_geometry": bool(has_geometry),
            "has_outcomes": any(column in columns for column in ("net_r", "outcome", "win", "target_first")),
            "sample": frame.head(3).replace({float("inf"): None, float("-inf"): None}).where(pd.notna(frame.head(3)), None).to_dict("records"),
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
