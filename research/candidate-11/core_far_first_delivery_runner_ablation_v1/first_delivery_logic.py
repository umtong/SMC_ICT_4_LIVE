"""Annotate FAR plans with one causal first-delivery objective and an external runner.

This module changes only the realization topology of an already-approved FAR plan.
Entry selection, entry price, initial stop, final external target, costs, risk sizing,
market-leadership admission and the global one-slot rule remain unchanged.

The first delivery is the nearest price node which was already knowable when the
confirmation bar completed:

* a causally confirmed five-minute opposing pivot not already consumed by the
  confirmation bar;
* a live external pool on the path to the inherited target; or
* the completed source range equilibrium.

The primary/runner split is not a fitted percentage.  Before exchange rounding,
the primary quantity fraction L / (L + G1) makes primary net gain at first
delivery exactly fund the remaining runner's full costed loss L.  The residual
quantity remains assigned to the inherited external target.
"""
from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Any

from logic import Auction, BarObs, CausalAuctionEngine, Direction, Scenario, TradePlan


POLICY = "SELF_FINANCING_FIRST_DELIVERY_EXTERNAL_RUNNER"
BASE_COSTED_PLAN = CausalAuctionEngine._costed_limit_plan


def _inside_path(direction: Direction, level: float, lower: float, final_target: float) -> bool:
    if direction == Direction.LONG:
        return lower < level < final_target
    return final_target < level < lower


def _source_bounds(a: Auction) -> tuple[float, float] | None:
    if a.pool.opposite_level is None:
        return None
    first = float(a.pool.level)
    second = float(a.pool.opposite_level)
    if not (isfinite(first) and isfinite(second)) or first == second:
        return None
    return min(first, second), max(first, second)


def _amend_plan_event(
    engine: CausalAuctionEngine,
    scenario_id: str,
    updates: dict[str, Any],
) -> None:
    for event in reversed(engine.events):
        if getattr(event, "scenario_id", None) != scenario_id:
            continue
        if getattr(event, "event_type", None) != "TRADE_PLAN_CONFIRMED":
            continue
        details = getattr(event, "details", None)
        if isinstance(details, dict):
            details.update(updates)
        break


