#!/usr/bin/env python3
"""Verify frozen v37 protocol constants and deterministic weeks."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path

from quarter_hour_auction_v37 import (
    BALANCE_MINUTES,
    COST_PER_SIDE,
    LIQUIDITY_LOOKBACK_MINUTES,
    MIN_BALANCE_WIDTH_FRACTION,
    SWING_RADIUS,
    TEN_SECONDS_NS,
)

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "candidate_01_v37_protocol.json"


def selected(protocol: dict[str, object]) -> list[str]:
    spec = protocol["week_selection"]
    assert isinstance(spec, dict)
    excluded = {
        date.fromisoformat(value)
        for value in spec["excluded_recent_candidate_weeks"]
    }
    current = date(2020, 1, 6)
    final = date(2025, 12, 29)
    ranked: list[tuple[str, str]] = []
    while current <= final:
        if current not in excluded:
            value = current.isoformat()
            ranked.append(
                (
                    sha256(
                        f"{spec['seed']}|{value}".encode(),
                    ).hexdigest(),
                    value,
                ),
            )
        current += timedelta(days=7)
    return [value for _, value in sorted(ranked)[:3]]


def verify() -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    frozen = protocol["frozen_parameters"]
    assert int(protocol["candidate_version"]) == 37
    assert int(frozen["balance_minutes"]) == BALANCE_MINUTES
    assert int(frozen["impulse_seconds"]) == TEN_SECONDS_NS // 1_000_000_000
    assert float(frozen["minimum_balance_width_fraction"]) == (
        MIN_BALANCE_WIDTH_FRACTION
    )
    assert float(frozen["minimum_displacement_fraction"]) == COST_PER_SIDE
    assert int(frozen["liquidity_swing_radius_bars"]) == SWING_RADIUS
    assert int(frozen["liquidity_lookback_hours"]) == (
        LIQUIDITY_LOOKBACK_MINUTES // 60
    )
    assert float(frozen["risk_fraction_current_nav"]) == 0.03
    assert float(frozen["all_in_cost_bps_per_side"]) == 7.0
    expected = selected(protocol)
    assert protocol["week_selection"]["frozen_weeks"] == expected
    print(json.dumps({"candidate_version": 37, "frozen_weeks": expected}, indent=2))


if __name__ == "__main__":
    verify()
