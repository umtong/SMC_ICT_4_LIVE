#!/usr/bin/env python3
"""Condense route-frontier experiments into one inspectable research result."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten(item, name))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            name = f"{prefix}[{index}]"
            out.update(flatten(item, name))
    else:
        out[prefix] = value
    return out


def numeric(flat: dict[str, Any], required: tuple[str, ...], preferred: tuple[str, ...] = ()) -> float | None:
    candidates: list[tuple[int, str, float]] = []
    for key, value in flat.items():
        low = key.lower()
        if not all(token in low for token in required):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        priority = sum(token in low for token in preferred)
        candidates.append((priority, key, number))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return candidates[0][2]


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}
    for summary_path in sorted(root.glob("fraction-*/strict-router/summary.json")):
        label = summary_path.parents[1].name
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        payloads[label] = payload
        flat = flatten(payload)
        rows.append(
            {
                "fraction": label.removeprefix("fraction-"),
                "label": label,
                "trades": numeric(flat, ("trade",), ("fresh", "integrated", "closed")),
                "trades_per_day": numeric(flat, ("trade", "day"), ("fresh", "integrated")),
                "win_rate": numeric(flat, ("win", "rate"), ("fresh", "integrated")),
                "mean_net_r": numeric(flat, ("mean", "net", "r"), ("fresh", "integrated")),
                "profit_factor": numeric(flat, ("profit", "factor"), ("fresh", "integrated")),
                "ending_nav": numeric(flat, ("ending", "nav"), ("fresh", "integrated")),
                "maximum_drawdown": numeric(flat, ("drawdown",), ("fresh", "integrated", "maximum")),
                "development_mean_net_r": numeric(flat, ("development", "mean", "net", "r")),
                "development_ending_nav": numeric(flat, ("development", "ending", "nav")),
            }
        )

    if not rows:
        inventory = [str(path.relative_to(root)) for path in root.rglob("summary.json")]
        raise FileNotFoundError("No strict-router summaries found: " + repr(inventory))

    keys = list(rows[0])
    with (output / "frontier_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)

    report = {"frontiers": rows, "raw_summaries": payloads}
    (output / "summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(rows, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
