"""Impact-saturation failed-auction reversal after an accepted pullback.

V31's immediate reversal after every accepted counterflow pullback was weakly
positive but unstable. V33 showed that a later close-resolution and aggressive-
flow sign still did not observe passive-liquidity resilience. V34 therefore
uses an effort-versus-result state already available from the causal equal-
notional event stream:

1. the three-event outside initiative is detected exactly as in v29-v33;
2. all three initiative events must have aligned aggressive flow and aligned
   price progress;
3. the terminal event carries at least the median aggressive-flow effort of the
   first two, but produces less marginal price response than their median;
4. the first completed opposite-flow pullback preserves outside value and its
   price response per unit flow exceeds the terminal initiative response;
5. enter the reversal on the first later official venue TradeTick;
6. invalidate beyond the completed pullback outside extreme plus one 7-bp
   side-cost buffer;
7. target the opposite edge of the completed pre-initiative 20-event structure.

This is a causal proxy for passive replenishment/absorption: equal or greater
aggressive effort produces less directional progress, then smaller opposite
effort moves price more efficiently. The single control removes only this
impact-saturation asymmetry and enters every otherwise identical accepted-
pullback reversal, reproducing the v31 immediate-reversal logic.

This module creates immutable plans and diagnostics only. NautilusTrader owns
all order matching, costs, margin, positions, PnL and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from core import Side
from impact_elasticity_resumption_v28_nautilus_week import (
    InitiativeSetup,
    SIDE_COST_BUFFER_FRACTION,
    _one_bar_elasticity,
)
from impact_regime_probe import EventFeature, ScenarioPlan
from impact_resumption_stop_state_v29 import PullbackResumptionEntryStateMachine


@dataclass(frozen=True, slots=True)
class InitiativeImpactProfile:
    scenario_id: str
    side: str
    initiative_end_index: int
    initiative_end_time_ns: int
    event_indices: tuple[int, int, int]
    aligned_flow: tuple[float | None, float | None, float | None]
    aligned_price_change_fraction: tuple[
        float | None,
        float | None,
        float | None,
    ]
    marginal_elasticity: tuple[
        float | None,
        float | None,
        float | None,
    ]
    reference_flow_effort: float | None
    terminal_flow_effort: float | None
    reference_marginal_elasticity: float | None
    terminal_marginal_elasticity: float | None
    terminal_effort_not_lower: bool
    terminal_response_decayed: bool
    initiative_saturated: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class ImpactSaturationReversalDecision:
    primary_scenario_id: str | None
    control_scenario_id: str
    source_scenario_id: str
    signal_index: int
    signal_time_ns: int
    source_side: str
    trade_side: str
    accepted_boundary: float
    pullback_high: float
    pullback_low: float
    stop_price: float
    target_price: float
    initiative_saturated: bool
    terminal_flow_effort: float | None
    reference_flow_effort: float | None
    terminal_marginal_elasticity: float | None
    reference_marginal_elasticity: float | None
    counterflow_marginal_elasticity: float
    counterflow_dominates_terminal: bool
    impact_saturation_confirmed: bool
    reason_code: str


class ImpactSaturationReversalStateMachine(PullbackResumptionEntryStateMachine):
    """Filter immediate reversals by causal marginal-impact saturation."""

    def __init__(self) -> None:
        super().__init__()
        self.primary_plans: list[ScenarioPlan] = self.plans
        self.immediate_control_plans: list[ScenarioPlan] = []
        self.impact_profiles: dict[str, InitiativeImpactProfile] = {}
        self.reversal_decisions: list[ImpactSaturationReversalDecision] = []
        self.stop_instructions.clear()
        self.entry_decisions.clear()

    @staticmethod
    def _reversal_side(setup: InitiativeSetup) -> Side:
        return Side.SHORT if setup.side is Side.LONG else Side.LONG

    @staticmethod
    def _target_touched(setup: InitiativeSetup, feature: EventFeature) -> bool:
        """Discard if the reversal destination was consumed before entry."""

        return (
            float(feature.bar.low) <= float(setup.structure_low)
            if setup.side is Side.LONG
            else float(feature.bar.high) >= float(setup.structure_high)
        )

    @staticmethod
    def _stop_and_target(setup: InitiativeSetup) -> tuple[float, float]:
        if setup.pullback_high is None or setup.pullback_low is None:
            raise RuntimeError("v34 reversal requires a completed pullback swing")
        if setup.side is Side.LONG:
            stop = float(setup.pullback_high) * (
                1.0 + SIDE_COST_BUFFER_FRACTION
            )
            target = float(setup.structure_low)
        else:
            stop = float(setup.pullback_low) * (
                1.0 - SIDE_COST_BUFFER_FRACTION
            )
            target = float(setup.structure_high)
        return float(stop), float(target)

    @staticmethod
    def _event_effort_result(
        *,
        side: Side,
        feature: EventFeature,
        previous: EventFeature,
    ) -> tuple[float | None, float | None, float | None]:
        previous_close = float(previous.bar.close)
        raw_flow = side.sign * float(feature.bar.imbalance)
        price_change = (
            side.sign * (float(feature.bar.close) - previous_close) / previous_close
            if previous_close > 0.0
            else 0.0
        )
        if raw_flow <= 0.0 or price_change <= 0.0:
            return None, None, None
        elasticity = price_change / raw_flow
        return float(raw_flow), float(price_change), float(elasticity)

    def _profile_new_initiatives(
        self,
        *,
        index: int,
        features: list[EventFeature],
        prior_active_ids: set[str],
    ) -> None:
        for setup in self.active:
            if setup.scenario_id in prior_active_ids:
                continue
            if setup.created_index != index or index < 2:
                continue
            rows = features[index - 2 : index + 1]
            previous_rows = features[index - 3 : index]
            if len(rows) != 3 or len(previous_rows) != 3:
                continue
            triples = [
                self._event_effort_result(
                    side=setup.side,
                    feature=feature,
                    previous=previous,
                )
                for feature, previous in zip(rows, previous_rows, strict=True)
            ]
            efforts = tuple(row[0] for row in triples)
            changes = tuple(row[1] for row in triples)
            elasticities = tuple(row[2] for row in triples)
            complete = all(value is not None for value in efforts + elasticities)
            if complete:
                first_efforts = [float(efforts[0]), float(efforts[1])]
                first_elasticities = [
                    float(elasticities[0]),
                    float(elasticities[1]),
                ]
                reference_effort = float(median(first_efforts))
                terminal_effort = float(efforts[2])
                reference_elasticity = float(median(first_elasticities))
                terminal_elasticity = float(elasticities[2])
                effort_not_lower = terminal_effort >= reference_effort
                response_decayed = terminal_elasticity < reference_elasticity
                saturated = effort_not_lower and response_decayed
                reason = (
                    "TERMINAL_EFFORT_HELD_OR_GREW_WHILE_MARGINAL_RESPONSE_DECAYED"
                    if saturated
                    else "INITIATIVE_MARGINAL_IMPACT_NOT_SATURATED"
                )
            else:
                reference_effort = None
                terminal_effort = None
                reference_elasticity = None
                terminal_elasticity = None
                effort_not_lower = False
                response_decayed = False
                saturated = False
                reason = "INITIATIVE_EVENTS_NOT_ALL_ALIGNED_EFFORT_AND_RESULT"
            profile = InitiativeImpactProfile(
                scenario_id=setup.scenario_id,
                side=setup.side.value,
                initiative_end_index=index,
                initiative_end_time_ns=int(features[index].bar.end_time_ns),
                event_indices=(index - 2, index - 1, index),
                aligned_flow=efforts,
                aligned_price_change_fraction=changes,
                marginal_elasticity=elasticities,
                reference_flow_effort=reference_effort,
                terminal_flow_effort=terminal_effort,
                reference_marginal_elasticity=reference_elasticity,
                terminal_marginal_elasticity=terminal_elasticity,
                terminal_effort_not_lower=effort_not_lower,
                terminal_response_decayed=response_decayed,
                initiative_saturated=saturated,
                reason_code=reason,
            )
            self.impact_profiles[setup.scenario_id] = profile
            self.counts[
                "initiative_profiles_saturated"
                if saturated
                else "initiative_profiles_not_saturated"
            ] += 1

    def _try_arm(self, *, index: int, features: list[EventFeature]) -> None:
        prior_active_ids = {setup.scenario_id for setup in self.active}
        super()._try_arm(index=index, features=features)
        self._profile_new_initiatives(
            index=index,
            features=features,
            prior_active_ids=prior_active_ids,
        )

    @classmethod
    def _make_plan(
        cls,
        *,
        scenario_id: str,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
        reason_code: str,
    ) -> ScenarioPlan:
        side = cls._reversal_side(setup)
        stop, target = cls._stop_and_target(setup)
        hold = (
            float(feature.bar.high)
            if side is Side.SHORT
            else float(feature.bar.low)
        )
        return ScenarioPlan(
            scenario_id=scenario_id,
            response="REVERSAL",
            side=side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=stop,
            target_price=target,
            confirmation_hold_price=hold,
            structure_high=max(
                float(setup.structure_high),
                float(stop),
                float(target),
            ),
            structure_low=min(
                float(setup.structure_low),
                float(stop),
                float(target),
            ),
            structure_midpoint=0.5 * (
                float(setup.structure_high) + float(setup.structure_low)
            ),
            pulse_high=float(setup.path_high),
            pulse_low=float(setup.path_low),
            pulse_flow_score=float(setup.pulse_flow_score),
            pulse_move_atr=0.0,
            pulse_path_efficiency=float(setup.pulse_efficiency),
            pulse_close_location=0.0,
            reason_code=reason_code,
        )

    def _emit_reversal(
        self,
        *,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
        counterflow_elasticity: float,
    ) -> None:
        profile = self.impact_profiles.get(setup.scenario_id)
        if profile is None:
            raise RuntimeError(f"missing v34 profile for {setup.scenario_id}")
        side = self._reversal_side(setup)
        stop, target = self._stop_and_target(setup)
        entry = float(feature.bar.close)
        geometry_ok = (
            stop < entry < target
            if side is Side.LONG
            else target < entry < stop
        )
        if not geometry_ok:
            self.counts["immediate_reversal_geometry_invalid"] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="IMPACT_SATURATION_REVERSAL_GEOMETRY_INVALID",
                aligned_close_change=None,
                event_elasticity=counterflow_elasticity,
            )
            return

        control_id = setup.scenario_id + f":immediate-reversal-control:{index}"
        terminal = profile.terminal_marginal_elasticity
        counterflow_dominates = (
            terminal is not None and counterflow_elasticity > float(terminal)
        )
        confirmed = profile.initiative_saturated and counterflow_dominates
        primary_id = (
            setup.scenario_id + f":impact-saturation-reversal:{index}"
            if confirmed
            else None
        )
        control_plan = self._make_plan(
            scenario_id=control_id,
            setup=setup,
            feature=feature,
            index=index,
            reason_code="ACCEPTED_PULLBACK_IMMEDIATE_REVERSAL_CONTROL",
        )
        self.immediate_control_plans.append(control_plan)
        self.counts["immediate_reversal_control_plans"] += 1
        if confirmed:
            assert primary_id is not None
            primary_plan = self._make_plan(
                scenario_id=primary_id,
                setup=setup,
                feature=feature,
                index=index,
                reason_code=(
                    "INITIATIVE_IMPACT_SATURATED_AND_COUNTERFLOW_DOMINATED"
                ),
            )
            self.primary_plans.append(primary_plan)
            self.counts["impact_saturation_primary_plans"] += 1
        elif profile.initiative_saturated:
            self.counts["saturated_but_counterflow_not_dominant"] += 1
        else:
            self.counts["pullback_without_initiative_saturation"] += 1

        self.reversal_decisions.append(
            ImpactSaturationReversalDecision(
                primary_scenario_id=primary_id,
                control_scenario_id=control_id,
                source_scenario_id=setup.scenario_id,
                signal_index=index,
                signal_time_ns=int(feature.bar.end_time_ns),
                source_side=setup.side.value,
                trade_side=side.value,
                accepted_boundary=float(setup.boundary),
                pullback_high=float(feature.bar.high),
                pullback_low=float(feature.bar.low),
                stop_price=stop,
                target_price=target,
                initiative_saturated=profile.initiative_saturated,
                terminal_flow_effort=profile.terminal_flow_effort,
                reference_flow_effort=profile.reference_flow_effort,
                terminal_marginal_elasticity=terminal,
                reference_marginal_elasticity=(
                    profile.reference_marginal_elasticity
                ),
                counterflow_marginal_elasticity=float(counterflow_elasticity),
                counterflow_dominates_terminal=counterflow_dominates,
                impact_saturation_confirmed=confirmed,
                reason_code=(
                    "IMPACT_SATURATION_CONFIRMED"
                    if confirmed
                    else "IMMEDIATE_REVERSAL_CONTROL_ONLY"
                ),
            ),
        )
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="IMMEDIATE_REVERSAL_CLASSIFIED",
            reason_code=(
                "INITIATIVE_IMPACT_SATURATED_AND_COUNTERFLOW_DOMINATED"
                if confirmed
                else "IMPACT_SATURATION_NOT_CONFIRMED"
            ),
            aligned_close_change=None,
            event_elasticity=counterflow_elasticity,
        )

    def _update_active(
        self,
        *,
        index: int,
        feature: EventFeature,
        previous: EventFeature,
        features: list[EventFeature] | None = None,
    ) -> None:
        if features is None:
            raise RuntimeError("v34 update requires completed feature history")
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
                    reason_code="IMPACT_SATURATION_RESPONSE_WINDOW_EXPIRED",
                    aligned_close_change=aligned_change,
                    event_elasticity=event_elasticity,
                )
                continue
            if not self._outside_holds(setup, feature):
                self.counts["outside_value_lost_before_pullback"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="ACCEPTED_OUTSIDE_VALUE_LOST_BEFORE_PULLBACK",
                    aligned_close_change=aligned_change,
                    event_elasticity=event_elasticity,
                )
                continue
            if self._target_touched(setup, feature):
                self.counts["reversal_target_consumed_before_pullback"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="REVERSAL_DESTINATION_CONSUMED_BEFORE_PULLBACK",
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
                self.counts["accepted_counterflow_pullbacks"] += 1
                self._emit_reversal(
                    setup=setup,
                    feature=feature,
                    index=index,
                    counterflow_elasticity=float(event_elasticity),
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


__all__ = [
    "ImpactSaturationReversalDecision",
    "ImpactSaturationReversalStateMachine",
    "InitiativeImpactProfile",
]
