"""Initiative-auction-aware NautilusTrader strategy for candidate-07."""
from __future__ import annotations

from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.events import PositionClosed

from initiative_auction import InitiativeAuctionGate
from model import Direction, ScenarioKind, ScenarioState, TradePlan
from strategy import Candidate07Strategy as _BaseCandidate07Strategy
from strategy import Candidate07StrategyConfig


class Candidate07Strategy(_BaseCandidate07Strategy):
    """Prevent repeated fades inside one causally accepted auction leg.

    After a structural stop closes an absorption/reclaim, the opposite
    initiative auction owns all later local pools in that reversal direction.
    The state ends only when the original opposing liquidity is delivered or a
    confirmed acceptance-continuation plan proves initiative has changed sides.
    """

    def __init__(self, config: Candidate07StrategyConfig):
        super().__init__(config)
        self._initiative_gate = InitiativeAuctionGate()

    def on_bar(self, bar: Bar) -> None:
        event_time_ns = int(bar.ts_event)
        close = bar.close.as_double()
        for released in self._initiative_gate.observe_close(close):
            self._append_manual_event(
                scenario_id=released.source_scenario_id,
                previous_state="INITIATIVE_AUCTION_ACTIVE",
                next_state=ScenarioState.IDLE.value,
                reason_code="INITIATIVE_OPPOSING_LIQUIDITY_DELIVERED",
                event_time_ns=event_time_ns,
                reference_price=released.opposing_delivery_price,
                details={
                    "blocked_reversal_direction": (
                        released.blocked_reversal_direction.value
                    ),
                    "initiative_direction": released.initiative_direction.value,
                    "accepted_source_level": released.accepted_source_level,
                    "accepted_at_ns": released.accepted_at_ns,
                    "bar_close": close,
                    "state_owner": "INITIATIVE_AUCTION_LEG",
                },
            )

        # A continuation plan can already be pending when a subclass advances a
        # stopped-absorption acceptance before delegating to this method.
        self._release_on_counter_acceptance(
            self._pending_plan,
            event_time_ns=event_time_ns,
        )
        super().on_bar(bar)
        self._release_on_counter_acceptance(
            self._pending_plan,
            event_time_ns=event_time_ns,
        )

        plan = self._pending_plan
        if plan is None or plan.kind is not ScenarioKind.ABSORPTION_RECLAIM:
            return
        state = self._initiative_gate.state(plan.direction)
        if state is None:
            return
        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state=ScenarioState.ENTRY_READY.value,
            next_state=ScenarioState.INVALIDATED.value,
            reason_code="INITIATIVE_AUCTION_OWNS_REVERSAL_DIRECTION",
            event_time_ns=event_time_ns,
            reference_price=plan.entry_reference,
            details={
                "direction": plan.direction.value,
                "candidate_source_liquidity_level": plan.liquidity_level,
                "initiative_direction": state.initiative_direction.value,
                "accepted_source_level": state.accepted_source_level,
                "opposing_delivery_price": state.opposing_delivery_price,
                "source_scenario_id": state.source_scenario_id,
                "accepted_at_ns": state.accepted_at_ns,
                "state_owner": "INITIATIVE_AUCTION_LEG",
            },
        )
        self._pending_plan = None
        self._pending_created_ns = None

    def on_position_closed(self, event: PositionClosed) -> None:
        plan = self._active_plan
        structural_stop = self._is_structural_stop(event, plan)
        super().on_position_closed(event)

        if (
            plan is None
            or plan.kind is not ScenarioKind.ABSORPTION_RECLAIM
            or not structural_stop
        ):
            return
        delivery = self._structural_delivery_price(plan)
        state = self._initiative_gate.accept_failed_reversal(
            blocked_reversal_direction=plan.direction,
            opposing_delivery_price=delivery,
            source_scenario_id=plan.scenario_id,
            accepted_source_level=plan.liquidity_level,
            accepted_at_ns=int(event.ts_event),
        )
        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state=ScenarioState.TERMINAL.value,
            next_state="INITIATIVE_AUCTION_ACTIVE",
            reason_code="ABSORPTION_STOP_CONFIRMS_INITIATIVE_ACCEPTANCE",
            event_time_ns=int(event.ts_event),
            reference_price=delivery,
            details={
                "blocked_reversal_direction": plan.direction.value,
                "initiative_direction": state.initiative_direction.value,
                "failed_stop_price": plan.stop_price,
                "accepted_source_level": plan.liquidity_level,
                "opposing_delivery_price": delivery,
                "delivery_basis": "opposing internal liquidity known at entry",
                "state_owner": "INITIATIVE_AUCTION_LEG",
            },
        )

    def _release_on_counter_acceptance(
        self,
        plan: TradePlan | None,
        *,
        event_time_ns: int,
    ) -> None:
        if plan is None or plan.kind is not ScenarioKind.ACCEPTANCE_CONTINUATION:
            return
        state = self._initiative_gate.observe_counter_acceptance(plan.direction)
        if state is None:
            return
        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state="INITIATIVE_AUCTION_ACTIVE",
            next_state=ScenarioState.CONFIRMED.value,
            reason_code="COUNTER_ACCEPTANCE_TRANSFERS_INITIATIVE",
            event_time_ns=event_time_ns,
            reference_price=plan.entry_reference,
            details={
                "released_source_scenario_id": state.source_scenario_id,
                "released_initiative_direction": state.initiative_direction.value,
                "new_initiative_direction": plan.direction.value,
                "accepted_source_level": state.accepted_source_level,
                "opposing_delivery_price": state.opposing_delivery_price,
                "state_owner": "INITIATIVE_AUCTION_LEG",
            },
        )

    def _is_structural_stop(
        self,
        event: PositionClosed,
        plan: TradePlan | None,
    ) -> bool:
        if plan is None:
            return False
        closing_order_id = getattr(event, "closing_order_id", None)
        order = self.cache.order(closing_order_id) if closing_order_id is not None else None
        if order is not None:
            raw_tags = getattr(order, "tags", None) or ()
            tags = {str(tag).upper() for tag in raw_tags}
            if "STOP_LOSS" in tags:
                return True
            order_type = str(getattr(order, "order_type", "")).upper()
            if "STOP_MARKET" in order_type:
                return True
            if "TAKE_PROFIT" in tags:
                return False

        raw_last_px = getattr(event, "last_px", plan.entry_reference)
        last_px = (
            raw_last_px.as_double()
            if hasattr(raw_last_px, "as_double")
            else float(raw_last_px)
        )
        if self._instrument is None:
            tolerance = max(abs(plan.stop_price) * 1e-9, 1e-9)
        else:
            tolerance = 2.0 * self._instrument.price_increment.as_double()
        return abs(last_px - plan.stop_price) <= tolerance

    @staticmethod
    def _structural_delivery_price(plan: TradePlan) -> float:
        raw_internal: Any = plan.details.get("opposing_internal")
        candidate = float(raw_internal) if raw_internal is not None else plan.target_price
        if plan.direction is Direction.LONG and candidate <= plan.entry_reference:
            return plan.target_price
        if plan.direction is Direction.SHORT and candidate >= plan.entry_reference:
            return plan.target_price
        return candidate


__all__ = ["Candidate07Strategy", "Candidate07StrategyConfig"]
