#!/usr/bin/env python3
"""Verify that the frozen v36 protocol and executable constants are identical."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path

from cross_market_failed_auction_v36 import (
    BALANCE_MINUTES,
    CONFIRMATION_MINUTES,
    COST_PER_SIDE,
    MIN_STRUCTURE_WIDTH_FRACTION,
    MIN_SWEEP_FRACTION,
)

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "candidate_01_v36_protocol.json"


def selected_weeks(protocol: dict[str, object]) -> list[str]:
    spec = protocol["week_selection"]
    assert isinstance(spec, dict)
    seed = str(spec["seed"])
    excluded = {date.fromisoformat(value) for value in spec["excluded_recent_candidate_weeks"]}
    current = date(2020, 1, 6)
    final = date(2025, 12, 29)
    ranked: list[tuple[str, str]] = []
    while current <= final:
        if current not in excluded:
            value = current.isoformat()
            digest = sha256(f"{seed}|{value}".encode()).hexdigest()
            ranked.append((digest, value))
        current += timedelta(days=7)
    return [value for _, value in sorted(ranked)[:3]]


def verify() -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    frozen = protocol["frozen_parameters"]
    assert int(protocol["candidate_version"]) == 36
    assert int(frozen["balance_minutes"]) == BALANCE_MINUTES
    assert int(frozen["confirmation_minutes"]) == CONFIRMATION_MINUTES
    assert float(frozen["minimum_structure_width_fraction"]) == (
        MIN_STRUCTURE_WIDTH_FRACTION
    )
    assert float(frozen["minimum_sweep_fraction"]) == MIN_SWEEP_FRACTION
    assert float(frozen["all_in_cost_bps_per_side"]) == COST_PER_SIDE * 10_000
    assert float(frozen["risk_fraction_current_nav"]) == 0.03
    assert int(frozen["global_pending_entry_plus_position_limit"]) == 1
    expected = selected_weeks(protocol)
    actual = protocol["week_selection"]["frozen_weeks"]
    assert actual == expected, (actual, expected)
    assert protocol["primary"]["rule"] == "spot-unconfirmed-primary"
    assert protocol["single_ablation"]["rule"] == "futures-failure-control"
    print(json.dumps({"candidate_version": 36, "frozen_weeks": actual}, indent=2))


if __name__ == "__main__":
    verify()
