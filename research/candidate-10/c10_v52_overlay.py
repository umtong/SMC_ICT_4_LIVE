"""v52 funded source-equilibrium checkpoint with an independent external runner.

The failed-auction detector remains independent of external liquidity.  Only
after a rank-one transfer state has confirmed may the trading scenario ask
whether a pre-existing external hazard exists beyond source equilibrium in the
same direction.  Source equilibrium then becomes a first-delivery checkpoint:
the minimum solved quantity is closed there whose all-cost profit funds the
complete original-stop loss of the residual runner.

This is a two-stage auction contract, not a farther fixed target or arbitrary
partial percentage.  It reuses Candidate 11's external-hazard formula,
dominance threshold, minimum net R, source range, costs and v32 solved funding
equation without introducing a new fitted constant.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
import os
from typing import Any

from c10_v51_overlay import *  # noqa: F403 frozen lower-layer re-export
from c10_v51_overlay import __all__ as _LOWER_ALL
from c10_v32_overlay import solve_funded_reduction


@dataclass(frozen=True, slots=True)
class ExternalRunnerDecision:
    approved: bool
    plan: Any
    reason: str
    details: dict[str, Any]


def external_runner_enabled() -> bool:
    return os.environ.get("C10_V52_EXTERNAL_RUNNER", "0") == "1"


def funded_equilibrium_runner_enabled() -> bool:
    return os.environ.get("C10_V52_FUNDED_EQUILIBRIUM", "0") == "1"


def _hazard(logic: Any, pool: Any, price: float, atr: float) -> float:
    value = float(logic._liquidity_hazard(pool, price, atr))
    if not isfinite(value) or value < 0.0:
        raise ValueError("invalid external liquidity hazard")
    return value


def _external_draw(
    logic: Any,
    *,
    reference: float,
    atr: float,
    observed_ts_ns: int,
    excluded_ids: set[str],
) -> tuple[Any | None, str | None, float, float, float]:
    eligible = [
        pool
        for pool in logic.pools
        if pool.scenario_id not in excluded_ids
        and not pool.consumed
        and bool(pool.external)
        and pool.source != "ROUND_NUMBER"
        and "SHELF" not in str(pool.source)
        and int(pool.confirmed_ts_ns) < int(observed_ts_ns)
        and int(logic._index) <= int(pool.expiry_index)
    ]
    highs = [pool for pool in eligible if str(pool.side.value) == "HIGH" and float(pool.level) > reference]
    lows = [pool for pool in eligible if str(pool.side.value) == "LOW" and float(pool.level) < reference]
    high_pool = max(
        highs,
        key=lambda pool: _hazard(logic, pool, reference, atr),
        default=None,
    )
    low_pool = max(
        lows,
        key=lambda pool: _hazard(logic, pool, reference, atr),
        default=None,
    )
    high_hazard = 0.0 if high_pool is None else _hazard(logic, high_pool, reference, atr)
    low_hazard = 0.0 if low_pool is None else _hazard(logic, low_pool, reference, atr)
    total = high_hazard + low_hazard
    if total <= 0.0:
        return None, None, 0.0, high_hazard, low_hazard
    signed = (high_hazard - low_hazard) / total
    if abs(signed) < float(logic.config.draw_dominance_min):
        return None, None, signed, high_hazard, low_hazard
    side = "HIGH" if signed > 0.0 else "LOW"
    pool = high_pool if side == "HIGH" else low_pool
    return pool, side, signed, high_hazard, low_hazard


def reframe_external_runner(plan: Any, logic: Any) -> ExternalRunnerDecision:
    """Replace midpoint target only when a causal independent draw agrees."""

    enabled = external_runner_enabled()
    common = {
        "schema": "candidate-10-v52-independent-external-runner-v1",
        "enabled": enabled,
        "detector": "SOURCE_SWEEP_RECLAIM_MSS_DISPLACEMENT",
        "detector_external_draw_required": False,
        "entry": "FIRST_DISPLACEMENT_NEAR_EDGE_PASSIVE_RETRACE",
        "hard_invalidation": "SOURCE_RAID_EXTREME_PLUS_EXISTING_ATR_BUFFER",
        "first_delivery": "SOURCE_DEALING_RANGE_EQUILIBRIUM",
        "runner_draw": "PREEXISTING_EXTERNAL_HAZARD_DOMINANCE",
        "new_fitted_thresholds": [],
    }
    if not enabled:
        return ExternalRunnerDecision(
            approved=True,
            plan=plan,
            reason="EXTERNAL_RUNNER_DISABLED",
            details={**common, "applied": False},
        )
    if str(getattr(getattr(plan, "scenario", None), "value", "")) != "FAR":
        return ExternalRunnerDecision(
            approved=False,
            plan=plan,
            reason="EXTERNAL_RUNNER_REQUIRES_FAR",
            details={**common, "applied": True},
        )

    source = next(
        (
            pool
            for pool in logic.pools
            if str(pool.scenario_id) == str(plan.scenario_id)
        ),
        None,
    )
    if source is None or source.opposite_level is None:
        return ExternalRunnerDecision(
            approved=False,
            plan=plan,
            reason="SOURCE_RANGE_ENDPOINTS_UNAVAILABLE",
            details={**common, "applied": True},
        )
    midpoint = (float(source.level) + float(source.opposite_level)) / 2.0
    direction = str(getattr(plan.direction, "value", plan.direction))
    desired_side = "HIGH" if direction == "LONG" else "LOW"
    pool, draw_side, score, high_hazard, low_hazard = _external_draw(
        logic,
        reference=midpoint,
        atr=float(plan.atr),
        observed_ts_ns=int(plan.observed_ts_ns),
        excluded_ids={str(plan.scenario_id)},
    )
    details = {
        **common,
        "applied": True,
        "direction": direction,
        "desired_draw_side": desired_side,
        "resolved_draw_side": draw_side,
        "draw_score": score,
        "high_hazard": high_hazard,
        "low_hazard": low_hazard,
        "minimum_existing_draw_dominance": float(
            logic.config.draw_dominance_min
        ),
        "source_range_id": source.range_id,
        "source_level": float(source.level),
        "source_opposite_level": float(source.opposite_level),
        "source_equilibrium_checkpoint": midpoint,
    }
    if pool is None or draw_side != desired_side:
        return ExternalRunnerDecision(
            approved=False,
            plan=plan,
            reason="NO_AGREEING_INDEPENDENT_EXTERNAL_RUNNER_DRAW",
            details=details,
        )

    target = float(pool.level)
    entry = float(plan.expected_entry)
    stop = float(plan.stop_price)
    confirmation = float(plan.details.get("confirmation_close", entry))
    if direction == "LONG":
        correct_order = stop < entry < midpoint < target
        passive = entry < confirmation
        reward = target - entry
    elif direction == "SHORT":
        correct_order = target < midpoint < entry < stop
        passive = entry > confirmation
        reward = entry - target
    else:
        raise ValueError(f"unsupported direction: {direction}")
    details.update(
        {
            "external_target_pool_id": str(pool.scenario_id),
            "external_target_source": str(pool.source),
            "external_target_confirmed_ts_ns": int(pool.confirmed_ts_ns),
            "external_target_level": target,
            "target_known_strictly_before_plan": (
                int(pool.confirmed_ts_ns) < int(plan.observed_ts_ns)
            ),
            "price_order_valid": correct_order,
            "entry_still_passive": passive,
        },
    )
    if not correct_order or not passive:
        return ExternalRunnerDecision(
            approved=False,
            plan=plan,
            reason="EXTERNAL_RUNNER_NON_CAUSAL_PRICE_ORDER",
            details=details,
        )

    maker = float(logic.config.effective_maker_rate)
    gain = reward - entry * maker - target * maker
    loss = float(plan.loss_per_unit)
    net_r = gain / loss if loss > 0.0 else float("-inf")
    details.update(
        {
            "preimpact_gain_per_unit": gain,
            "preimpact_loss_per_unit": loss,
            "preimpact_net_r": net_r,
            "minimum_existing_net_r": float(logic.config.min_net_r),
        },
    )
    if gain <= 0.0 or net_r < float(logic.config.min_net_r):
        return ExternalRunnerDecision(
            approved=False,
            plan=plan,
            reason="EXTERNAL_RUNNER_INSUFFICIENT_EXISTING_COSTED_R",
            details=details,
        )

    plan_details = dict(plan.details)
    plan_details["external_runner"] = details
    plan_details["source_equilibrium_checkpoint"] = midpoint
    plan_details["source_equilibrium_primary_target"] = float(
        plan.target_price
    )
    reframed = replace(
        plan,
        target_price=target,
        gain_per_unit=gain,
        net_r=net_r,
        reason_code="SOURCE_EQUILIBRIUM_CHECKPOINT_EXTERNAL_DRAW_RUNNER",
        details=plan_details,
    )
    return ExternalRunnerDecision(
        approved=True,
        plan=reframed,
        reason="INDEPENDENT_EXTERNAL_RUNNER_CONFIRMED",
        details=details,
    )


__all__ = [
    *_LOWER_ALL,
    "ExternalRunnerDecision",
    "external_runner_enabled",
    "funded_equilibrium_runner_enabled",
    "reframe_external_runner",
    "solve_funded_reduction",
]
