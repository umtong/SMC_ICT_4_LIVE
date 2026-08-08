#!/usr/bin/env python3
"""Causal contract tests for CIRB discharge half-back placement."""

from __future__ import annotations

from math import isclose
from types import SimpleNamespace

from defense_origin_limit import (
    BAR_BOUNDARY_GTD_ENCODING_NS,
    resolve_entry_placement,
)


MINUTE_NS = 60 * 1_000_000_000


def _signal(
    direction: str,
    reference: float,
    boundary: float,
    target: float,
    *,
    family: str = "CIRB_D_R",
):
    return SimpleNamespace(
        family=family,
        direction=direction,
        reference_entry=reference,
        liquidity_level=boundary,
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


def _resolve(signal, snapshot, mode="CROWD_DISCHARGE_HALF_BACK_LIMIT"):
    return resolve_entry_placement(
        signal,
        signal,
        snapshot,
        {
            "cirb_discharge_reversal_entry_execution": mode,
            "cirb_entry_auction_period_minutes": 5,
            "sac_entry_execution": "MARKET_AFTER_DEFENSE",
        },
        confirmation_passed=True,
        trap_armed=False,
    )


def main() -> int:
    # Real W2 geometry: BUY-side discharge is exhausted, so the reversal is
    # SHORT. The already-observed wave extreme is above the completed reclaim
    # close and therefore defines a passive half-back sell limit.
    event_boundary = 63277.1
    signal_close = 63234.2
    wave_extreme = 63319.9
    signal_ts = event_boundary * 0  # keep the price constants visibly separate
    del signal_ts
    decision_ts_ns = 1727103060000000000
    short = _signal("SHORT", signal_close, wave_extreme, 63039.6)
    short_snapshot = _snapshot(
        decision_ts_ns,
        63277.1,
        63277.1,
        63216.0,
        signal_close,
    )
    placement = _resolve(short, short_snapshot)
    assert placement.reason is None, placement
    assert placement.mode == "CROWD_DISCHARGE_HALF_BACK_LIMIT"
    assert placement.order_type == "LIMIT"
    assert isclose(placement.expected_entry, 63277.05)
    assert placement.expected_entry > signal_close
    assert placement.details["boundary_is_favorable"] is True
    assert placement.details["limit_is_passive_at_submission"] is True
    assert placement.details["remaining_seconds"] == 240.0
    assert placement.expiry_ts_ns == (
        1727103300000000000 + BAR_BOUNDARY_GTD_ENCODING_NS
    )

    # Mirrored SELL-side discharge exhaustion produces a LONG reversal and a
    # passive bid halfway back to the completed wave low.
    long = _signal("LONG", 66200.0, 66158.4, 66364.8)
    long_snapshot = _snapshot(
        1727451960000000000,
        66170.2,
        66202.0,
        66170.1,
        66200.0,
    )
    placement = _resolve(long, long_snapshot)
    assert placement.reason is None, placement
    assert placement.order_type == "LIMIT"
    assert isclose(placement.expected_entry, 66179.2)
    assert placement.expected_entry < 66200.0

    # The causal boundary must be on the favorable side. A wrong-side level is
    # rejected rather than silently converted into an aggressive order.
    wrong = _signal("SHORT", 100.0, 99.0, 90.0)
    wrong_snapshot = _snapshot(16 * 60 * MINUTE_NS + 6 * MINUTE_NS, 100.5, 101.0, 99.5, 100.0)
    placement = _resolve(wrong, wrong_snapshot)
    assert placement.reason == "CROWD_DISCHARGE_EXTREME_NOT_ON_FAVORABLE_ENTRY_SIDE"

    # The decision bar may confirm the reversal, but if it has already reached
    # the objective the setup is no longer an unconsumed auction leg.
    touched = _signal("SHORT", 100.0, 102.0, 97.0)
    touched_snapshot = _snapshot(16 * 60 * MINUTE_NS + 6 * MINUTE_NS, 101.0, 102.0, 96.5, 100.0)
    placement = _resolve(touched, touched_snapshot)
    assert placement.reason == "CROWD_DISCHARGE_SIGNAL_BAR_OBJECTIVE_ALREADY_TOUCHED"

    # Other CIRB families and the baseline mode preserve the existing market
    # path; the new mechanic cannot alter continuation or counter-inventory.
    continuation = _signal("SHORT", 100.0, 102.0, 90.0, family="CIRB_D_C")
    market = _resolve(continuation, wrong_snapshot)
    assert market.order_type == "MARKET"
    baseline = _resolve(short, short_snapshot, mode="MARKET_ON_RESPONSE_CLOSE")
    assert baseline.order_type == "MARKET"
    assert baseline.expected_entry == short_snapshot.observation.close

    print("CIRB discharge half-back causal tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
