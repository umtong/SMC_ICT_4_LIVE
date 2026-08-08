"""v44 causal target hierarchy for the v41 near-edge primary trade.

The v40 source-range failed-auction detector, v41 first-displacement near-edge
entry, original source-raid invalidation, cost model, risk sizing and global
portfolio slot are frozen.  This layer changes only the primary delivery
objective:

* the source dealing-range equilibrium; or
* the nearest still-live five-minute internal liquidity already right-confirmed
  before the trade plan was observed and lying strictly between entry and the
  source equilibrium.

A closer internal level which cannot clear the frozen all-cost structural-R
floor is skipped in favor of the next farther live level.  No distance, age,
ATR, percentile or fitted score is introduced.  If no preconfirmed internal
level satisfies the causal and economic contract, the internal-target variant
rejects the trade instead of silently falling back to the source midpoint.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from typing import Any, Iterable

from c10_v41_overlay import (  # re-export the frozen lower layers
    CostAwareRiskSizer,
    FirstDisplacementEntryDecision,
    InternalPivotProtection,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    consequent_encroachment,
    first_favorable_internal_pivot,
    internal_pivot_protection_enabled,
    micro_pivot_protection_enabled,
    micro_pivot_reference_contract,
    normalize_kline_open_time,
    reframe_first_displacement_entry,
    rejection_displacement,
    repair_kline_flow_frame,
    source_entry_mode,
    source_equilibrium,
    source_equilibrium_detector_enabled,
)


@dataclass(frozen=True, slots=True)
class TargetHierarchyDecision:
    approved: bool
    plan: Any
    reason: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class InternalLiquidityCandidate:
    event_ts_ns: int
    known_ts_ns: int
    level: float
    distance_from_entry: float
    delivered_after_confirmation: bool
    gain_per_unit: float
    net_r: float
    cost_qualified: bool


def primary_target_mode() -> str:
    value = os.environ.get(
        "C10_V44_PRIMARY_TARGET_MODE",
        "SOURCE_EQUILIBRIUM",
    )
    if value not in {
        "SOURCE_EQUILIBRIUM",
        "PRECONFIRMED_INTERNAL_LIQUIDITY",
    }:
        raise ValueError(f"unsupported v44 primary target mode: {value}")
    return value


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _delivered_after_confirmation(
    *,
    direction: str,
    level: float,
    known_ts_ns: int,
    observed_ts_ns: int,
    bars: Iterable[Any],
) -> bool:
    """Whether a pivot was already consumed after it became knowable.

    The pivot event itself creates the liquidity and is therefore not counted as
    delivery.  Inspection begins at the right-confirmation timestamp.  A touch
    on that completed confirmation bar is already unavailable to a plan observed
    later and is correctly treated as consumed.
    """

    for bar in bars:
        ts_ns = int(getattr(bar, "ts_ns", -1))
        if not known_ts_ns <= ts_ns <= observed_ts_ns:
            continue
        if direction == "LONG" and float(getattr(bar, "high")) >= level:
            return True
        if direction == "SHORT" and float(getattr(bar, "low")) <= level:
            return True
    return False


def _candidate_points(
    *,
    direction: str,
    internal_highs: Iterable[tuple[int, int, float]],
    internal_lows: Iterable[tuple[int, int, float]],
) -> Iterable[tuple[int, int, float]]:
    if direction == "LONG":
        return internal_highs
    if direction == "SHORT":
        return internal_lows
    raise ValueError(f"unsupported direction: {direction}")


def reframe_primary_target(
    plan: Any,
    logic: Any,
) -> TargetHierarchyDecision:
    """Select the first live, cost-qualified internal delivery objective."""

    mode = primary_target_mode()
    common = {
        "schema": "candidate-10-v44-causal-target-hierarchy-v1",
        "target_mode": mode,
        "detector": "SOURCE_SWEEP_RECLAIM_MSS_DISPLACEMENT",
        "entry": "FIRST_DISPLACEMENT_NEAR_EDGE_PASSIVE_RETRACE",
        "initial_invalidation": "SOURCE_RAID_EXTREME_PLUS_FROZEN_ATR_BUFFER",
        "source_equilibrium_role": (
            "PRIMARY_TARGET"
            if mode == "SOURCE_EQUILIBRIUM"
            else "SECONDARY_OBJECTIVE_NOT_TRADED_BY_PRIMARY_POSITION"
        ),
    }
    if mode == "SOURCE_EQUILIBRIUM":
        return TargetHierarchyDecision(
            approved=True,
            plan=plan,
            reason="SOURCE_EQUILIBRIUM_TARGET_UNCHANGED",
            details={**common, "applied": False},
        )
    if _value(getattr(plan, "scenario", "")) != "FAR":
        return TargetHierarchyDecision(
            approved=True,
            plan=plan,
            reason="NON_FAR_UNCHANGED",
            details={**common, "applied": False},
        )

    direction = _value(plan.direction)
    entry = float(plan.expected_entry)
    stop = float(plan.stop_price)
    source_target = float(plan.target_price)
    observed_ts_ns = int(plan.observed_ts_ns)
    confirmation_close = plan.details.get("confirmation_close")
    confirmation_close_value = (
        None if confirmation_close is None else float(confirmation_close)
    )
    maker = float(logic.config.effective_maker_rate)
    minimum_r = float(logic.config.min_net_r)
    loss = float(plan.loss_per_unit)
    if loss <= 0.0:
        return TargetHierarchyDecision(
            approved=False,
            plan=plan,
            reason="INVALID_FROZEN_LOSS_PER_UNIT",
            details={**common, "applied": False, "loss_per_unit": loss},
        )

    points = _candidate_points(
        direction=direction,
        internal_highs=getattr(logic, "internal_highs", ()),
        internal_lows=getattr(logic, "internal_lows", ()),
    )
    raw_candidates: list[tuple[int, int, float]] = []
    for event_ts_raw, known_ts_raw, level_raw in points:
        event_ts_ns = int(event_ts_raw)
        known_ts_ns = int(known_ts_raw)
        level = float(level_raw)
        if known_ts_ns >= observed_ts_ns:
            continue
        between = (
            entry < level < source_target
            if direction == "LONG"
            else source_target < level < entry
        )
        if not between:
            continue
        raw_candidates.append((event_ts_ns, known_ts_ns, level))

    raw_candidates.sort(
        key=lambda item: (
            abs(item[2] - entry),
            item[1],
            item[0],
        ),
    )
    evaluated: list[InternalLiquidityCandidate] = []
    selected: InternalLiquidityCandidate | None = None
    for event_ts_ns, known_ts_ns, level in raw_candidates:
        delivered = _delivered_after_confirmation(
            direction=direction,
            level=level,
            known_ts_ns=known_ts_ns,
            observed_ts_ns=observed_ts_ns,
            bars=getattr(logic, "bars", ()),
        )
        gross_reward = (
            level - entry if direction == "LONG" else entry - level
        )
        gain = gross_reward - entry * maker - level * maker
        net_r = gain / loss if loss > 0.0 else float("-inf")
        qualified = not delivered and gain > 0.0 and net_r >= minimum_r
        candidate = InternalLiquidityCandidate(
            event_ts_ns=event_ts_ns,
            known_ts_ns=known_ts_ns,
            level=level,
            distance_from_entry=abs(level - entry),
            delivered_after_confirmation=delivered,
            gain_per_unit=gain,
            net_r=net_r,
            cost_qualified=qualified,
        )
        evaluated.append(candidate)
        if qualified:
            selected = candidate
            break

    details = {
        **common,
        "applied": selected is not None,
        "direction": direction,
        "observed_ts_ns": observed_ts_ns,
        "entry": entry,
        "confirmation_close": confirmation_close_value,
        "stop": stop,
        "source_equilibrium": source_target,
        "frozen_loss_per_unit": loss,
        "minimum_existing_costed_structural_r": minimum_r,
        "candidate_count_between_entry_and_equilibrium": len(raw_candidates),
        "evaluated_candidates": [
            {
                "event_ts_ns": item.event_ts_ns,
                "known_ts_ns": item.known_ts_ns,
                "level": item.level,
                "distance_from_entry": item.distance_from_entry,
                "delivered_after_confirmation": (
                    item.delivered_after_confirmation
                ),
                "gain_per_unit_before_impact": item.gain_per_unit,
                "costed_structural_r_before_impact": item.net_r,
                "cost_qualified": item.cost_qualified,
            }
            for item in evaluated
        ],
        "target_selection_order": (
            "nearest price first; skip consumed or sub-minimum-costed-R levels"
        ),
        "new_fitted_thresholds": [],
        "runner_contract": (
            "none in this ablation; source equilibrium remains an independent "
            "secondary objective requiring separately funded ownership"
        ),
    }
    if selected is None:
        reason = (
            "NO_PRECONFIRMED_INTERNAL_LIQUIDITY_BETWEEN_ENTRY_AND_EQUILIBRIUM"
            if not raw_candidates
            else "NO_LIVE_COST_QUALIFIED_PRECONFIRMED_INTERNAL_LIQUIDITY"
        )
        return TargetHierarchyDecision(
            approved=False,
            plan=plan,
            reason=reason,
            details=details,
        )

    details.update(
        {
            "selected_internal_event_ts_ns": selected.event_ts_ns,
            "selected_internal_known_ts_ns": selected.known_ts_ns,
            "selected_internal_liquidity": selected.level,
            "selected_distance_from_entry": selected.distance_from_entry,
            "selected_gain_per_unit_before_impact": selected.gain_per_unit,
            "selected_costed_structural_r_before_impact": selected.net_r,
            "state_sequence": [
                "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
                "FIRST_DISPLACEMENT_NEAR_EDGE_RETRACE_PENDING",
                "POSITION_OPEN",
                "PRECONFIRMED_INTERNAL_LIQUIDITY_DELIVERY_OR_RAID_INVALIDATION",
            ],
        },
    )
    plan_details = dict(plan.details)
    ce_primary = dict(plan_details.get("ce_rejection_primary", {}))
    ce_primary.update(
        {
            "target_contract": "PRECONFIRMED_FIVE_MINUTE_INTERNAL_LIQUIDITY",
            "selected_target": selected.level,
            "source_equilibrium": source_target,
        },
    )
    plan_details["ce_rejection_primary"] = ce_primary
    plan_details["source_target_hierarchy"] = details
    reframed = replace(
        plan,
        target_price=selected.level,
        gain_per_unit=selected.gain_per_unit,
        net_r=selected.net_r,
        reason_code=(
            "SOURCE_FAILED_AUCTION_TO_PRECONFIRMED_INTERNAL_LIQUIDITY"
        ),
        details=plan_details,
    )
    return TargetHierarchyDecision(
        approved=True,
        plan=reframed,
        reason="SOURCE_FAILED_AUCTION_TO_PRECONFIRMED_INTERNAL_LIQUIDITY",
        details=details,
    )


__all__ = [
    "CostAwareRiskSizer",
    "FirstDisplacementEntryDecision",
    "InternalLiquidityCandidate",
    "InternalPivotProtection",
    "LiveImpactLedger",
    "TargetHierarchyDecision",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "consequent_encroachment",
    "first_favorable_internal_pivot",
    "internal_pivot_protection_enabled",
    "micro_pivot_protection_enabled",
    "micro_pivot_reference_contract",
    "normalize_kline_open_time",
    "primary_target_mode",
    "reframe_first_displacement_entry",
    "reframe_primary_target",
    "rejection_displacement",
    "repair_kline_flow_frame",
    "source_entry_mode",
    "source_equilibrium",
    "source_equilibrium_detector_enabled",
]
