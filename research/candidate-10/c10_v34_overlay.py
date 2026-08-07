"""v34 source-boundary retest entry for the equilibrium primary trade.

v33 established two facts: source equilibrium can be a valid first delivery, but
using the old near-edge displacement retrace with the old raid stop often leaves
insufficient costed payoff; using the displacement zone itself as an immediate
hard stop can place the stop inside the live market when the entry fills.

v34 therefore freezes the original raid invalidation and tests one structural
entry change: after failed-auction confirmation, rest a passive order at the
reclaimed source-liquidity boundary itself.  That level existed before the raid,
is between the confirmation close and the raid extreme, and is not a fitted
fraction.  The exact 2x2 ablation separates entry location from target location:

* near displacement edge vs reclaimed source boundary;
* independent external draw vs source dealing-range equilibrium.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from typing import Any

from c10_v33_overlay import (  # re-export frozen infrastructure/lower layers
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    normalize_kline_open_time,
    repair_kline_flow_frame,
)


@dataclass(frozen=True, slots=True)
class SourceRetestDecision:
    approved: bool
    plan: Any
    reason: str
    details: dict[str, Any]


def source_retest_enabled() -> bool:
    return os.environ.get("C10_V34_SOURCE_RETEST_ENTRY", "0") == "1"


def equilibrium_target_enabled() -> bool:
    return os.environ.get("C10_V34_EQUILIBRIUM_TARGET", "0") == "1"


def _value(item: Any) -> str:
    return str(getattr(item, "value", item))


def _source_pool(logic: Any, scenario_id: str) -> Any | None:
    return next(
        (
            pool
            for pool in getattr(logic, "pools", ())
            if str(getattr(pool, "scenario_id", "")) == scenario_id
        ),
        None,
    )


def _target_was_delivered(
    *,
    logic: Any,
    direction: str,
    target: float,
    sweep_ts_ns: int,
    confirmation_ts_ns: int,
) -> bool:
    for bar in getattr(logic, "bars", ()):
        ts_ns = int(getattr(bar, "ts_ns", -1))
        if not sweep_ts_ns <= ts_ns <= confirmation_ts_ns:
            continue
        if direction == "LONG" and float(getattr(bar, "high")) >= target:
            return True
        if direction == "SHORT" and float(getattr(bar, "low")) <= target:
            return True
    return False


def _variant(*, source_retest: bool, equilibrium_target: bool) -> str:
    if source_retest and equilibrium_target:
        return "SOURCE_RETEST_TO_EQUILIBRIUM"
    if source_retest:
        return "SOURCE_RETEST_TO_EXTERNAL_DRAW"
    if equilibrium_target:
        return "NEAR_EDGE_TO_EQUILIBRIUM"
    return "NEAR_EDGE_TO_EXTERNAL_DRAW"


def reframe_source_retest(plan: Any, logic: Any) -> SourceRetestDecision:
    source_retest = source_retest_enabled()
    equilibrium_target = equilibrium_target_enabled()
    variant = _variant(
        source_retest=source_retest,
        equilibrium_target=equilibrium_target,
    )
    common = {
        "schema": "candidate-10-v34-source-retest-v1",
        "variant": variant,
        "source_retest_entry_enabled": source_retest,
        "equilibrium_target_enabled": equilibrium_target,
        "hard_invalidation": "ORIGINAL_RAID_EXTREME_WITH_FROZEN_ATR_BUFFER",
    }

    if _value(getattr(plan, "scenario", "")) != "FAR":
        return SourceRetestDecision(
            approved=True,
            plan=plan,
            reason="NON_FAR_UNCHANGED",
            details={**common, "applied": False},
        )
    if not source_retest and not equilibrium_target:
        return SourceRetestDecision(
            approved=True,
            plan=plan,
            reason="EXACT_BASELINE_UNCHANGED",
            details={**common, "applied": False},
        )

    pool = _source_pool(logic, str(plan.scenario_id))
    opposite = None if pool is None else getattr(pool, "opposite_level", None)
    if pool is None or opposite is None:
        return SourceRetestDecision(
            approved=False,
            plan=plan,
            reason="SOURCE_RANGE_ENDPOINTS_UNAVAILABLE",
            details={**common, "applied": False},
        )

    direction = _value(plan.direction)
    source_level = float(pool.level)
    opposite_level = float(opposite)
    equilibrium = (source_level + opposite_level) / 2.0
    original_entry = float(plan.expected_entry)
    original_target = float(plan.target_price)
    stop = float(plan.stop_price)
    confirmation = float(plan.details.get("confirmation_close"))
    sweep_extreme = float(plan.details.get("sweep_extreme"))
    entry = source_level if source_retest else original_entry
    target = equilibrium if equilibrium_target else original_target

    if source_retest:
        passive_and_between = (
            sweep_extreme <= entry < confirmation
            if direction == "LONG"
            else confirmation < entry <= sweep_extreme
        )
        if not passive_and_between:
            return SourceRetestDecision(
                approved=False,
                plan=plan,
                reason="SOURCE_RETEST_NOT_PASSIVE_BETWEEN_CONFIRMATION_AND_RAID",
                details={
                    **common,
                    "applied": False,
                    "direction": direction,
                    "source_retest_entry": entry,
                    "confirmation_close": confirmation,
                    "sweep_extreme": sweep_extreme,
                },
            )

    if equilibrium_target and _target_was_delivered(
        logic=logic,
        direction=direction,
        target=target,
        sweep_ts_ns=int(plan.details.get("sweep_ts_ns", -1)),
        confirmation_ts_ns=int(plan.observed_ts_ns),
    ):
        return SourceRetestDecision(
            approved=False,
            plan=plan,
            reason="SOURCE_EQUILIBRIUM_DELIVERED_BEFORE_ENTRY_PLAN",
            details={
                **common,
                "applied": False,
                "source_equilibrium": equilibrium,
            },
        )

    if direction == "LONG":
        gross_risk = entry - stop
        gross_reward = target - entry
    elif direction == "SHORT":
        gross_risk = stop - entry
        gross_reward = entry - target
    else:
        raise ValueError(f"unsupported direction: {direction}")

    details = {
        **common,
        "applied": True,
        "direction": direction,
        "original_near_edge_entry": original_entry,
        "source_retest_entry": source_level,
        "selected_entry": entry,
        "confirmation_close": confirmation,
        "sweep_extreme": sweep_extreme,
        "raid_invalidation": stop,
        "source_pool_level": source_level,
        "source_opposite_level": opposite_level,
        "source_range_low": min(source_level, opposite_level),
        "source_range_high": max(source_level, opposite_level),
        "source_equilibrium": equilibrium,
        "selected_target": target,
        "original_independent_external_draw": original_target,
        "entry_state_sequence": [
            "FAILED_AUCTION_CONFIRMED",
            "SOURCE_BOUNDARY_RETEST_PENDING",
            "PRIMARY_TRADE_OPEN_OR_EXPIRED",
        ],
        "runner_contract": (
            "NOT_PART_OF_PRIMARY_TRADE; any post-equilibrium external-draw "
            "runner requires a separately funded auction state"
        ),
    }
    if gross_risk <= 0.0 or gross_reward <= 0.0:
        return SourceRetestDecision(
            approved=False,
            plan=plan,
            reason="SOURCE_RETEST_NON_CAUSAL_PRICE_ORDER",
            details={
                **details,
                "gross_risk": gross_risk,
                "gross_reward": gross_reward,
            },
        )

    maker = float(logic.config.effective_maker_rate)
    taker = float(logic.config.effective_taker_rate)
    loss = gross_risk + entry * maker + stop * taker
    gain = gross_reward - entry * maker - target * maker
    net_r = gain / loss if loss > 0.0 else float("-inf")
    details.update(
        {
            "gross_risk": gross_risk,
            "gross_reward": gross_reward,
            "loss_per_unit_before_impact": loss,
            "gain_per_unit_before_impact": gain,
            "costed_structural_r_before_impact": net_r,
            "minimum_existing_costed_structural_r": float(logic.config.min_net_r),
        },
    )
    if gain <= 0.0 or net_r < float(logic.config.min_net_r):
        return SourceRetestDecision(
            approved=False,
            plan=plan,
            reason="SOURCE_RETEST_INSUFFICIENT_COSTED_STRUCTURAL_R",
            details=details,
        )

    reason_code = {
        "SOURCE_RETEST_TO_EQUILIBRIUM": (
            "FAR_RECLAIMED_SOURCE_BOUNDARY_RETEST_TO_SOURCE_EQUILIBRIUM"
        ),
        "SOURCE_RETEST_TO_EXTERNAL_DRAW": (
            "FAR_RECLAIMED_SOURCE_BOUNDARY_RETEST_TO_EXTERNAL_DRAW"
        ),
        "NEAR_EDGE_TO_EQUILIBRIUM": (
            "FAR_NEAR_EDGE_RETRACE_TO_SOURCE_EQUILIBRIUM"
        ),
    }[variant]
    plan_details = dict(plan.details)
    plan_details["source_retest_primary"] = details
    plan_details["entry_cost_assumption"] = "MAKER"
    reframed = replace(
        plan,
        expected_entry=entry,
        target_price=target,
        loss_per_unit=loss,
        gain_per_unit=gain,
        net_r=net_r,
        reason_code=reason_code,
        details=plan_details,
    )
    return SourceRetestDecision(
        approved=True,
        plan=reframed,
        reason=reason_code,
        details=details,
    )


__all__ = [
    "CostAwareRiskSizer",
    "LiveImpactLedger",
    "SourceRetestDecision",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "equilibrium_target_enabled",
    "normalize_kline_open_time",
    "reframe_source_retest",
    "repair_kline_flow_frame",
    "source_retest_enabled",
]
