#!/usr/bin/env python3
"""Aggregate precommitted cross-asset gap mechanism diagnostics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


DATA_SCHEMAS = {
    "candidate-11-cross-asset-gap-data-v1",
    "candidate-11-cross-asset-gap-aggtrades-data-v1",
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    protocol = load(args.protocol)
    intervals = sorted(protocol["weeks"])
    weeks: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    manifests = 0
    for interval in intervals:
        root = args.results / interval
        summary = load(root / "summary.json")
        payload = load(root / "events.json")
        manifest = load(root / "data_manifest.json")
        if manifest.get("schema") in DATA_SCHEMAS:
            manifests += 1
        interval_events = payload.get("events", [])
        if not isinstance(interval_events, list):
            raise TypeError(f"{root / 'events.json'} events must be a list")
        events.extend(interval_events)
        weeks.append(summary)

    realized = [float(event["realized_r"]) for event in events if event.get("realized_r") is not None]
    target_first = sum(event.get("outcome") == "TARGET_FIRST" for event in events)
    per_week_events = {week["interval"]: int(week["event_count"]) for week in weeks}
    positive_mean_weeks = sum(
        week.get("mean_realized_r_diagnostic") is not None
        and float(week["mean_realized_r_diagnostic"]) > 0.0
        for week in weeks
    )
    total_events = len(events)
    target_rate = target_first / total_events if total_events else 0.0
    pooled_mean = mean(realized) if realized else None
    gate = protocol["advance_gate"]
    checks = {
        "minimum_total_events": total_events >= int(gate["minimum_total_events"]),
        "minimum_events_per_week": all(
            count >= int(gate["minimum_events_per_week"])
            for count in per_week_events.values()
        ),
        "minimum_pooled_target_first_rate": target_rate >= float(gate["minimum_pooled_target_first_rate"]),
        "minimum_pooled_mean_realized_r_diagnostic": (
            pooled_mean is not None
            and pooled_mean >= float(gate["minimum_pooled_mean_realized_r_diagnostic"])
        ),
        "minimum_positive_mean_weeks": positive_mean_weeks >= int(gate["minimum_positive_mean_weeks"]),
        "require_all_three_data_manifests": manifests == len(intervals),
    }
    passed = all(checks.values())
    result = {
        "schema": "candidate-11-cross-asset-gap-aggregate-v1",
        "candidate": protocol["candidate"],
        "classification": (
            "CROSS_ASSET_GAP_MECHANISM_ADVANCE"
            if passed
            else "CROSS_ASSET_GAP_MECHANISM_REJECT"
        ),
        "diagnostic_gate_passed": passed,
        "success_claim": False,
        "account_return_claim": False,
        "intervals": intervals,
        "total_events": total_events,
        "events_per_week": per_week_events,
        "target_first": target_first,
        "stop_first": sum(
            event.get("outcome") in {"STOP_FIRST", "BOTH_STOP_FIRST"}
            for event in events
        ),
        "timeout": sum(event.get("outcome") == "TIMEOUT" for event in events),
        "pooled_target_first_rate": target_rate,
        "pooled_mean_realized_r_diagnostic": pooled_mean,
        "pooled_median_realized_r_diagnostic": (
            sorted(realized)[len(realized) // 2] if realized else None
        ),
        "positive_mean_weeks": positive_mean_weeks,
        "checks": checks,
        "weeks": weeks,
        "decision": protocol["decision_rule"]["pass"] if passed else protocol["decision_rule"]["fail"],
    }
    write(args.output, result)

    lines = [
        "# Candidate 11 second-scale cross-asset gap diagnostic",
        "",
        f"**{result['classification']}**",
        "",
        f"- diagnostic_gate_passed: `{passed}`",
        f"- total_events: `{total_events}`",
        f"- target / stop / timeout: `{result['target_first']} / {result['stop_first']} / {result['timeout']}`",
        f"- pooled_target_first_rate: `{target_rate:.6f}`",
        f"- pooled_mean_realized_r_diagnostic: `{pooled_mean}`",
        f"- positive_mean_weeks: `{positive_mean_weeks}`",
        "",
        "## Precommitted checks",
    ]
    lines.extend(f"- {name}: `{passed_value}`" for name, passed_value in checks.items())
    lines.extend(("", "## Weekly mechanism evidence"))
    for week in weeks:
        lines.append(
            f"- {week['interval']}: events={week['event_count']}, "
            f"target_rate={week['target_first_rate']:.6f}, "
            f"mean_R={week['mean_realized_r_diagnostic']}, "
            f"followers={week['follower_counts']}"
        )
    lines.extend(("", "## Decision", result["decision"]))
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
