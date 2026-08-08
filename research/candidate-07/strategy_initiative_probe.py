"""Persistent initiative ownership with shadow-reversal state probes."""
from __future__ import annotations

from nautilus_trader.model.data import Bar
from nautilus_trader.model.events import PositionClosed

from failed_continuation import within_signal_shock_window
from initiative_probe import (
    PersistentInitiativeAuctionGate,
    ProbeOutcome,
    ShadowReversalProbe,
)
from model import Direction, ScenarioKind, ScenarioState, TradePlan
from strategy import Candidate07StrategyConfig
from strategy_failed_continuation_initiative import (
    Candidate07Strategy as _InitiativeContinuationStrategy,
)


class Candidate07Strategy(_InitiativeContinuationStrategy):
    """Use a blocked reversal as information before risking the counter-trend.

    A real stopped reversal installs one global initiative owner. Later reversal
    plans against that owner are not thrown away and are not traded blindly.
    Their already-declared structural stop and target become a causal shadow
    probe. Target-first proves counter-initiative and releases the old owner;
    stop-first reconfirms the owner and can route the opposite continuation when
    acceptance occurs inside the same signal shock.
    """

    def __init__(self, config: Candidate07StrategyConfig):
        super().__init__(config)
        self._initiative_gate = PersistentInitiativeAuctionGate()
        self._shadow_probe: ShadowReversalProbe | None = None

    @property
    def _persistent_gate(self) -> PersistentInitiativeAuctionGate:
        gate = self._initiative_gate
        if not isinstance(gate, PersistentInitiativeAuctionGate):
            raise RuntimeError("persistent initiative gate was replaced")
        return gate

    def on_bar(self, bar: Bar) -> None:
        event_time_ns = int(bar.ts_event)
        self._advance_shadow_probe(bar)

        gate = self._persistent_gate
        gate.defer_blocking = True
        try:
            super().on_bar(bar)
        finally:
            gate.defer_blocking = False

        notice = gate.consume_source_reclaim_notice()
        if notice is not None:
            self._append_manual_event(
                scenario_id=notice.source_scenario_id,
                previous_state="INITIATIVE_AUCTION_ACTIVE",
                next_state="INITIATIVE_AUCTION_ACTIVE",
                reason_code="INITIATIVE_SOURCE_RECLAIM_UNRESOLVED",
                event_time_ns=event_time_ns,
                reference_price=notice.accepted_source_level,
                details={
                    "blocked_reversal_direction": (
                        notice.blocked_reversal_direction.value
                    ),
                    "initiative_direction": notice.initiative_direction.value,
                    "accepted_source_level": notice.accepted_source_level,
                    "accepted_at_ns": notice.accepted_at_ns,
                    "bar_close": bar.close.as_double(),
                    "state_owner": "PERSISTENT_INITIATIVE_EPOCH",
                    "interpretation": (
                        "outside acceptance reclaimed but initiative transfer "
                        "remains unproven"
                    ),
                },
            )

        self._discard_probe_if_owner_changed(event_time_ns)
        self._capture_blocked_reversal(event_time_ns)

    def on_position_closed(self, event: PositionClosed) -> None:
        super().on_position_closed(event)
        self._discard_probe_if_owner_changed(int(event.ts_event))

    def _capture_blocked_reversal(self, event_time_ns: int) -> None:
        plan = self._pending_plan
        if plan is None or plan.kind is not ScenarioKind.ABSORPTION_RECLAIM:
            return
        state = self._persistent_gate.actual_state(plan.direction)
        if state is None:
            return

        existing = self._shadow_probe
        if existing is not None:
            if existing.new_source_is_more_extreme(plan):
                self._append_manual_event(
                    scenario_id=existing.plan.scenario_id,
                    previous_state="SHADOW_PROBE_ACTIVE",
                    next_state=ScenarioState.INVALIDATED.value,
                    reason_code="MORE_EXTREME_SHADOW_SOURCE_SUPERSEDES_PROBE",
                    event_time_ns=event_time_ns,
                    reference_price=plan.liquidity_level,
                    details={
                        "replacement_scenario_id": plan.scenario_id,
                        "old_liquidity_level": existing.plan.liquidity_level,
                        "new_liquidity_level": plan.liquidity_level,
                        "direction": plan.direction.value,
                    },
                )
                self._shadow_probe = None
            else:
                self._append_manual_event(
                    scenario_id=plan.scenario_id,
                    previous_state=ScenarioState.ENTRY_READY.value,
                    next_state=ScenarioState.INVALIDATED.value,
                    reason_code="ACTIVE_SHADOW_PROBE_OWNS_REVERSAL_DIRECTION",
                    event_time_ns=event_time_ns,
                    reference_price=plan.entry_reference,
                    details={
                        "active_probe_scenario_id": existing.plan.scenario_id,
                        "active_probe_liquidity_level": (
                            existing.plan.liquidity_level
                        ),
                        "candidate_liquidity_level": plan.liquidity_level,
                        "direction": plan.direction.value,
                    },
                )
                self._pending_plan = None
                self._pending_created_ns = None
                return

        self._shadow_probe = ShadowReversalProbe(
            plan=plan,
            owner_scenario_id=state.source_scenario_id,
            armed_at_ns=event_time_ns,
        )
        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state=ScenarioState.ENTRY_READY.value,
            next_state="SHADOW_PROBE_ACTIVE",
            reason_code="INITIATIVE_OWNS_REVERSAL_SHADOW_ONLY",
            event_time_ns=event_time_ns,
            reference_price=plan.entry_reference,
            details={
                "direction": plan.direction.value,
                "entry_reference": plan.entry_reference,
                "structural_stop": plan.stop_price,
                "declared_target": plan.target_price,
                "liquidity_level": plan.liquidity_level,
                "initiative_owner_scenario_id": state.source_scenario_id,
                "initiative_direction": state.initiative_direction.value,
                "accepted_source_level": state.accepted_source_level,
                "state_owner": "PERSISTENT_INITIATIVE_EPOCH",
                "capital_risked": False,
            },
        )
        self._pending_plan = None
        self._pending_created_ns = None

    def _advance_shadow_probe(self, bar: Bar) -> None:
        probe = self._shadow_probe
        if probe is None:
            return
        event_time_ns = int(bar.ts_event)
        state = self._persistent_gate.actual_state(probe.plan.direction)
        if state is None or state.source_scenario_id != probe.owner_scenario_id:
            self._invalidate_shadow_probe(
                "INITIATIVE_OWNER_CHANGED",
                event_time_ns,
                bar.close.as_double(),
            )
            return
        if not (
            self.config.trade_start_ns
            <= event_time_ns
            < self.config.trade_end_ns
        ):
            self._invalidate_shadow_probe(
                "SHADOW_PROBE_OUTSIDE_TRADE_WINDOW",
                event_time_ns,
                bar.close.as_double(),
            )
            return

        observation = probe.observe_close(bar.close.as_double())
        if observation.outcome is ProbeOutcome.WAITING:
            return

        self._append_manual_event(
            scenario_id=probe.plan.scenario_id,
            previous_state="SHADOW_PROBE_ACTIVE",
            next_state=ScenarioState.TERMINAL.value,
            reason_code=observation.reason_code,
            event_time_ns=event_time_ns,
            reference_price=observation.close,
            details={
                "bars_seen": observation.bars_seen,
                "direction": probe.plan.direction.value,
                "structural_stop": probe.plan.stop_price,
                "declared_target": probe.plan.target_price,
                "liquidity_level": probe.plan.liquidity_level,
                "capital_risked": False,
            },
        )

        if observation.outcome is ProbeOutcome.TARGET_DELIVERED:
            released = self._persistent_gate.clear(probe.plan.direction)
            if released is not None:
                self._append_manual_event(
                    scenario_id=released.source_scenario_id,
                    previous_state="INITIATIVE_AUCTION_ACTIVE",
                    next_state=ScenarioState.IDLE.value,
                    reason_code="SHADOW_REVERSAL_PROVES_COUNTER_INITIATIVE",
                    event_time_ns=event_time_ns,
                    reference_price=probe.plan.target_price,
                    details={
                        "blocked_reversal_direction": (
                            released.blocked_reversal_direction.value
                        ),
                        "released_initiative_direction": (
                            released.initiative_direction.value
                        ),
                        "probe_scenario_id": probe.plan.scenario_id,
                        "probe_target": probe.plan.target_price,
                        "bars_seen": observation.bars_seen,
                        "state_owner": "PERSISTENT_INITIATIVE_EPOCH",
                    },
                )
            self._shadow_probe = None
            return

        # Stop-first reconfirms the initiative owner. The shadow probe itself
        # terminally failed, but no account loss was incurred.
        self._append_manual_event(
            scenario_id=state.source_scenario_id,
            previous_state="INITIATIVE_AUCTION_ACTIVE",
            next_state="INITIATIVE_AUCTION_ACTIVE",
            reason_code="SHADOW_FAILURE_RECONFIRMS_INITIATIVE",
            event_time_ns=event_time_ns,
            reference_price=probe.plan.stop_price,
            details={
                "blocked_reversal_direction": (
                    state.blocked_reversal_direction.value
                ),
                "initiative_direction": state.initiative_direction.value,
                "probe_scenario_id": probe.plan.scenario_id,
                "probe_stop": probe.plan.stop_price,
                "bars_seen": observation.bars_seen,
                "capital_loss_avoided": True,
                "state_owner": "PERSISTENT_INITIATIVE_EPOCH",
            },
        )

        child_scenario_id = f"{probe.plan.scenario_id}-spfac"
        same_shock = within_signal_shock_window(
            opened_ns=probe.plan.observed_time_ns,
            closed_ns=event_time_ns,
            signal_minutes=self.logic.signal_minutes,
        )
        flat = self.portfolio.is_flat(self.config.instrument_id)
        slot_free = (
            flat
            and self._active_plan is None
            and self._pending_plan is None
        )
        if not same_shock:
            self._append_manual_event(
                scenario_id=child_scenario_id,
                previous_state=ScenarioState.IDLE.value,
                next_state=ScenarioState.INVALIDATED.value,
                reason_code="SHADOW_ACCEPTANCE_OUTSIDE_SIGNAL_SHOCK",
                event_time_ns=event_time_ns,
                reference_price=probe.plan.stop_price,
                details={
                    "source_scenario_id": probe.plan.scenario_id,
                    "observed_ns": probe.plan.observed_time_ns,
                    "accepted_ns": event_time_ns,
                    "bars_seen": observation.bars_seen,
                },
            )
        elif not slot_free:
            self._append_manual_event(
                scenario_id=child_scenario_id,
                previous_state=ScenarioState.IDLE.value,
                next_state=ScenarioState.INVALIDATED.value,
                reason_code="SHADOW_ACCEPTANCE_SLOT_OCCUPIED",
                event_time_ns=event_time_ns,
                reference_price=probe.plan.stop_price,
                details={
                    "source_scenario_id": probe.plan.scenario_id,
                    "portfolio_flat": flat,
                    "active_plan": (
                        self._active_plan.scenario_id
                        if self._active_plan is not None
                        else None
                    ),
                    "pending_plan": (
                        self._pending_plan.scenario_id
                        if self._pending_plan is not None
                        else None
                    ),
                },
            )
        else:
            continuation = self._build_shadow_continuation_plan(
                probe,
                bar,
                child_scenario_id,
            )
            if continuation is None:
                self._append_manual_event(
                    scenario_id=child_scenario_id,
                    previous_state=ScenarioState.IDLE.value,
                    next_state=ScenarioState.INVALIDATED.value,
                    reason_code="SHADOW_CONTINUATION_GEOMETRY_REJECTED",
                    event_time_ns=event_time_ns,
                    reference_price=bar.close.as_double(),
                    details={
                        "source_scenario_id": probe.plan.scenario_id,
                        "bars_seen": observation.bars_seen,
                    },
                )
            else:
                self._pending_plan = continuation
                self._pending_created_ns = event_time_ns
                self._append_manual_event(
                    scenario_id=continuation.scenario_id,
                    previous_state=ScenarioState.IDLE.value,
                    next_state=ScenarioState.ENTRY_READY.value,
                    reason_code="SHADOW_STOP_ACCEPTANCE_CONTINUATION_READY",
                    event_time_ns=event_time_ns,
                    reference_price=continuation.entry_reference,
                    details={
                        "source_scenario_id": probe.plan.scenario_id,
                        "initiative_owner_scenario_id": state.source_scenario_id,
                        "bars_seen": observation.bars_seen,
                        "liquidity_level": continuation.liquidity_level,
                        "stop_price": continuation.stop_price,
                        "target_price": continuation.target_price,
                        "expected_rr": continuation.expected_rr,
                    },
                )
        self._shadow_probe = None

    def _build_shadow_continuation_plan(
        self,
        probe: ShadowReversalProbe,
        bar: Bar,
        scenario_id: str,
    ) -> TradePlan | None:
        atr = float(probe.plan.details["atr"])
        entry = bar.close.as_double()
        direction = probe.continuation_direction
        buffer = self.logic.stop_buffer_atr * atr
        if direction is Direction.LONG:
            raw_stop = probe.plan.liquidity_level - buffer
            minimum_stop = entry - self.logic.minimum_stop_atr * atr
            stop = min(raw_stop, minimum_stop)
            risk = entry - stop
            target = entry + risk * self.logic.continuation_target_rr
        else:
            raw_stop = probe.plan.liquidity_level + buffer
            minimum_stop = entry + self.logic.minimum_stop_atr * atr
            stop = max(raw_stop, minimum_stop)
            risk = stop - entry
            target = entry - risk * self.logic.continuation_target_rr
        if risk <= 0.0 or risk > self.logic.maximum_stop_atr * atr:
            return None
        return TradePlan(
            scenario_id=scenario_id,
            kind=ScenarioKind.ACCEPTANCE_CONTINUATION,
            direction=direction,
            observed_time_ns=int(bar.ts_event),
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            liquidity_level=probe.plan.liquidity_level,
            expected_rr=self.logic.continuation_target_rr,
            details={
                "atr": atr,
                "route_age_bars": probe.bars_seen,
                "source_scenario_id": probe.plan.scenario_id,
                "initiative_owner_scenario_id": probe.owner_scenario_id,
                "acceptance_level": probe.plan.stop_price,
                "shadow_probe_continuation": True,
                "same_signal_shock": True,
                "capital_loss_preceding_continuation": False,
            },
        )

    def _discard_probe_if_owner_changed(self, event_time_ns: int) -> None:
        probe = self._shadow_probe
        if probe is None:
            return
        state = self._persistent_gate.actual_state(probe.plan.direction)
        if state is not None and state.source_scenario_id == probe.owner_scenario_id:
            return
        self._invalidate_shadow_probe(
            "INITIATIVE_OWNER_CHANGED",
            event_time_ns,
            probe.plan.entry_reference,
        )

    def _invalidate_shadow_probe(
        self,
        reason_code: str,
        event_time_ns: int,
        reference_price: float,
    ) -> None:
        probe = self._shadow_probe
        if probe is None:
            return
        self._append_manual_event(
            scenario_id=probe.plan.scenario_id,
            previous_state="SHADOW_PROBE_ACTIVE",
            next_state=ScenarioState.INVALIDATED.value,
            reason_code=reason_code,
            event_time_ns=event_time_ns,
            reference_price=reference_price,
            details={
                "direction": probe.plan.direction.value,
                "owner_scenario_id": probe.owner_scenario_id,
                "bars_seen": probe.bars_seen,
                "capital_risked": False,
            },
        )
        self._shadow_probe = None


__all__ = ["Candidate07Strategy", "Candidate07StrategyConfig"]
