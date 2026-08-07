#!/usr/bin/env python3
"""Compare v37 quarter-hour primary with ordinary five-minute ablation."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

SHARED = (
    "clock_minute_bars.csv",
    "five_minute_bars.csv",
    "clock_auction_patterns.csv",
    "clock_auction_diagnostics.csv",
    "primary_plans.csv",
    "control_plans.csv",
)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def profit_factor(gate: dict[str, Any]) -> float:
    return float(gate["profit_factor_gate_value"])


def compare(primary_root: Path, control_root: Path, output: Path) -> dict[str, Any]:
    primary = load(primary_root / "week_gate.json")
    control = load(control_root / "week_gate.json")
    assert primary["week"] == control["week"]
    assert primary["rule"] == "quarter-hour-clock-primary"
    assert control["rule"] == "ordinary-five-minute-clock-control"

    hashes = {
        name: {
            "primary": digest(primary_root / name),
            "control": digest(control_root / name),
        }
        for name in SHARED
    }
    parity = {
        name: values["primary"] == values["control"]
        for name, values in hashes.items()
    }
    assert all(parity.values()), parity

    better = {
        "win_rate": float(primary["win_rate"]) > float(control["win_rate"]),
        "total_return": float(primary["total_return"])
        > float(control["total_return"]),
        "geometric_daily": float(primary["geometric_daily"])
        > float(control["geometric_daily"]),
        "profit_factor": profit_factor(primary) > profit_factor(control),
        "max_drawdown": float(primary["max_drawdown"])
        > float(control["max_drawdown"]),
    }
    discriminates = int(primary["trades"]) >= 7 and all(better.values())
    advance = bool(primary["advance"]) and discriminates
    payload = {
        "candidate_version": 37,
        "week": primary["week"],
        "immutable_evidence_parity": parity,
        "immutable_evidence_sha256": hashes,
        "primary_classification": primary["classification"],
        "control_classification": control["classification"],
        "primary_plans": int(primary["selected_plan_count"]),
        "control_plans": int(control["selected_plan_count"]),
        "primary_trades": int(primary["trades"]),
        "control_trades": int(control["trades"]),
        "strict_metric_improvements": better,
        "quarter_hour_clock_discriminates": discriminates,
        "advance": advance,
        "decision": (
            "advance_to_frozen_week_2"
            if advance
            else "stop_primary_passed_but_clock_not_discriminating"
            if bool(primary["advance"])
            else "stop_first_week_gate_failed"
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-root", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    compare(arguments.primary_root, arguments.control_root, arguments.output)
