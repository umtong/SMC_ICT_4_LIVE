"""Causal v29 state machine for conditional impact-resumption entries.

The market-state logic is inherited from the v28 sequence-only ablation:

    cost-resolved outside initiative
    -> completed opposite-flow pullback which retains outside value.

The v28 result showed that waiting for a later completed resumption event used
most of the remaining target distance.  V29 therefore ends scenario detection
at the completed pullback and creates two immutable execution intents:

* a STOP_LIMIT instruction one tick beyond the completed pullback extreme;
* an immediate-market plan at the same observation time for the single
  entry-timing ablation.

No fill, PnL or NAV logic is implemented here.  NautilusTrader owns every order,
fill, commission, margin, position and account calculation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import ceil, floor
from statistics import median
from typing import Iterable

from core import Side
from impact_elasticity_resumption_v28_nautilus_week import (
    InitiativeSetup,
    ImpactElasticityStateMachine,
    SIDE_COST_BUFFER_FRACTION,
    _one_bar_elasticity,
)
from impact_regime_probe import EventFeature, ScenarioPlan
from nautilus_tick_stop_plan_backtest import StopEntryInstruction


BTC_TICK_SIZE = 0.1
STOP_LIMIT_PROTECTION_FRACTION = 7.0 / 10_000.0
DURATION_LOOKBACK_EVENTS = 20


@dataclass(frozen=True, slots=True)
class EntryDecision:
    scenario_id: str
    source_scenario_id: str
    signal_index: int
    signal_time_ns: int
    side: str
    boundary: float
    pullback_high: float
    pullback_low: float
    trigger_price: float
    worst_limit_price: float
    stop_price: float
    target_price: float
    expiry_time_ns: int
    remaining_response_events: int
    causal_median_event_duration_ns: int
    initiative_elasticity: float
    adverse_elasticity: float
    reason_code: str


def next_tick_above(price: float) -> float:
    """Return the first BTC tick strictly above a completed observed high."""

    ticks = floor(price / BTC_TICK_SIZE + 1e-9)
    return round((ticks + 1) * BTC_TICK_SIZE, 1)


def next_tick_below(price: float) -> float:
    """Return the first BTC tick strictly below a completed observed low."""

    ticks = ceil(price / BTC_TICK_SIZE - 1e-9)
    return round((ticks - 1) * BTC_TICK_SIZE, 1)


def causal_event_duration_ns(
    features: list[EventFeature],
    *,
    index: int,
) -> int:
    """Median duration of only already completed equal-notional events."""

    start = max(0, index - DURATION_LOOKBACK_EVENTS + 1)
    durations = [
        max(
            int(row.bar.end_time_ns) - int(row.bar.start_time_ns),
            1,
        )
        for row in features[start : index + 1]
    ]
    if not durations:
        raise RuntimeError("v29 requires at least one completed event duration")
    return max(int(median(durations)), 1)


class PullbackResumptionEntryStateMachine(ImpactElasticityStateMachine):
    """Emit conditional resumption entries at the first accepted pullback."""

    def __init__(self) -> None:
        # V28's one-variable ablation established that the state sequence, not
        # the elasticity threshold, was the surviving component.  V29 keeps
        # that sequence frozen and changes only entry timing.
        super().__init__(rule="sequence-only-control")
        self.stop_instructions: list[StopEntryInstruction] = []
        self.market_plans: list[ScenarioPlan] = self.plans
        self.entry_decisions: list[EntryDecision] = []

    def _try_arm(self, *, index: int, features: list[EventFeature]) -> None:
        """Use inherited causal initiative detection with truthful labels."""

        initiative_start = len(self.initiatives)
        transition_start = len(self.transitions)
        super()._try_arm(index=index, features=features)
        for position in range(initiative_start, len(self.initiatives)):
            row = self.initiatives[position]
            if row.classification == "ARMED":
                self.initiatives[position] = replace(
                    row,
                    reason_code="COST_RESOLVED_OUTSIDE_FLOW_INITIATIVE",
                )
        for position in range(transition_start, len(self.transitions)):
            row = self.transitions[position]
            if row.event_type == "INITIATIVE_ARMED":
                self.transitions[position] = replace(
                    row,
                    reason_code="COST_RESOLVED_OUTSIDE_FLOW_INITIATIVE",
                )

    @staticmethod
    def _stop_and_target(setup: InitiativeSetup) -> tuple[float, float]:
        target = (
            setup.boundary + setup.structure_width
            if setup.side is Side.LONG
            else setup.boundary - setup.structure_width
        )
        if setup.side is Side.LONG:
            invalidation = min(setup.path_low, setup.boundary)
            stop = invalidation * (1.0 - SIDE_COST_BUFFER_FRACTION)
        else:
            invalidation = max(setup.path_high, setup.boundary)
            stop = invalidation * (1.0 + SIDE_COST_BUFFER_FRACTION)
        return float(stop), float(target)

    @staticmethod
    def _trigger_and_limit(
        setup: InitiativeSetup,
        feature: EventFeature,
    ) -> tuple[float, float]:
        if setup.side is Side.LONG:
            trigger = next_tick_above(float(feature.bar.high))
            limit_price = trigger * (1.0 + STOP_LIMIT_PROTECTION_FRACTION)
        else:
            trigger = next_tick_below(float(feature.bar.low))
            limit_price = trigger * (1.0 - STOP_LIMIT_PROTECTION_FRACTION)
        return float(trigger), float(limit_price)

    def _arm_conditional_entry(
        self,
        *,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
        features: list[EventFeature],
        event_elasticity: float,
    ) -> None:
        stop, target = self._stop_and_target(setup)
        trigger, limit_price = self._trigger_and_limit(setup, feature)
        geometry_ok = (
            stop < trigger <= limit_price < target
            if setup.side is Side.LONG
            else target < limit_price <= trigger < stop
        )
        if not geometry_ok:
            self.counts["conditional_entry_geometry_invalid"] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="CONDITIONAL_RESUMPTION_GEOMETRY_INVALID",
                aligned_close_change=None,
                event_elasticity=event_elasticity,
            )
            return

        remaining_events = max(int(setup.expiry_index) - index, 1)
        median_duration_ns = causal_event_duration_ns(features, index=index)
        expiry_time_ns = (
            int(feature.bar.end_time_ns)
            + remaining_events * median_duration_ns
        )
        scenario_id = setup.scenario_id + f":pullback-stop:{index}"
        plan = ScenarioPlan(
            scenario_id=scenario_id,
            response="CONTINUATION",
            side=setup.side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=stop,
            target_price=target,
            confirmation_hold_price=float(setup.boundary),
            structure_high=float(setup.structure_high),
            structure_low=float(setup.structure_low),
            structure_midpoint=0.5 * (
                setup.structure_high + setup.structure_low
            ),
            pulse_high=float(setup.path_high),
            pulse_low=float(setup.path_low),
            pulse_flow_score=float(setup.pulse_flow_score),
            pulse_move_atr=0.0,
            pulse_path_efficiency=float(setup.pulse_efficiency),
            pulse_close_location=0.0,
            reason_code="OUTSIDE_IMPACT_PULLBACK_ARMED_FOR_RESUMPTION",
        )
        instruction = StopEntryInstruction(
            plan=plan,
            trigger_price=trigger,
            limit_price=limit_price,
            expiry_time_ns=expiry_time_ns,
            entry_reason="BREAK_COMPLETED_COUNTERFLOW_PULLBACK_EXTREME",
        )
        self.market_plans.append(plan)
        self.stop_instructions.append(instruction)
        adverse = float(event_elasticity)
        self.entry_decisions.append(
            EntryDecision(
                scenario_id=scenario_id,
                source_scenario_id=setup.scenario_id,
                signal_index=index,
                signal_time_ns=int(feature.bar.end_time_ns),
                side=setup.side.value,
                boundary=float(setup.boundary),
                pullback_high=float(feature.bar.high),
                pullback_low=float(feature.bar.low),
                trigger_price=trigger,
                worst_limit_price=limit_price,
                stop_price=stop,
                target_price=target,
                expiry_time_ns=expiry_time_ns,
                remaining_response_events=remaining_events,
                causal_median_event_duration_ns=median_duration_ns,
                initiative_elasticity=float(setup.initiative_elasticity),
                adverse_elasticity=adverse,
                reason_code=plan.reason_code,
            ),
        )
        self.counts["counterflow_pullbacks"] += 1
        self.counts["stop_limit_instructions_armed"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="STOP_LIMIT_INSTRUCTION_ARMED",
            reason_code=plan.reason_code,
            aligned_close_change=None,
            event_elasticity=event_elasticity,
        )

    def _update_active(
        self,
        *,
        index: int,
        feature: EventFeature,
        previous: EventFeature,
        features: list[EventFeature] | None = None,
    ) -> None:
        # The inherited on_feature signature does not pass the feature list to
        # _update_active.  on_feature below calls this method directly with the
        # completed causal history.
        if features is None:
            raise RuntimeError("v29 update requires completed feature history")
        remaining: list[InitiativeSetup] = []
        event_elasticity = _one_bar_elasticity(feature, previous)
        for setup in self.active:
            if index <= setup.created_index:
                remaining.append(setup)
                continue
            previous_close = float(previous.bar.close)
            aligned_change = setup.side.sign * (
                float(feature.bar.close) - previous_close
            )
            aligned_z = self._aligned_z(setup.side, feature)
            setup.path_high = max(setup.path_high, float(feature.bar.high))
            setup.path_low = min(setup.path_low, float(feature.bar.low))

            if index > setup.expiry_index:
                self.counts["response_window_expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="PULLBACK_ENTRY_WINDOW_EXPIRED",
                    aligned_close_change=aligned_change,
                    event_elasticity=event_elasticity,
                )
                continue
            if not self._outside_holds(setup, feature):
                self.counts["outside_value_lost"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="ACCEPTED_OUTSIDE_VALUE_LOST",
                    aligned_close_change=aligned_change,
                    event_elasticity=event_elasticity,
                )
                continue
            if self._target_touched(setup, feature):
                self.counts["target_consumed_before_entry"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="MEASURED_TARGET_CONSUMED_BEFORE_ENTRY",
                    aligned_close_change=aligned_change,
                    event_elasticity=event_elasticity,
                )
                continue

            counterflow_pullback = (
                aligned_z is not None
                and aligned_z < 0.0
                and aligned_change < 0.0
                and event_elasticity is not None
            )
            if counterflow_pullback:
                setup.pullback_index = index
                setup.pullback_time_ns = int(feature.bar.end_time_ns)
                setup.pullback_high = float(feature.bar.high)
                setup.pullback_low = float(feature.bar.low)
                setup.adverse_elasticity = float(event_elasticity)
                self._arm_conditional_entry(
                    setup=setup,
                    feature=feature,
                    index=index,
                    features=features,
                    event_elasticity=float(event_elasticity),
                )
                continue
            remaining.append(setup)
        self.active = remaining

    def on_feature(
        self,
        *,
        index: int,
        features: list[EventFeature],
    ) -> None:
        feature = features[index]
        if index <= 0:
            return
        previous = features[index - 1]
        self._update_active(
            index=index,
            feature=feature,
            previous=previous,
            features=features,
        )
        self._try_arm(index=index, features=features)
        elasticity = _one_bar_elasticity(feature, previous)
        if elasticity is not None:
            self.elasticity_history.append(float(elasticity))


def evaluation_instructions(
    rows: Iterable[StopEntryInstruction],
    *,
    start_ns: int,
    end_ns: int,
) -> list[StopEntryInstruction]:
    return [
        row
        for row in rows
        if start_ns <= int(row.plan.signal_time_ns) < end_ns
    ]


def evaluation_plans(
    rows: Iterable[ScenarioPlan],
    *,
    start_ns: int,
    end_ns: int,
) -> list[ScenarioPlan]:
    return [
        row
        for row in rows
        if start_ns <= int(row.signal_time_ns) < end_ns
    ]
