#!/usr/bin/env python3
"""Classify structured research evidence into the next controlled action."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


IMPLEMENTATION_MARKERS = {
    "implementation_or_workflow",
    "implementation_still_unresolved",
    "implementation_or_workflow_failure_before_decision",
    "implementation_error_requires_same_week_recovery",
}


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def any_true(value: Any, keys: set[str]) -> bool:
    return any(
        bool(item.get(key))
        for item in walk(value)
        for key in keys
        if key in item
    )


def implementation_failure(value: dict[str, Any]) -> bool:
    for item in walk(value):
        for key in ("failure_classification", "status", "decision", "route"):
            marker = item.get(key)
            if isinstance(marker, str) and marker in IMPLEMENTATION_MARKERS:
                return True
    return False


def route_passes(value: dict[str, Any]) -> dict[str, bool]:
    passes = {"continuation": False, "reversal": False}
    for item in walk(value):
        for key, child in item.items():
            if not isinstance(child, dict):
                continue
            lowered = str(key).lower()
            passed = bool(
                child.get("candidate_pass")
                or child.get("three_week_pass")
                or child.get("three_unopened_weeks_pass")
                or child.get("project_target_reached")
            )
            if not passed:
                continue
            if "continuation" in lowered:
                passes["continuation"] = True
            if "reversal" in lowered:
                passes["reversal"] = True
    return passes


def source_commit(value: dict[str, Any], fallback: str | None = None) -> str | None:
    for item in walk(value):
        for key in ("source_commit", "source_checkout_ref", "head_sha"):
            candidate = item.get(key)
            if isinstance(candidate, str) and len(candidate) == 40:
                return candidate
    return fallback


def classify(value: dict[str, Any], fallback_source: str | None = None) -> dict[str, Any]:
    project = any_true(value, {"project_target_reached"})
    full = any_true(
        value,
        {
            "development_pass",
            "three_week_pass",
            "three_unopened_weeks_pass",
        },
    )
    final_completed = any_true(value, {"final_validation_completed"})
    implementation = implementation_failure(value)
    passes = route_passes(value)
    survivors = [name for name in ("continuation", "reversal") if passes[name]]

    if project:
        action = "project_target_reached"
        survivors = ["full"]
    elif implementation:
        action = "same_week_implementation_recovery"
        survivors = []
    elif final_completed:
        action = "economic_path_exhausted_after_long_evaluation"
        survivors = []
    elif full:
        action = "full_candidate_progression_pending_or_completed"
        survivors = ["full"]
    elif len(survivors) == 1:
        action = "cross_develop_single_survivor"
    elif len(survivors) > 1:
        action = "cross_develop_all_survivors"
    else:
        action = "economic_path_exhausted"

    return {
        "action": action,
        "project_target_reached": project,
        "implementation_failure": implementation,
        "full_progression": full,
        "final_validation_completed": final_completed,
        "route_passes": passes,
        "survivors": survivors,
        "source_commit": source_commit(value, fallback_source),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--fallback-source")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = json.loads(args.evidence.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("evidence must be a JSON object")
    result = classify(value, args.fallback_source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
