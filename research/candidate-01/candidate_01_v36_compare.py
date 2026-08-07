#!/usr/bin/env python3
"""Compare v36 primary and its one-variable futures-only control."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

IMMUTABLE_EVIDENCE = (
    "joint_minute_bars.csv",
    "cross_market_sweep_events.csv",
    "cross_market_diagnostics.csv",
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


def pf(gate: dict[str, Any]) -> float:
    return float(gate["profit_factor_gate_value"])


def compare(primary_root: Path, control_root: Path, output: Path) -> dict[str, Any]:
    primary = load(primary_root / "week_gate.json")
    control = load(control_root / "week_gate.json")
    primary_summary = load(
        primary_root / "cross_market_failed_auction_v36_summary.json",
    )
    control_summary = load(
        control_root / "cross_market_failed_auction_v36_summary.json",
    )

    assert int(primary["candidate_version"]) == 36
    assert int(control["candidate_version"]) == 36
    assert primary["week"] == control["week"]
    assert primary["rule"] == "spot-unconfirmed-primary"
    assert control["rule"] == "futures-failure-control"
    assert primary_summary["evaluation_start_utc"] == control_summary[
        "evaluation_start_utc"
    ]
    assert primary_summary["evaluation_end_utc"] == control_summary[
        "evaluation_end_utc"
    ]

    parity: dict[str, bool] = {}
    hashes: dict[str, dict[str, str]] = {}
    for name in IMMUTABLE_EVIDENCE:
        primary_hash = digest(primary_root / name)
        control_hash = digest(control_root / name)
        parity[name] = primary_hash == control_hash
        hashes[name] = {
            "primary": primary_hash,
            "control": control_hash,
        }
    immutable_parity = all(parity.values())
    assert immutable_parity, parity

    primary_plans = int(primary["selected_plan_count"])
    control_plans = int(control["selected_plan_count"])
    excluded_events = control_plans - primary_plans
    assert excluded_events >= 0

    metrics_strictly_better = {
        "win_rate": float(primary["win_rate"]) > float(control["win_rate"]),
        "total_return": float(primary["total_return"]) > float(control["total_return"]),
        "geometric_daily": float(primary["geometric_daily"]) > float(
            control["geometric_daily"],
        ),
        "profit_factor": pf(primary) > pf(control),
        "max_drawdown": float(primary["max_drawdown"]) > float(
            control["max_drawdown"],
        ),
    }
    spot_nonconfirmation_discriminates = (
        excluded_events > 0
        and int(primary["trades"]) >= 7
        and all(metrics_strictly_better.values())
    )
    advance = bool(primary["advance"]) and spot_nonconfirmation_discriminates
    decision = (
        "advance_to_frozen_week_2"
        if advance
        else "stop_primary_passed_but_ablation_not_discriminating"
        if bool(primary["advance"])
        else "stop_first_week_gate_failed"
    )
    payload = {
        "candidate_version": 36,
        "week": primary["week"],
        "immutable_primary_control_evidence_parity": immutable_parity,
        "immutable_evidence_parity": parity,
        "immutable_evidence_sha256": hashes,
        "primary_selected_plans": primary_plans,
        "control_selected_plans": control_plans,
        "spot_confirmed_control_only_events": excluded_events,
        "primary_trades": int(primary["trades"]),
        "control_trades": int(control["trades"]),
        "primary_classification": primary["classification"],
        "control_classification": control["classification"],
        "strict_metric_improvements": metrics_strictly_better,
        "spot_nonconfirmation_discriminates": spot_nonconfirmation_discriminates,
        "advance": advance,
        "decision": decision,
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
