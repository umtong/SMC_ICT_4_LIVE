"""v51 size-dependent all-cost reward feasibility certificate.

The v27 risk sizer correctly solves quantity after adding size-dependent impact
to the stop-loss path, and the live ledger debits that same impact on every real
fill.  Until v51, however, the pre-order structural-R certificate still used the
pre-impact target gain.  This module closes that accounting gap by subtracting
entry and target impact from gain and comparing it with the exact impact-aware
loss-per-unit produced by the existing risk solution.

No alpha threshold is added.  The existing ``LogicConfig.min_net_r`` is reused.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from typing import Any

from c10_v49_overlay import *  # noqa: F403 frozen lower-layer re-export
from c10_v49_overlay import __all__ as _LOWER_ALL


@dataclass(frozen=True, slots=True)
class SizeDependentRewardDecision:
    approved: bool
    reason: str
    impact_adjusted_gain_per_unit: Decimal
    impact_adjusted_loss_per_unit: Decimal
    impact_adjusted_net_r: Decimal
    details: dict[str, Any]


def size_dependent_reward_certificate_enabled() -> bool:
    return os.environ.get("C10_V51_SIZE_DEPENDENT_REWARD", "0") == "1"


def size_dependent_reward_certificate(
    plan: Any,
    solution: Any,
    *,
    minimum_net_r: float,
) -> SizeDependentRewardDecision:
    """Certify target economics at the quantity the risk solver would submit."""

    original_gain = Decimal(str(plan.gain_per_unit))
    original_loss = Decimal(str(plan.loss_per_unit))
    impact_per_side = Decimal(str(solution.impact_per_side))
    impact_adjusted_loss = Decimal(str(solution.per_unit_loss))
    threshold = Decimal(str(minimum_net_r))
    if (
        original_gain <= 0
        or original_loss <= 0
        or impact_per_side < 0
        or impact_adjusted_loss <= 0
        or threshold <= 0
    ):
        raise ValueError("invalid size-dependent reward certificate inputs")

    impact_adjusted_gain = original_gain - Decimal("2") * impact_per_side
    net_r = impact_adjusted_gain / impact_adjusted_loss
    enabled = size_dependent_reward_certificate_enabled()
    approved = (
        True
        if not enabled
        else impact_adjusted_gain > 0 and net_r >= threshold
    )
    if not enabled:
        reason = "SIZE_DEPENDENT_REWARD_CERTIFICATE_DISABLED"
    elif impact_adjusted_gain <= 0:
        reason = "NONPOSITIVE_SIZE_DEPENDENT_ALL_COST_GAIN"
    elif net_r < threshold:
        reason = "INSUFFICIENT_SIZE_DEPENDENT_ALL_COST_R"
    else:
        reason = "SIZE_DEPENDENT_ALL_COST_R_CONFIRMED"

    details = {
        "schema": "candidate-10-v51-size-dependent-all-cost-reward-v1",
        "enabled": enabled,
        "original_plan_gain_per_unit": str(original_gain),
        "original_plan_loss_per_unit": str(original_loss),
        "impact_per_side": str(impact_per_side),
        "entry_plus_target_impact_per_unit": str(
            Decimal("2") * impact_per_side
        ),
        "impact_adjusted_gain_per_unit": str(impact_adjusted_gain),
        "impact_adjusted_loss_per_unit": str(impact_adjusted_loss),
        "impact_adjusted_net_r": str(net_r),
        "minimum_existing_net_r": str(threshold),
        "quantity": str(solution.quantity),
        "participation": str(solution.participation),
        "liquidity_notional": str(solution.liquidity_notional),
        "atr": str(solution.atr),
        "cost_contract": (
            "the same size-dependent impact debited by the live ledger is "
            "charged on both entry and target before order submission"
        ),
        "new_fitted_thresholds": [],
    }
    return SizeDependentRewardDecision(
        approved=approved,
        reason=reason,
        impact_adjusted_gain_per_unit=impact_adjusted_gain,
        impact_adjusted_loss_per_unit=impact_adjusted_loss,
        impact_adjusted_net_r=net_r,
        details=details,
    )


__all__ = [
    *_LOWER_ALL,
    "SizeDependentRewardDecision",
    "size_dependent_reward_certificate",
    "size_dependent_reward_certificate_enabled",
]
