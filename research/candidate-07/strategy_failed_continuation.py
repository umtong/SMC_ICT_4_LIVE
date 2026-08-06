"""NautilusTrader strategy with failed-absorption continuation routing."""
from __future__ import annotations

from nautilus_trader.model.data import Bar
from nautilus_trader.model.events import PositionClosed

from failed_continuation import (
    AcceptanceOutcome,
    FailedAbsorptionAcceptance,
    within_signal_shock_window,
)
from model import Direction, ScenarioKind, ScenarioState, TradePlan
from strategy import Candidate07StrategyConfig
from strategy_cascade import Candidate07Strategy as _CascadeCandidate07Strategy


class Candidate07Strategy(_CascadeCandidate07Strategy):
    """Turn an immediate stopped absorption into opposite acceptance.

    Generic breakout continuation remains disabled. A continuation can exist
    only after a real NautilusTrader stop child closes an absorption/reclaim
    position within the same five-minute signal shock, and only if a completed
    one-minute bar subsequently closes beyond that stop boundary before price
    reclaims the original liquidity pool. A later stop belongs to a new market
    episode and cannot retroactively convert the old sweep into continuation.
    """

    ACCEPTANCE_TIMEOUT_BARS = 3

    def __init__(self, config: Candidate07StrategyConfig):
        super().__init__(config)
        self._failed_acceptance: FailedAbsorptionAcceptance | None = None

    def on_bar(self, bar: Bar) -> None:
        self._advance_failed_acceptance(bar)
        super().on_bar(bar)
        if self._failed_acceptance is not None and self._pending_plan is not None:
            self._invalidate_pending(
                "FAILED_ABSORPTION_ACCEPTANCE_RESERVES_SLOT",
                int(bar.ts_event),
            )

    def on_position_closed(self, event: PositionClosed) -> None:
        plan = self._active_plan
        opened_ns = self._position_open_ns
        structural_stop = (
            plan is not None
            and plan.kind is ScenarioKind.ABSORPTION_RECLAIM
            and self._is_structural_stop(event, plan)
        )
        super().on_position_closed(event)
        if plan is None or not structural_stop:
            return

        closed_ns = int(event.ts_event)
        child_scenario_id = self._acceptance_scenario_id_from_source(plan.scenario_id)
        if opened_ns is None or not within_signal_shock_window(
            opened_ns=int(opened_ns),
            closed_ns=closed_ns,
            signal_minutes=self.logic.signal_minutes,
        ):
            hold_minutes = (
                (closed_ns - int(opened_ns)) / 60_000_000_000
                if opened_ns is not None
                else None
            )
            self._append_manual_event(
                scenario_id=child_scenario_id,
                previous_state=ScenarioState.IDLE.value,
                next_state=ScenarioState.INVALIDATED.value,
                reason_code="ABSORPTION_FAILURE_OUTSIDE_SIGNAL_SHOCK",
                event_time_ns=closed_ns,
                reference_price=plan.stop_price,
                details={
                    "source_scenario_id": plan.scenario_id,
                    "opened_ns": opened_ns,
                    "closed_ns": closed_ns,
                    "hold_minutes": hold_minutes,
                    "maximum_same_shock_minutes": self.logic.signal_minutes,
                },
            )
            return

        continuation_direction = (
            Direction.LONG if plan.direction is Direction.SHORT else Direction.SHORT
        )
        state = FailedAbsorptionAcceptance(
            source_scenario_id=plan.scenario_id,
            direction=continuation_direction,
            liquidity_level=plan.liquidity_level,
            acceptance_level=plan.stop_price,
            atr=float(plan.details["atr"]),
            armed_at_ns=closed_ns,
            timeout_bars=self.ACCEPTANCE_TIMEOUT_BARS,
        )
        self._failed_acceptance = state
        self._append_manual_event(
            scenario_id=self._acceptance_scenario_id(state),
            previous_state=ScenarioState.IDLE.value,
            next_state="FAILED_ACCEPTANCE_ARMED",
            reason_code="ABSORPTION_STOP_ARMS_OPPOSITE_ACCEPTANCE",
            event_time_ns=closed_ns,
            reference_price=state.acceptance_level,
            details={
                "source_scenario_id": plan.scenario_id,
                "source_opened_ns": int(opened_ns),
                "source_hold_minutes": (closed_ns - int(opened_ns)) / 60_000_000_000,
                "maximum_same_shock_minutes": self.logic.signal_minutes,
                "continuation_direction": continuation_direction.value,
                "liquidity_level": state.liquidity_level,
                "acceptance_level": state.acceptance_level,
                "atr": state.atr,
                "timeout_bars": state.timeout_bars,
            },
        )

    def _advance_failed_acceptance(self, bar: Bar) -> None:
        state = self._failed_acceptance
        if state is None:
            return
        if not self.portfolio.is_flat(self.config.instrument_id):
            return
        if self._active_plan is not None or self._pending_plan is not None:
            return
        event_time_ns = int(bar.ts_event)
        if not (self.config.trade_start_ns <= event_time_ns < self.config.trade_end_ns):
            self._append_acceptance_terminal(
                state=state,
                event_time_ns=event_time_ns,
                close=bar.close.as_double(),
                reason_code="FAILED_ABSORPTION_ACCEPTANCE_OUTSIDE_WINDOW",
            )
            self._failed_acceptance = None
            return

        observation = state.observe(bar.close.as_double())
        if observation.outcome is AcceptanceOutcome.WAITING:
            return
        if observation.outcome is AcceptanceOutcome.CONFIRMED:
            plan = self._build_failed_continuation_plan(state, bar)
            if plan is None:
                self._append_acceptance_terminal(
                    state=state,
                    event_time_ns=event_time_ns,
                    close=observation.close,
                    reason_code="FAILED_ABSORPTION_CONTINUATION_GEOMETRY_REJECTED",
                )
            else:
                self._pending_plan = plan
                self._pending_created_ns = event_time_ns
                self._append_manual_event(
                    scenario_id=plan.scenario_id,
                    previous_state="FAILED_ACCEPTANCE_ARMED",
                    next_state=ScenarioState.ENTRY_READY.value,
                    reason_code=observation.reason_code,
                    event_time_ns=event_time_ns,
                    reference_price=plan.entry_reference,
                    details={
                        "source_scenario_id": state.source_scenario_id,
                        "bars_seen": observation.bars_seen,
                        "liquidity_level": state.liquidity_level,
                        "acceptance_level": state.acceptance_level,
                        "stop_price": plan.stop_price,
                        "target_price": plan.target_price,
                        "expected_rr": plan.expected_rr,
                    },
                )
            self._failed_acceptance = None
            return

        self._append_acceptance_terminal(
            state=state,
            event_time_ns=event_time_ns,
            close=observation.close,
            reason_code=observation.reason_code,
        )
        self._failed_acceptance = None

    def _build_failed_continuation_plan(
        self,
        state: FailedAbsorptionAcceptance,
        bar: Bar,
    ) -> TradePlan | None:
        entry = bar.close.as_double()
        buffer = self.logic.stop_buffer_atr * state.atr
        if state.direction is Direction.LONG:
            raw_stop = state.liquidity_level - buffer
            minimum_stop = entry - self.logic.minimum_stop_atr * state.atr
            stop = min(raw_stop, minimum_stop)
            risk = entry - stop
            target = entry + risk * self.logic.continuation_target_rr
        else:
            raw_stop = state.liquidity_level + buffer
            minimum_stop = entry + self.logic.minimum_stop_atr * state.atr
            stop = max(raw_stop, minimum_stop)
            risk = stop - entry
            target = entry - risk * self.logic.continuation_target_rr
        if risk <= 0.0 or risk > self.logic.maximum_stop_atr * state.atr:
            return None
        scenario_id = self._acceptance_scenario_id(state)
        return TradePlan(
            scenario_id=scenario_id,
            kind=ScenarioKind.ACCEPTANCE_CONTINUATION,
            direction=state.direction,
            observed_time_ns=int(bar.ts_event),
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            liquidity_level=state.liquidity_level,
            expected_rr=self.logic.continuation_target_rr,
            details={
                "atr": state.atr,
                "route_age_bars": state.bars_seen,
                "source_scenario_id": state.source_scenario_id,
                "acceptance_level": state.acceptance_level,
                "failed_absorption_continuation": True,
                "same_signal_shock": True,
            },
        )

    def _append_acceptance_terminal(
        self,
        *,
        state: FailedAbsorptionAcceptance,
        event_time_ns: int,
        close: float,
        reason_code: str,
    ) -> None:
        self._append_manual_event(
            scenario_id=self._acceptance_scenario_id(state),
            previous_state="FAILED_ACCEPTANCE_ARMED",
            next_state=ScenarioState.INVALIDATED.value,
            reason_code=reason_code,
            event_time_ns=event_time_ns,
            reference_price=close,
            details={
                "source_scenario_id": state.source_scenario_id,
                "continuation_direction": state.direction.value,
                "liquidity_level": state.liquidity_level,
                "acceptance_level": state.acceptance_level,
                "bars_seen": state.bars_seen,
            },
        )

    @staticmethod
    def _acceptance_scenario_id(state: FailedAbsorptionAcceptance) -> str:
        return Candidate07Strategy._acceptance_scenario_id_from_source(
            state.source_scenario_id,
        )

    @staticmethod
    def _acceptance_scenario_id_from_source(source_scenario_id: str) -> str:
        return f"{source_scenario_id}-fac"


__all__ = ["Candidate07Strategy", "Candidate07StrategyConfig"]
