"""v57 earliest solvable preconfirmed funding checkpoint.

v44 showed that preconfirmed five-minute internal liquidity is usually too near
to serve as a full-position target against the original source-raid stop.  v57
changes its role rather than relaxing its geometry: after the exact v51 size and
impact solution is known, the earliest still-live preconfirmed internal level is
selected only when a partial exit at that level can fund the complete original-
stop loss of the residual v52 external runner.

If no internal level can do so, source equilibrium remains the checkpoint.  No
fixed fraction, distance, ATR, percentile, age or fitted threshold is added.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
import os
from typing import Any, Iterable

from c10_v56_overlay import *  # noqa: F403 frozen lower-layer re-export
from c10_v56_overlay import __all__ as _LOWER_ALL


@dataclass(frozen=True, slots=True)
class FundedCheckpointDecision:
    approved: bool
    plan: Any
    reason: str
    level: float | None
    source: str | None
    details: dict[str, Any]


def internal_funding_checkpoint_enabled() -> bool:
    return os.environ.get("C10_V57_INTERNAL_FUNDING_CHECKPOINT", "0") == "1"


def _delivered(
    *,
    direction: str,
    level: float,
    known_ts_ns: int,
    observed_ts_ns: int,
    bars: Iterable[Any],
) -> bool:
    for bar in bars:
        ts_ns = int(getattr(bar, "ts_ns", -1))
        if not known_ts_ns <= ts_ns <= observed_ts_ns:
            continue
        if direction == "LONG" and float(getattr(bar, "high")) >= level:
            return True
        if direction == "SHORT" and float(getattr(bar, "low")) <= level:
            return True
    return False


def select_funded_checkpoint(
    plan: Any,
    logic: Any,
    solution: Any,
    instrument: Any,
    *,
    maker_fee: Decimal,
    taker_fee: Decimal,
) -> FundedCheckpointDecision:
    enabled = internal_funding_checkpoint_enabled()
    details_common = {
        "schema": "candidate-10-v57-earliest-solvable-funding-checkpoint-v1",
        "enabled": enabled,
        "checkpoint_role": (
            "fund residual original-stop risk; never replace external runner target"
        ),
        "partial_fraction": "solved, never fixed",
        "new_fitted_thresholds": [],
    }
    source_eq_raw = getattr(plan, "details", {}).get(
        "source_equilibrium_checkpoint"
    )
    if source_eq_raw is None:
        return FundedCheckpointDecision(
            False,
            plan,
            "SOURCE_EQUILIBRIUM_CHECKPOINT_UNAVAILABLE",
            None,
            None,
            details_common,
        )
    source_eq = float(source_eq_raw)
    if not enabled:
        return FundedCheckpointDecision(
            True,
            plan,
            "SOURCE_EQUILIBRIUM_CHECKPOINT_UNCHANGED",
            source_eq,
            "SOURCE_EQUILIBRIUM",
            {**details_common, "applied": False, "selected_level": source_eq},
        )

    direction = str(getattr(plan.direction, "value", plan.direction))
    entry = float(plan.expected_entry)
    observed = int(plan.observed_ts_ns)
    points = (
        getattr(logic, "internal_highs", ())
        if direction == "LONG"
        else getattr(logic, "internal_lows", ())
    )
    candidates: list[tuple[int, int, float]] = []
    for event_raw, known_raw, level_raw in points:
        event = int(event_raw)
        known = int(known_raw)
        level = float(level_raw)
        if known >= observed:
            continue
        between = (
            entry < level < source_eq
            if direction == "LONG"
            else source_eq < level < entry
        )
        if not between:
            continue
        if _delivered(
            direction=direction,
            level=level,
            known_ts_ns=known,
            observed_ts_ns=observed,
            bars=getattr(logic, "bars", ()),
        ):
            continue
        candidates.append((event, known, level))
    candidates.sort(key=lambda item: (abs(item[2] - entry), item[1], item[0]))
    candidate_levels = [*candidates, (-1, -1, source_eq)]

    evaluated: list[dict[str, Any]] = []
    selected = None
    for event, known, level in candidate_levels:
        reduction = solve_funded_reduction(  # noqa: F405 lower-layer export
            direction=direction,
            total_quantity=Decimal(str(solution.quantity)),
            entry_price=Decimal(str(plan.expected_entry)),
            current_price=Decimal(str(level)),
            original_loss_per_unit=Decimal(str(solution.per_unit_loss)),
            maker_fee=maker_fee,
            taker_fee=taker_fee,
            impact_per_side=Decimal(str(solution.impact_per_side)),
            tick_size=Decimal(str(instrument.price_increment)),
            quantity_increment=Decimal(str(instrument.size_increment)),
            min_quantity=Decimal(str(instrument.min_quantity)),
        )
        row = {
            "event_ts_ns": None if event < 0 else event,
            "known_ts_ns": None if known < 0 else known,
            "level": level,
            "source": (
                "SOURCE_EQUILIBRIUM"
                if event < 0
                else "PRECONFIRMED_FIVE_MINUTE_INTERNAL_LIQUIDITY"
            ),
            "distance_from_entry": abs(level - entry),
            "funding_solvable_at_exact_level": reduction is not None,
            "solved_partial_fraction": (
                None if reduction is None else float(reduction.fraction)
            ),
            "solved_partial_quantity": (
                None if reduction is None else float(reduction.partial_quantity)
            ),
            "solved_residual_quantity": (
                None if reduction is None else float(reduction.residual_quantity)
            ),
            "solved_locked_profit": (
                None if reduction is None else float(reduction.locked_profit)
            ),
            "solved_residual_max_loss": (
                None if reduction is None else float(reduction.residual_max_loss)
            ),
        }
        evaluated.append(row)
        if reduction is not None:
            selected = row
            break

    if selected is None:
        return FundedCheckpointDecision(
            False,
            plan,
            "NO_SOLVABLE_FUNDING_CHECKPOINT",
            None,
            None,
            {
                **details_common,
                "applied": True,
                "entry": entry,
                "source_equilibrium": source_eq,
                "evaluated": evaluated,
            },
        )

    details = {
        **details_common,
        "applied": True,
        "direction": direction,
        "entry": entry,
        "source_equilibrium": source_eq,
        "candidate_count_before_source_equilibrium": len(candidates),
        "evaluated": evaluated,
        "selected_level": selected["level"],
        "selected_source": selected["source"],
        "selected_expected_partial_fraction": selected[
            "solved_partial_fraction"
        ],
        "selected_expected_residual_quantity": selected[
            "solved_residual_quantity"
        ],
        "selection_order": (
            "nearest live preconfirmed internal liquidity first, source equilibrium last"
        ),
    }
    plan_details = dict(plan.details)
    plan_details["funded_checkpoint"] = details
    plan_details["funding_checkpoint"] = float(selected["level"])
    plan_details["funding_checkpoint_source"] = str(selected["source"])
    reframed = replace(plan, details=plan_details)
    return FundedCheckpointDecision(
        True,
        reframed,
        "FUNDED_CHECKPOINT_SELECTED",
        float(selected["level"]),
        str(selected["source"]),
        details,
    )


__all__ = [
    *_LOWER_ALL,
    "FundedCheckpointDecision",
    "internal_funding_checkpoint_enabled",
    "select_funded_checkpoint",
]
