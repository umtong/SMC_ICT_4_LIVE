"""Candidate 16 v3: accepted-failure state with a next-source-liquidity objective.

Candidate 16 v2 proved that the sequential state router reaches true acceptance,
later failure, and an independent trigger, but every trigger was rejected because
the failed source range midpoint/opposite edge no longer offered enough after-cost
space.  This module reuses the v2 router unchanged and replaces exactly one role:
the delivery objective.

The objective path is causal and fixed before the breach:
1. the failed source range's opposite edge;
2. the next still-live completed source-auction boundary in trade direction.

The midpoint is deliberately removed because it belongs to the failed range's
internal balance, not to the next delivery leg after a confirmed accepted-side
failure.  No synthetic R multiple, MFE threshold, fitted percentage, or outcome
lookup is permitted.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Any

from nautilus_trader.model.enums import OrderSide, TimeInForce

from accepted_failure_router import AcceptedFailureScenario, AuctionLevel
from logic import floor_quantity, net_r_at_price, planned_loss_per_unit
from strategy import Candidate16Strategy
from strategy_base import PendingSetup


@dataclass(frozen=True, slots=True)
class DeliveryObjective:
    label: str
    price: float
    level_id: str
    horizon_minutes: int
    observed_index: int


def ordered_next_source_objectives(
    *,
    levels: Iterable[AuctionLevel],
    breached: AuctionLevel,
    interaction_index: int,
    side: int,
    entry: float,
) -> tuple[DeliveryObjective, ...]:
    """Return only source objectives knowable when the breach began.

    The failed range's opposite edge is always considered first when still ahead
    of entry.  Subsequent objectives must be live opposite-side boundaries,
    completed no later than the original interaction, and lie beyond that edge
    in the delivery direction.  Price order, not realized outcome, determines
    priority.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if not math.isfinite(entry) or entry <= 0.0:
        raise ValueError("entry must be finite and positive")

    opposite_edge = breached.range_high if side > 0 else breached.range_low
    required_kind = "HIGH" if side > 0 else "LOW"
    candidates: list[DeliveryObjective] = []
    seen_prices: list[float] = []

    def add(candidate: DeliveryObjective) -> None:
        if not math.isfinite(candidate.price) or side * (candidate.price - entry) <= 0.0:
            return
        if any(math.isclose(candidate.price, price, rel_tol=0.0, abs_tol=1e-9) for price in seen_prices):
            return
        candidates.append(candidate)
        seen_prices.append(candidate.price)

    add(
        DeliveryObjective(
            label="FAILED_SOURCE_OPPOSITE_EDGE",
            price=float(opposite_edge),
            level_id=f"{breached.level_id}:OPPOSITE_EDGE",
            horizon_minutes=int(breached.horizon_minutes),
            observed_index=int(breached.observed_index),
        ),
    )

    external: list[DeliveryObjective] = []
    for level in levels:
        if level.kind != required_kind:
            continue
        if int(level.observed_index) > int(interaction_index):
            continue
        price = float(level.price)
        if side * (price - entry) <= 0.0:
            continue
        # If the paired edge is still ahead, the next objective must sit beyond
        # it.  Otherwise entry has already traversed the range and any causal
        # boundary farther in trade direction is eligible.
        if side * (opposite_edge - entry) > 0.0 and side * (price - opposite_edge) <= 0.0:
            continue
        external.append(
            DeliveryObjective(
                label="NEXT_COMPLETED_SOURCE_BOUNDARY",
                price=price,
                level_id=str(level.level_id),
                horizon_minutes=int(level.horizon_minutes),
                observed_index=int(level.observed_index),
            ),
        )

    external.sort(
        key=lambda item: (
            abs(item.price - entry),
            -item.horizon_minutes,
            item.observed_index,
            item.level_id,
        ),
    )
    for item in external:
        add(item)
    return tuple(candidates)


class Candidate16V3Strategy(Candidate16Strategy):
    """V2 state machine with one causally ordered external objective resolver."""

    def _submit_resolution_entry(
        self,
        state: AcceptedFailureScenario,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        level = state.level
        side = -level.direction
        atr = self._atr()
        entry = float(row["close"])
        if not math.isfinite(atr) or atr <= 0.0:
            self._reject_geometry(row, "INVALID_ENTRY_ATR")
            return True
        if state.failure_high is None or state.failure_low is None:
            self._reject_geometry(row, "MISSING_FAILURE_EXTREME")
            return True

        if side > 0:
            stop = min(level.price, state.failure_low, float(row["low"])) - (
                self.config.stop_buffer_atr * atr
            )
        else:
            stop = max(level.price, state.failure_high, float(row["high"])) + (
                self.config.stop_buffer_atr * atr
            )

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._reject_geometry(row, "INVALID_STOP_GEOMETRY")
            return True

        objectives = ordered_next_source_objectives(
            levels=self._source_levels.values(),
            breached=level,
            interaction_index=int(setup.details["interaction_bar_index"]),
            side=side,
            entry=entry,
        )
        target: float | None = None
        selected: DeliveryObjective | None = None
        target_net_r = -math.inf
        objective_records: list[dict[str, Any]] = []
        for objective in objectives:
            net_r = net_r_at_price(
                entry,
                objective.price,
                side,
                planned_loss,
                cost_rate,
            )
            objective_records.append(
                {
                    "label": objective.label,
                    "price": objective.price,
                    "level_id": objective.level_id,
                    "horizon_minutes": objective.horizon_minutes,
                    "observed_index": objective.observed_index,
                    "net_r": net_r,
                },
            )
            # Existing frozen economic viability gate; this experiment changes
            # the source-liquidity objective, not the minimum R threshold.
            if net_r >= self.config.min_target_net_r:
                target = objective.price
                selected = objective
                target_net_r = net_r
                break
        if target is None or selected is None:
            self._reject_geometry(
                row,
                "NEXT_SOURCE_LIQUIDITY_OBJECTIVE_INSUFFICIENT_AFTER_COSTS",
            )
            return True

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / planned_loss
        quantity_value = floor_quantity(
            raw_quantity,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self._reject_geometry(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return True
        if side > 0 and not (stop < entry < target):
            self._reject_geometry(row, "INVALID_LONG_BRACKET")
            return True
        if side < 0 and not (target < entry < stop):
            self._reject_geometry(row, "INVALID_SHORT_BRACKET")
            return True

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.GTC,
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = "ACCEPTED_FAILURE_NEXT_SOURCE_DELIVERY"
        self.current_pool_level = level.price
        self.pending = None
        self._resolution = None
        self._last_resolution_index = self.bar_index
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate16_v2_entries"] = int(
            self.diagnostics["candidate16_v2_entries"],
        ) + 1
        self.diagnostics["candidate16_v3_next_source_entries"] = int(
            self.diagnostics.get("candidate16_v3_next_source_entries", 0),
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            state.reason,
            entry,
            {
                **setup.details,
                "candidate16_branch": self.current_branch,
                "entry_trigger": state.trigger_kind,
                "side": side,
                "entry_estimate": entry,
                "stop": stop,
                "target": target,
                "target_source": selected.label,
                "target_level_id": selected.level_id,
                "target_horizon_minutes": selected.horizon_minutes,
                "target_observed_index": selected.observed_index,
                "target_net_r": target_net_r,
                "causal_objective_candidates": objective_records,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
            },
        )
        return True


__all__ = [
    "Candidate16V3Strategy",
    "DeliveryObjective",
    "ordered_next_source_objectives",
]
