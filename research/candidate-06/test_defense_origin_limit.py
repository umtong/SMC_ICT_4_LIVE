#!/usr/bin/env python3
"""Pure causality and state-contract checks for structural entry placement."""

from __future__ import annotations

from math import isclose
from types import SimpleNamespace

from defense_origin_limit import _next_interval_boundary_ns, resolve_entry_placement


MINUTE = 60 * 1_000_000_000


def _signal(
    direction: str,
    target: float,
    *,
    family: str = "SAC",
    reference: float = 100.0,
    liquidity: float = 100.0,
):
    return SimpleNamespace(
        family=family,
        direction=direction,
        target_price=target,
        reference_entry=reference,
        liquidity_level=liquidity,
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
        {
            "sac_entry_execution": "DEFENSE_ORIGIN_LIMIT",
            "auction_period_minutes": 30,
        },
        confirmation_passed=True,
        trap_armed=False,
    )
    assert placement.reason is None, placement
    assert placement.order_type == "LIMIT"
    assert placement.expected_entry == 100.0
    assert placement.expiry_ts_ns == (16 * 60 + 30) * MINUTE
    assert placement.details["source_interval_ts_ns"] == (16 * 60 + 5) * MINUTE
    assert placement.details["remaining_seconds"] == 24 * 60

    boundary_snapshot = _snapshot(
        boundary_ts_ns,
        100.0,
        106.0,
        99.0,
        105.0,
    )
    boundary_placement = resolve_entry_placement(
        original,
        original,
        boundary_snapshot,
        {
            "sac_entry_execution": "DEFENSE_ORIGIN_LIMIT",
            "auction_period_minutes": 30,
        },
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
        {
            "sac_entry_execution": "DEFENSE_ORIGIN_LIMIT",
            "auction_period_minutes": 30,
        },
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
        {
            "sac_entry_execution": "DEFENSE_ORIGIN_LIMIT",
            "auction_period_minutes": 30,
        },
        confirmation_passed=True,
        trap_armed=False,
    )
    assert placement.reason == "DEFENSE_BAR_OBJECTIVE_ALREADY_TOUCHED"

    market = resolve_entry_placement(
        original,
        original,
        snapshot,
        {
            "sac_entry_execution": "MARKET_AFTER_DEFENSE",
            "auction_period_minutes": 30,
        },
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
        {
            "sac_entry_execution": "DEFENSE_ORIGIN_LIMIT",
            "auction_period_minutes": 30,
        },
        confirmation_passed=False,
        trap_armed=False,
    )
    assert failed.order_type == "MARKET"

    trap = SimpleNamespace(
        family="FAT",
        direction="SHORT",
        target_price=80.0,
    )
    failed_trap = resolve_entry_placement(
        original,
        trap,
        snapshot,
        {
            "sac_entry_execution": "DEFENSE_ORIGIN_LIMIT",
            "auction_period_minutes": 30,
        },
        confirmation_passed=False,
        trap_armed=True,
    )
    assert failed_trap.order_type == "MARKET"

    # LCOR_RF short: the completed second failure closes below the already-known
    # failed ownership boundary. The exact midpoint is a passive sell limit and
    # expires at the end of the same 15-minute source auction.
    lcor_short = _signal(
        "SHORT",
        64107.7,
        family="LCOR_RF",
        reference=64321.6,
        liquidity=64357.7,
    )
    lcor_ts = (20 * 60 + 14) * MINUTE
    lcor_snapshot = _snapshot(
        lcor_ts,
        64359.3,
        64373.5,
        64300.0,
        64321.6,
    )
    lcor_placement = resolve_entry_placement(
        lcor_short,
        lcor_short,
        lcor_snapshot,
        {
            "lcor_reaccept_failure_entry_execution": (
                "FAILED_BOUNDARY_HALF_BACK_LIMIT"
            ),
            "ciot_auction_period_minutes": 15,
        },
        confirmation_passed=True,
        trap_armed=False,
    )
    assert lcor_placement.reason is None, lcor_placement
    assert lcor_placement.order_type == "LIMIT"
    assert lcor_placement.mode == "FAILED_BOUNDARY_HALF_BACK_LIMIT"
    assert isclose(lcor_placement.expected_entry, 64339.65)
    assert lcor_placement.expected_entry > lcor_snapshot.observation.close
    assert lcor_placement.expiry_ts_ns == (20 * 60 + 15) * MINUTE
    assert lcor_placement.details["remaining_seconds"] == 60.0
    assert lcor_placement.details["boundary_is_favorable"] is True
    assert lcor_placement.details["limit_is_passive_at_submission"] is True

    # The mirrored long contract places its passive bid halfway back to a
    # lower failed boundary.
    lcor_long = _signal(
        "LONG",
        102.0,
        family="LCOR_RF",
        reference=100.0,
        liquidity=99.0,
    )
    lcor_long_snapshot = _snapshot(
        ts_ns,
        99.5,
        100.2,
        99.7,
        100.0,
    )
    lcor_long_placement = resolve_entry_placement(
        lcor_long,
        lcor_long,
        lcor_long_snapshot,
        {
            "lcor_reaccept_failure_entry_execution": (
                "FAILED_BOUNDARY_HALF_BACK_LIMIT"
            ),
            "ciot_auction_period_minutes": 15,
        },
        confirmation_passed=True,
        trap_armed=False,
    )
    assert lcor_long_placement.reason is None
    assert lcor_long_placement.expected_entry == 99.5
    assert lcor_long_placement.expected_entry < lcor_long_snapshot.observation.close

    # A boundary on the wrong side is rejected rather than silently converted
    # into an aggressive or marketable limit.
    wrong_side = _signal(
        "SHORT",
        90.0,
        family="LCOR_RF",
        reference=100.0,
        liquidity=99.0,
    )
    wrong_side_placement = resolve_entry_placement(
        wrong_side,
        wrong_side,
        _snapshot(ts_ns, 100.5, 101.0, 99.5, 100.0),
        {
            "lcor_reaccept_failure_entry_execution": (
                "FAILED_BOUNDARY_HALF_BACK_LIMIT"
            ),
            "ciot_auction_period_minutes": 15,
        },
        confirmation_passed=True,
        trap_armed=False,
    )
    assert wrong_side_placement.reason == (
        "FAILED_BOUNDARY_NOT_ON_FAVORABLE_ENTRY_SIDE"
    )

    print("structural entry placement tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
