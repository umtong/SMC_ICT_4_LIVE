#!/usr/bin/env python3
"""Contracts for tick-aware Nautilus close classification."""

from __future__ import annotations

from nautilus_outcome_classification import classify_position_outcome


# Exact W2 LCOR half-back target close. Binary float representation is a hair
# above target + one tick, but it is the native target order fill and must not
# become OTHER_EXIT.
assert classify_position_outcome(
    direction="SHORT",
    close_price=64107.80000000001,
    target_price=64107.7,
    stop_price=64457.4,
    tick=0.1,
) == "TARGET"

# Values meaningfully beyond the one-tick execution allowance remain unrelated.
assert classify_position_outcome(
    direction="SHORT",
    close_price=64107.80001,
    target_price=64107.7,
    stop_price=64457.4,
    tick=0.1,
) == "OTHER_EXIT"

assert classify_position_outcome(
    direction="LONG",
    close_price=120.0 - 0.1 - 1e-11,
    target_price=120.0,
    stop_price=95.0,
    tick=0.1,
) == "TARGET"
assert classify_position_outcome(
    direction="LONG",
    close_price=95.0 + 0.1 + 1e-11,
    target_price=120.0,
    stop_price=95.0,
    tick=0.1,
) == "STOP"
assert classify_position_outcome(
    direction="SHORT",
    close_price=105.0 - 0.1 - 1e-11,
    target_price=80.0,
    stop_price=105.0,
    tick=0.1,
) == "STOP"
assert classify_position_outcome(
    direction="LONG",
    close_price=100.0,
    target_price=120.0,
    stop_price=95.0,
    tick=0.1,
    forced_exit_reason="TIMEOUT",
) == "TIMEOUT"

try:
    classify_position_outcome(
        direction="FLAT",
        close_price=100.0,
        target_price=120.0,
        stop_price=95.0,
        tick=0.1,
    )
except ValueError:
    pass
else:
    raise AssertionError("unsupported direction must fail closed")

print("tick-aware Nautilus outcome classification verified")