def first_delivery_annotation(
    engine: CausalAuctionEngine,
    auction: Auction,
    confirmation_bar: BarObs,
    plan: TradePlan,
) -> dict[str, Any] | None:
    """Return the causal first-delivery and self-financing split geometry."""
    if plan.scenario != Scenario.FAR:
        return None
    direction = plan.direction
    entry = float(plan.expected_entry)
    final_target = float(plan.target_price)
    if direction == Direction.LONG:
        causal_floor = max(entry, float(confirmation_bar.high))
    else:
        causal_floor = min(entry, float(confirmation_bar.low))

    candidates: list[dict[str, Any]] = []
    bounds = _source_bounds(auction)
    if bounds is not None:
        source_low, source_high = bounds
        equilibrium = (source_low + source_high) / 2.0
        if _inside_path(direction, equilibrium, causal_floor, final_target):
            candidates.append(
                {
                    "kind": "SOURCE_RANGE_EQUILIBRIUM",
                    "price": equilibrium,
                    "identity": auction.pool.range_id,
                    "source": auction.pool.source,
                }
            )
    else:
        source_low = source_high = equilibrium = None

    points = (
        engine.internal_highs if direction == Direction.LONG else engine.internal_lows
    )
    for event_ts_ns, known_ts_ns, raw_level in points:
        level = float(raw_level)
        if (
            int(event_ts_ns) < int(auction.pool.confirmed_ts_ns)
            or int(known_ts_ns) > int(confirmation_bar.ts_ns)
        ):
            continue
        # Internal pivots have no persistent consumed flag.  Reconstruct their
        # liveness causally: once price trades through the level after the pivot
        # became known, it cannot remain a future first-delivery objective.
        crossed_after_known = any(
            (
                bar.high >= level
                if direction == Direction.LONG
                else bar.low <= level
            )
            for bar in engine.bars
            if int(known_ts_ns) <= int(bar.ts_ns) <= int(confirmation_bar.ts_ns)
        )
        if crossed_after_known:
            continue
        if not _inside_path(direction, level, causal_floor, final_target):
            continue
        candidates.append(
            {
                "kind": "CAUSAL_INTERNAL_PIVOT",
                "price": level,
                "identity": f"{int(event_ts_ns)}:{int(known_ts_ns)}",
                "source": "COMPLETED_5M_INTERNAL_STRUCTURE",
                "event_ts_ns": int(event_ts_ns),
                "known_ts_ns": int(known_ts_ns),
            }
        )

    for pool in engine.pools:
        level = float(pool.level)
        if (
            pool.consumed
            or not pool.external
            or pool.scenario_id == auction.pool.scenario_id
            or pool.source == "ROUND_NUMBER"
            or "SHELF" in pool.source
            or int(pool.confirmed_index) >= int(engine._index)
            or int(engine._index) > int(pool.expiry_index)
            or not _inside_path(direction, level, causal_floor, final_target)
        ):
            continue
        candidates.append(
            {
                "kind": "LIVE_EXTERNAL_POOL",
                "price": level,
                "identity": pool.scenario_id,
                "source": pool.source,
                "strength": int(pool.strength),
            }
        )

    if not candidates:
        return {
            "realization_policy": POLICY,
            "first_delivery_available": False,
            "first_delivery_rejection": "NO_CAUSAL_PRICE_NODE_BEFORE_EXTERNAL_TARGET",
            "source_range_low": source_low,
            "source_range_high": source_high,
            "source_range_equilibrium": equilibrium,
            "external_runner_target": final_target,
        }

    selected = min(candidates, key=lambda item: abs(float(item["price"]) - entry))
    first_target = float(selected["price"])
    entry_cost = str(plan.details.get("entry_cost_assumption", "MAKER")).upper()
    entry_rate = (
        float(engine.config.effective_taker_rate)
        if entry_cost == "TAKER"
        else float(engine.config.effective_maker_rate)
    )
    maker_rate = float(engine.config.effective_maker_rate)
    gross_first = (
        first_target - entry
        if direction == Direction.LONG
        else entry - first_target
    )
    first_net_gain = gross_first - entry * entry_rate - first_target * maker_rate
    full_loss = float(plan.loss_per_unit)
    if not (isfinite(first_net_gain) and isfinite(full_loss)) or first_net_gain <= 0.0 or full_loss <= 0.0:
        return {
            "realization_policy": POLICY,
            "first_delivery_available": False,
            "first_delivery_rejection": "NON_POSITIVE_COSTED_FIRST_DELIVERY_GEOMETRY",
            "first_delivery_target": first_target,
            "first_delivery_source": selected,
            "first_delivery_net_gain_per_unit": first_net_gain,
            "original_costed_loss_per_unit": full_loss,
            "source_range_low": source_low,
            "source_range_high": source_high,
            "source_range_equilibrium": equilibrium,
            "external_runner_target": final_target,
        }

    primary_fraction = full_loss / (full_loss + first_net_gain)
    runner_fraction = first_net_gain / (full_loss + first_net_gain)
    identity = primary_fraction * first_net_gain - runner_fraction * full_loss
    return {
        "realization_policy": POLICY,
        "first_delivery_available": True,
        "first_delivery_target": first_target,
        "first_delivery_kind": selected["kind"],
        "first_delivery_identity": selected.get("identity"),
        "first_delivery_source": selected.get("source"),
        "first_delivery_selected_record": selected,
        "first_delivery_candidate_count": len(candidates),
        "first_delivery_candidates": sorted(
            candidates,
            key=lambda item: abs(float(item["price"]) - entry),
        ),
        "first_delivery_net_gain_per_unit": first_net_gain,
        "original_costed_loss_per_unit": full_loss,
        "first_delivery_primary_fraction": primary_fraction,
        "external_runner_fraction": runner_fraction,
        "pre_rounding_self_financing_error": identity,
        "external_runner_target": final_target,
        "external_runner_net_gain_per_unit": float(plan.gain_per_unit),
        "source_range_low": source_low,
        "source_range_high": source_high,
        "source_range_equilibrium": equilibrium,
        "confirmation_causal_guard": causal_floor,
        "allocation_formula": "primary=L/(L+G1); runner=G1/(L+G1)",
    }


def costed_plan_with_first_delivery(
    self: CausalAuctionEngine,
    auction: Auction,
    confirmation_bar: BarObs,
    reason: str,
) -> TradePlan | None:
    plan = BASE_COSTED_PLAN(self, auction, confirmation_bar, reason)
    if plan is None or plan.scenario != Scenario.FAR:
        return plan
    annotation = first_delivery_annotation(self, auction, confirmation_bar, plan)
    if annotation is None:
        return plan
    details = dict(plan.details)
    details.update(annotation)
    amended = replace(plan, details=details)
    _amend_plan_event(
        self,
        amended.scenario_id,
        {
            "realization_policy": POLICY,
            "first_delivery_available": annotation.get("first_delivery_available"),
            "first_delivery_target": annotation.get("first_delivery_target"),
            "first_delivery_kind": annotation.get("first_delivery_kind"),
            "first_delivery_primary_fraction": annotation.get(
                "first_delivery_primary_fraction"
            ),
            "external_runner_fraction": annotation.get("external_runner_fraction"),
            "external_runner_target": annotation.get("external_runner_target"),
        },
    )
    return amended


def install() -> None:
    if getattr(CausalAuctionEngine, "_first_delivery_runner_installed", False):
        return
    CausalAuctionEngine._costed_limit_plan = costed_plan_with_first_delivery
    CausalAuctionEngine._first_delivery_runner_installed = True
