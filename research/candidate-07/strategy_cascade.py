"""Source-owned cascade-aware NautilusTrader strategy for candidate-07."""
from __future__ import annotations

from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.events import PositionClosed

from cascade import FailedAbsorptionGate
from model import Direction, ScenarioKind, ScenarioState, TradePlan
from strategy import Candidate07Strategy as _BaseCandidate07Strategy
from strategy import Candidate07StrategyConfig


class Candidate07Strategy(_BaseCandidate07Strategy):
    """Remember a failed absorption only for its swept liquidity source.

    A stop-loss fill proves that the particular swept pool was accepted instead
    of absorbed. It does not prove that all later liquidity pools on the same
    side must also fail. Repeated reversal plans against the exact same source
    level remain blocked until the original opposing-liquidity objective is
    delivered; an independently formed source in the same direction remains
    eligible. Acceptance continuation remains a separate thesis.
    """

    def __init__(self, config: Candidate07StrategyConfig):
        super().__init__(config)
        self._failed_absorption_gate = FailedAbsorptionGate()

    def on_bar(self, bar: Bar) -> None:
        close = bar.close.as_double()
        for released in self._failed_absorption_gate.observe_close(close):
            self._append_manual_event(
                scenario_id=released.source_scenario_id,
                previous_state="CASCADE_LOCKED",
                next_state=ScenarioState.IDLE.value,
                reason_code="FAILED_ABSORPTION_TARGET_RECLAIMED",
                event_time_ns=int(bar.ts_event),
                reference_price=released.reset_price,
                details={
                    "direction": released.direction.value,
                    "bar_close": close,
                    "blocked_at_ns": released.blocked_at_ns,
                    "source_key": released.source_key,
                    "source_liquidity_level": (
                        released.source_liquidity_level
                    ),
                    "state_owner": "FAILED_SOURCE_LIQUIDITY",
                },
            )

        super().on_bar(bar)

        plan = self._pending_plan
        if plan is None or plan.kind is not ScenarioKind.ABSORPTION_RECLAIM:
            return
        block = self._failed_absorption_gate.state(
            plan.direction,
            plan.liquidity_level,
        )
        if block is None:
            return
        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state=ScenarioState.ENTRY_READY.value,
            next_state=ScenarioState.INVALIDATED.value,
            reason_code="FAILED_ABSORPTION_SOURCE_CASCADE_ACTIVE",
            event_time_ns=int(bar.ts_event),
            reference_price=plan.entry_reference,
            details={
                "direction": plan.direction.value,
                "candidate_source_liquidity_level": plan.liquidity_level,
                "reset_price": block.reset_price,
                "source_scenario_id": block.source_scenario_id,
                "source_key": block.source_key,
                "blocked_at_ns": block.blocked_at_ns,
                "blocked_kind": ScenarioKind.ABSORPTION_RECLAIM.value,
                "state_owner": "FAILED_SOURCE_LIQUIDITY",
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
        reset_price = self._structural_reset_price(plan)
        block = self._failed_absorption_gate.block(
            direction=plan.direction,
            source_liquidity_level=plan.liquidity_level,
            reset_price=reset_price,
            source_scenario_id=plan.scenario_id,
            blocked_at_ns=int(event.ts_event),
        )
        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state=ScenarioState.TERMINAL.value,
            next_state="CASCADE_LOCKED",
            reason_code="ABSORPTION_STOP_CONFIRMED_SOURCE_ACCEPTANCE",
            event_time_ns=int(event.ts_event),
            reference_price=block.reset_price,
            details={
                "direction": plan.direction.value,
                "failed_stop_price": plan.stop_price,
                "source_liquidity_level": plan.liquidity_level,
                "source_key": block.source_key,
                "reset_price": block.reset_price,
                "reset_basis": "opposing internal liquidity known at entry",
                "state_owner": "FAILED_SOURCE_LIQUIDITY",
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
    def _structural_reset_price(plan: TradePlan) -> float:
        raw_internal: Any = plan.details.get("opposing_internal")
        candidate = float(raw_internal) if raw_internal is not None else plan.target_price
        if plan.direction is Direction.LONG and candidate <= plan.entry_reference:
            return plan.target_price
        if plan.direction is Direction.SHORT and candidate >= plan.entry_reference:
            return plan.target_price
        return candidate


__all__ = ["Candidate07Strategy", "Candidate07StrategyConfig"]
