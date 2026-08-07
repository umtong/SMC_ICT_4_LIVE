"""Causal per-unit execution geometry shared by candidate-07 scenarios.

This module contains no signal detector, order submission, fill simulation, PnL
ledger or NAV logic. It answers one pre-trade question using only prices and
cost assumptions already known at submission: can the declared structural
target still produce a positive result after the same adverse execution costs
used by risk sizing?
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


_BPS_DENOMINATOR = Decimal("10000")


@dataclass(frozen=True, slots=True)
class AdverseExecutionGeometry:
    """Per-unit loss and target outcomes under adverse execution assumptions."""

    expected_entry_fill: Decimal
    expected_stop_fill: Decimal
    expected_target_fill: Decimal
    entry_fee: Decimal
    stop_fee: Decimal
    target_fee: Decimal
    funding_reserve: Decimal
    per_unit_expected_loss: Decimal
    per_unit_expected_target_gain: Decimal
    cost_adjusted_target_r: Decimal

    @property
    def target_is_net_positive(self) -> bool:
        return self.per_unit_expected_target_gain > 0


def adverse_execution_geometry(
    *,
    direction: str,
    entry_reference: Decimal,
    stop_price: Decimal,
    target_price: Decimal,
    price_increment: Decimal,
    taker_fee_rate: Decimal,
    funding_reserve_bps: Decimal,
    adverse_slippage_ticks: int = 1,
) -> AdverseExecutionGeometry:
    """Return causal cost geometry for one unit of a proposed position.

    The entry, stop and target each receive the configured number of adverse
    ticks. Entry and exit taker fees are charged on their expected fills. The
    same funding reserve used by loss-budget sizing is included on both the
    stop and target branches, so a target is accepted only when it is positive
    under the strategy's already-declared conservative cost contract.
    """
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported direction: {direction}")
    if entry_reference <= 0 or stop_price <= 0 or target_price <= 0:
        raise ValueError("prices must be positive")
    if price_increment <= 0:
        raise ValueError("price_increment must be positive")
    if taker_fee_rate < 0 or taker_fee_rate >= 1:
        raise ValueError("taker_fee_rate must be in [0, 1)")
    if funding_reserve_bps < 0:
        raise ValueError("funding_reserve_bps must be non-negative")
    if adverse_slippage_ticks < 0:
        raise ValueError("adverse_slippage_ticks must be non-negative")

    adverse = price_increment * Decimal(adverse_slippage_ticks)
    if direction == "LONG":
        expected_entry = entry_reference + adverse
        expected_stop = stop_price - adverse
        expected_target = target_price - adverse
        stop_distance = expected_entry - expected_stop
        target_distance = expected_target - expected_entry
    else:
        expected_entry = entry_reference - adverse
        expected_stop = stop_price + adverse
        expected_target = target_price + adverse
        stop_distance = expected_stop - expected_entry
        target_distance = expected_entry - expected_target

    if expected_entry <= 0 or expected_stop <= 0 or expected_target <= 0:
        raise ValueError("adverse expected fills must remain positive")
    if stop_distance <= 0:
        raise ValueError("stop is on the wrong side of expected entry")

    entry_fee = expected_entry * taker_fee_rate
    stop_fee = expected_stop * taker_fee_rate
    target_fee = expected_target * taker_fee_rate
    funding_reserve = expected_entry * funding_reserve_bps / _BPS_DENOMINATOR
    expected_loss = stop_distance + entry_fee + stop_fee + funding_reserve
    if expected_loss <= 0:
        raise ValueError("per-unit expected loss must be positive")

    expected_target_gain = (
        target_distance - entry_fee - target_fee - funding_reserve
    )
    return AdverseExecutionGeometry(
        expected_entry_fill=expected_entry,
        expected_stop_fill=expected_stop,
        expected_target_fill=expected_target,
        entry_fee=entry_fee,
        stop_fee=stop_fee,
        target_fee=target_fee,
        funding_reserve=funding_reserve,
        per_unit_expected_loss=expected_loss,
        per_unit_expected_target_gain=expected_target_gain,
        cost_adjusted_target_r=expected_target_gain / expected_loss,
    )


__all__ = ["AdverseExecutionGeometry", "adverse_execution_geometry"]
