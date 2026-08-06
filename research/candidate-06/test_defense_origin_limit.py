#!/usr/bin/env python3
"""Pure causality and state-contract checks for ADOM entry placement."""

from __future__ import annotations

from types import SimpleNamespace

from defense_origin_limit import _next_interval_boundary_ns, resolve_entry_placement


MINUTE = 60 * 1_000_000_000


def _signal(direction: str, target: float):
    return SimpleNamespace(
        family="SAC",
        direction=direction,
        target_price=target,
    )


def _snapshot(ts_ns: int, open_: float, high: float, low: float, close: float):
    return SimpleNamespace(
        observation=SimpleNamespace(
            ts_ns=ts_ns,
            open=open_,
            high=high,
            low=low,
            close=close,
        ),
    )


def main() -> int:
    ts_ns = (16 * 60 + 6) * MINUTE
    assert _next_interval_boundary_ns(ts_ns, 30) == (16 * 60 + 30) * MINUTE

    # A completed bar observed exactly at 16:30 belongs to the source
    # interval beginning 16:29 and therefore has no causal order lifetime
    # beyond the 16:30 auction boundary.
    boundary_ts_ns = (16 * 60 + 30) * MINUTE
    assert _next_interval_boundary_ns(boundary_ts_ns, 30) == boundary_ts_ns
    assert _next_interval_boundary_ns(boundary_ts_ns + MINUTE, 30) == (17 * 60) * MINUTE

    original = _signal("LONG", 120.0)
    snapshot = _snapshot(ts_ns, 100.0, 106.0, 99.0, 105.0)
    placement = resolve_entry_placement(
        original,
        original,
        snapshot,
        {"sac_entry_execution": "DEFENSE_ORIGIN_LIMIT", "auction_period_minutes": 30},
        confirmation_passed=True,
        trap_armed=False,
    )
    assert placement.reason is None, placement
    assert placement.order_type == "LIMIT"
    assert placement.expected_entry == 100.0
    assert placement.expiry_ts_ns == (16 * 60 + 30) * MINUTE
    assert placement.details["source_interval_ts_ns"] == (16 * 60 + 5) * MINUTE
    assert placement.details["remaining_seconds"] == 24 * 60

    boundary_snapshot = _snapshot(boundary_ts_ns, 100.0, 106.0, 99.0, 105.0)
    boundary_placement = resolve_entry_placement(
        original,
        original,
        boundary_snapshot,
        {"sac_entry_execution": "DEFENSE_ORIGIN_LIMIT", "auction_period_minutes": 30},
        confirmation_passed=True,
        trap_armed=False,
    )
    assert boundary_placement.expiry_ts_ns == boundary_ts_ns
    assert boundary_placement.details["remaining_seconds"] == 0.0
    assert (
        boundary_placement.reason
        == "DEFENSE_ORIGIN_ENTRY_HAS_NO_CAUSAL_LIFETIME"
    )

    short = _signal("SHORT", 80.0)
    snapshot = _snapshot(ts_ns, 100.0, 101.0, 94.0, 95.0)
    placement = resolve_entry_placement(
        short,
        short,
        snapshot,
        {"sac_entry_execution": "DEFENSE_ORIGIN_LIMIT", "auction_period_minutes": 30},
        confirmation_passed=True,
        trap_armed=False,
    )
    assert placement.reason is None, placement
    assert placement.expected_entry == 100.0

    touched = _snapshot(ts_ns, 100.0, 121.0, 99.0, 105.0)
    placement = resolve_entry_placement(
        original,
        original,
        touched,
        {"sac_entry_execution": "DEFENSE_ORIGIN_LIMIT", "auction_period_minutes": 30},
        confirmation_passed=True,
        trap_armed=False,
    )
    assert placement.reason == "DEFENSE_BAR_OBJECTIVE_ALREADY_TOUCHED"

    market = resolve_entry_placement(
        original,
        original,
        snapshot,
        {"sac_entry_execution": "MARKET_AFTER_DEFENSE", "auction_period_minutes": 30},
        confirmation_passed=True,
        trap_armed=False,
    )
    assert market.order_type == "MARKET"
    assert market.expected_entry == snapshot.observation.close
    assert market.expiry_ts_ns is None

    failed = resolve_entry_placement(
        original,
        original,
        snapshot,
        {"sac_entry_execution": "DEFENSE_ORIGIN_LIMIT", "auction_period_minutes": 30},
        confirmation_passed=False,
        trap_armed=False,
    )
    assert failed.order_type == "MARKET"

    trap = SimpleNamespace(family="FAT", direction="SHORT", target_price=80.0)
    failed_trap = resolve_entry_placement(
        original,
        trap,
        snapshot,
        {"sac_entry_execution": "DEFENSE_ORIGIN_LIMIT", "auction_period_minutes": 30},
        confirmation_passed=False,
        trap_armed=True,
    )
    assert failed_trap.order_type == "MARKET"

    print("defense-origin entry placement tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
