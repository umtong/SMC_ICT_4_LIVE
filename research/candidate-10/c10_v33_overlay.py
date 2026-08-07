"""v33 source-equilibrium primary trade over frozen v29/v28/v27.

The failed-auction detector, dynamic market leadership, independent external draw,
entry location, fee schedule, size-dependent impact model, current-NAV 3% risk
budget and global slot are frozen. This layer changes the economic contract of
the FAR trade:

* the midpoint of the already-completed source dealing range is the primary
  delivery objective;
* acceptance through the complete confirmation displacement void is the primary
  structural invalidation;
* the original independent external draw is retained only as metadata for a
  later, separately funded runner.

The 2x2 exact ablation independently restores the original external target and
the original raid-extreme stop. No fitted threshold is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from typing import Any

import pandas as pd

from c10_v29_overlay import (  # re-export frozen lower layers
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    repair_kline_flow_frame,
)


def normalize_kline_open_time(frame: Any, filename: str) -> Any:
    """Normalize official archive timestamps after header rows are removed."""

    if "open_time" not in frame.columns:
        raise RuntimeError(f"missing open_time column: {filename}")
    result = frame.copy()
    values = pd.to_numeric(result["open_time"], errors="raise")
    if values.isna().any():
        raise RuntimeError(f"missing open_time value after normalization: {filename}")
    result["open_time"] = values.astype("int64")
    return result


@dataclass(frozen=True, slots=True)
class PrimaryEquilibriumDecision:
    approved: bool
    plan: Any
    reason: str
    details: dict[str, Any]


def equilibrium_target_enabled() -> bool:
    return os.environ.get("C10_V33_EQUILIBRIUM_TARGET", "0") == "1"


def zone_invalidation_enabled() -> bool:
    return os.environ.get("C10_V33_ZONE_INVALIDATION", "0") == "1"


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
    midpoint: float,
    sweep_ts_ns: int,
    confirmation_ts_ns: int,
) -> bool:
    for bar in getattr(logic, "bars", ()):
        ts_ns = int(getattr(bar, "ts_ns", -1))
        if not sweep_ts_ns <= ts_ns <= confirmation_ts_ns:
            continue
        if direction == "LONG" and float(getattr(bar, "high")) >= midpoint:
            return True
        if direction == "SHORT" and float(getattr(bar, "low")) <= midpoint:
            return True
    return False


def _variant_name(*, equilibrium_target: bool, zone_invalidation: bool) -> str:
    if equilibrium_target and zone_invalidation:
        return "PRIMARY_EQUILIBRIUM_ZONE_INVALIDATION"
    if equilibrium_target:
        return "EQUILIBRIUM_TARGET_RAID_STOP"
    if zone_invalidation:
        return "EXTERNAL_DRAW_ZONE_INVALIDATION"
    return "BASELINE_EXTERNAL_DRAW_RAID_STOP"


def reframe_primary_equilibrium(plan: Any, logic: Any) -> PrimaryEquilibriumDecision:
    """Return a causal FAR plan under the v33 primary-trade contract."""

    target_enabled = equilibrium_target_enabled()
    stop_enabled = zone_invalidation_enabled()
    variant = _variant_name(
        equilibrium_target=target_enabled,
        zone_invalidation=stop_enabled,
    )
    common = {
        "schema": "candidate-10-v33-primary-equilibrium-v1",
        "variant": variant,
        "equilibrium_target_enabled": target_enabled,
        "zone_invalidation_enabled": stop_enabled,
    }

    if _value(getattr(plan, "scenario", "")) != "FAR":
        return PrimaryEquilibriumDecision(
            approved=True,
            plan=plan,
            reason="NON_FAR_UNCHANGED",
            details={**common, "applied": False},
        )
    if not target_enabled and not stop_enabled:
        return PrimaryEquilibriumDecision(
            approved=True,
            plan=plan,
            reason="EXACT_BASELINE_UNCHANGED",
            details={**common, "applied": False},
        )

    pool = _source_pool(logic, str(plan.scenario_id))
    opposite = None if pool is None else getattr(pool, "opposite_level", None)
    if pool is None or opposite is None:
        return PrimaryEquilibriumDecision(
            approved=False,
            plan=plan,
            reason="SOURCE_RANGE_ENDPOINTS_UNAVAILABLE",
            details={**common, "applied": False},
        )

    source_level = float(pool.level)
    opposite_level = float(opposite)
    midpoint = (source_level + opposite_level) / 2.0
    direction = _value(plan.direction)
    entry = float(plan.expected_entry)
    original_stop = float(plan.stop_price)
    original_target = float(plan.target_price)
    target = midpoint if target_enabled else original_target

    if target_enabled:
        sweep_ts_ns = int(plan.details.get("sweep_ts_ns", -1))
        if _target_was_delivered(
            logic=logic,
            direction=direction,
            midpoint=midpoint,
            sweep_ts_ns=sweep_ts_ns,
            confirmation_ts_ns=int(plan.observed_ts_ns),
        ):
            return PrimaryEquilibriumDecision(
                approved=False,
                plan=plan,
                reason="SOURCE_EQUILIBRIUM_DELIVERED_BEFORE_ENTRY_PLAN",
                details={
                    **common,
                    "applied": False,
                    "source_equilibrium": midpoint,
                    "sweep_ts_ns": sweep_ts_ns,
                    "confirmation_ts_ns": int(plan.observed_ts_ns),
                },
            )

    stop = original_stop
    zone_low = plan.details.get("zone_low")
    zone_high = plan.details.get("zone_high")
    if stop_enabled:
        if zone_low is None or zone_high is None:
            return PrimaryEquilibriumDecision(
                approved=False,
                plan=plan,
                reason="CONFIRMATION_DISPLACEMENT_ZONE_UNAVAILABLE",
                details={**common, "applied": False},
            )
        zone_low_f = float(zone_low)
        zone_high_f = float(zone_high)
        if not zone_low_f < zone_high_f:
            return PrimaryEquilibriumDecision(
                approved=False,
                plan=plan,
                reason="INVALID_CONFIRMATION_DISPLACEMENT_ZONE",
                details={
                    **common,
                    "applied": False,
                    "zone_low": zone_low_f,
                    "zone_high": zone_high_f,
                },
            )
        buffer_atr = float(logic.config.stop_buffer_atr)
        buffer = buffer_atr * float(plan.atr)
        stop = (
            zone_low_f - buffer
            if direction == "LONG"
            else zone_high_f + buffer
        )

    if direction == "LONG":
        gross_risk = entry - stop
        gross_reward = target - entry
    elif direction == "SHORT":
        gross_risk = stop - entry
        gross_reward = entry - target
    else:
        raise ValueError(f"unsupported direction: {direction}")

    diagnostics = {
        **common,
        "applied": True,
        "source_pool_level": source_level,
        "source_opposite_level": opposite_level,
        "source_range_low": min(source_level, opposite_level),
        "source_range_high": max(source_level, opposite_level),
        "source_equilibrium": midpoint,
        "primary_target": target,
        "primary_invalidation": stop,
        "original_independent_external_draw": original_target,
        "original_raid_invalidation": original_stop,
        "confirmation_zone_low": None if zone_low is None else float(zone_low),
        "confirmation_zone_high": None if zone_high is None else float(zone_high),
        "state_sequence": [
            "FAILED_AUCTION_CONFIRMED",
            "PRIMARY_EQUILIBRIUM_PENDING",
            "PRIMARY_TRADE_TERMINAL",
        ],
        "runner_contract": (
            "NOT_PART_OF_PRIMARY_TRADE; original independent external draw "
            "requires a separately funded post-equilibrium state machine"
        ),
    }
    if gross_risk <= 0.0 or gross_reward <= 0.0:
        return PrimaryEquilibriumDecision(
            approved=False,
            plan=plan,
            reason="PRIMARY_EQUILIBRIUM_NON_CAUSAL_PRICE_ORDER",
            details={
                **diagnostics,
                "gross_risk": gross_risk,
                "gross_reward": gross_reward,
            },
        )

    maker = float(logic.config.effective_maker_rate)
    taker = float(logic.config.effective_taker_rate)
    loss = gross_risk + entry * maker + stop * taker
    net_gain = gross_reward - entry * maker - target * maker
    net_r = net_gain / loss if loss > 0.0 else float("-inf")
    diagnostics.update(
        {
            "gross_risk": gross_risk,
            "gross_reward": gross_reward,
            "loss_per_unit_before_impact": loss,
            "gain_per_unit_before_impact": net_gain,
            "costed_structural_r_before_impact": net_r,
            "minimum_existing_costed_structural_r": float(logic.config.min_net_r),
        },
    )
    if net_gain <= 0.0 or net_r < float(logic.config.min_net_r):
        return PrimaryEquilibriumDecision(
            approved=False,
            plan=plan,
            reason="PRIMARY_EQUILIBRIUM_INSUFFICIENT_COSTED_STRUCTURAL_R",
            details=diagnostics,
        )

    reason_code = {
        "PRIMARY_EQUILIBRIUM_ZONE_INVALIDATION": (
            "FAR_SOURCE_EQUILIBRIUM_PRIMARY_WITH_DISPLACEMENT_INVALIDATION"
        ),
        "EQUILIBRIUM_TARGET_RAID_STOP": (
            "FAR_SOURCE_EQUILIBRIUM_TARGET_WITH_RAID_INVALIDATION"
        ),
        "EXTERNAL_DRAW_ZONE_INVALIDATION": (
            "FAR_EXTERNAL_DRAW_WITH_DISPLACEMENT_INVALIDATION"
        ),
    }[variant]
    details = dict(plan.details)
    details["primary_equilibrium"] = diagnostics
    reframed = replace(
        plan,
        stop_price=stop,
        target_price=target,
        loss_per_unit=loss,
        gain_per_unit=net_gain,
        net_r=net_r,
        reason_code=reason_code,
        details=details,
    )
    return PrimaryEquilibriumDecision(
        approved=True,
        plan=reframed,
        reason=reason_code,
        details=diagnostics,
    )


__all__ = [
    "CostAwareRiskSizer",
    "LiveImpactLedger",
    "PrimaryEquilibriumDecision",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "equilibrium_target_enabled",
    "normalize_kline_open_time",
    "reframe_primary_equilibrium",
    "repair_kline_flow_frame",
    "zone_invalidation_enabled",
]
