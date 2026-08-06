"""Causal auction-state overlay for aggressor-flow liquidity routing.

The existing router handles low-efficiency failed-aggression reversals. This
module adds the complementary high-efficiency auction and a failed-thesis
branch without changing order, fill, cash, fee, or portfolio accounting.

Two independent scenarios are modeled:

1. External acceptance with counterflow mitigation
   A previously formed external pool is first contacted by directional
   displacement and matching aggressor flow. The next completed signal bar
   pulls back with opposite flow but remains outside the broken pool. This is a
   mitigated displacement, not a market-order chase.

2. Failed absorption accepted in the original attack direction
   A confirmed absorption thesis first reaches its structural invalidation.
   Only a completed close beyond that boundary with renewed original-direction
   flow, followed by an outside hold, creates the continuation route.

All observations use completed bars and past-only rolling state. A pool is
consumed at its first causal contact and a reclaimed pool terminates its episode.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import sqrt
from statistics import fmean
from typing import Any, Mapping

from model import Direction, ScenarioKind, ScenarioState, TradePlan, Transition
from model_flow import FlowLogicConfig, FlowObservation, FlowSignalBar
from model_flow_target_ladder import TargetLadderAggressorFlowRouter


CONTINUATION_TARGET_RR = 1.0


@dataclass(slots=True)
class _ExternalAcceptance:
    scenario_id: str
    direction: Direction
    created_index: int
    liquidity_level: float
    liquidity_formed_ns: int
    atr: float
    lower_range: float
    upper_range: float
    contact_close: float


@dataclass(slots=True)
class _FailedAbsorption:
    source_scenario_id: str
    source_direction: Direction
    continuation_direction: Direction
    liquidity_level: float
    acceptance_level: float
    atr: float
    armed_index: int
    timeout_bars: int
    source_target: float | None
    boundary_touched: bool
    awaiting_hold: bool = False
    trigger_index: int | None = None

    @property
    def scenario_id(self) -> str:
        return f"{self.source_scenario_id}-fac"


@dataclass(frozen=True, slots=True)
class _OverlayObservation:
    plan: TradePlan | None
    transitions: tuple[Transition, ...]
    diagnostics: Mapping[str, Any]


class CausalAuctionContinuationOverlay:
    """Complement absorption reversal with accepted-auction state transitions."""

    def __init__(self, config: FlowLogicConfig, *, failed_timeout_bars: int):
        config.validate()
        if failed_timeout_bars <= 0:
            raise ValueError("failed_timeout_bars must be positive")
        self.config = config
        capacity = max(
            config.min_history,
            config.external_lookback,
            config.flow_period,
            config.atr_period,
        ) + 8
        self._history: deque[FlowSignalBar] = deque(maxlen=capacity)
        self._last_ts = -1
        self._external_counter = 0
        self._external_episode: _ExternalAcceptance | None = None
        self._external_consumed: set[tuple[Direction, int]] = set()
        self._failed: dict[str, _FailedAbsorption] = {}
        self._failed_timeout_bars = failed_timeout_bars

    @property
    def consumed_pool_count(self) -> int:
        return len(self._external_consumed)

    @property
    def failed_state_count(self) -> int:
        return len(self._failed)

    def arm_failed_from_geometry(
        self,
        *,
        source_scenario_id: str,
        source_direction: Direction,
        liquidity_level: float,
        acceptance_level: float,
        atr: float,
        index: int,
        opposing_internal: float | None,
        opposing_external: float | None,
    ) -> _FailedAbsorption:
        source_target = self._nearest_source_target(
            source_direction=source_direction,
            liquidity_level=liquidity_level,
            levels=(opposing_internal, opposing_external),
        )
        state = _FailedAbsorption(
            source_scenario_id=source_scenario_id,
            source_direction=source_direction,
            continuation_direction=(
                Direction.LONG
                if source_direction is Direction.SHORT
                else Direction.SHORT
            ),
            liquidity_level=liquidity_level,
            acceptance_level=acceptance_level,
            atr=atr,
            armed_index=index,
            timeout_bars=max(1, self._failed_timeout_bars),
            source_target=source_target,
            boundary_touched=False,
        )
        self._failed[source_scenario_id] = state
        return state

    def arm_failed_from_plan(
        self,
        plan: TradePlan,
        *,
        index: int,
        boundary_touched: bool,
    ) -> _FailedAbsorption:
        raw_internal = plan.details.get("opposing_internal")
        raw_external = plan.details.get("opposing_external")
        return self._arm_failed(
            source_scenario_id=plan.scenario_id,
            source_direction=plan.direction,
            liquidity_level=plan.liquidity_level,
            acceptance_level=plan.stop_price,
            atr=float(plan.details["atr"]),
            index=index,
            opposing_internal=(
                float(raw_internal) if raw_internal is not None else None
            ),
            opposing_external=(
                float(raw_external) if raw_external is not None else None
            ),
            boundary_touched=boundary_touched,
        )

    def _arm_failed(
        self,
        *,
        source_scenario_id: str,
        source_direction: Direction,
        liquidity_level: float,
        acceptance_level: float,
        atr: float,
        index: int,
        opposing_internal: float | None,
        opposing_external: float | None,
        boundary_touched: bool,
    ) -> _FailedAbsorption:
        if liquidity_level <= 0 or acceptance_level <= 0 or atr <= 0:
            raise ValueError("failed absorption prices and ATR must be positive")
        continuation_direction = (
            Direction.LONG
            if source_direction is Direction.SHORT
            else Direction.SHORT
        )
        if (
            continuation_direction is Direction.LONG
            and acceptance_level <= liquidity_level
        ):
            raise ValueError("long acceptance boundary must exceed liquidity")
        if (
            continuation_direction is Direction.SHORT
            and acceptance_level >= liquidity_level
        ):
            raise ValueError("short acceptance boundary must be below liquidity")
        source_target = self._nearest_source_target(
            source_direction=source_direction,
            liquidity_level=liquidity_level,
            levels=(opposing_internal, opposing_external),
        )
        state = _FailedAbsorption(
            source_scenario_id=source_scenario_id,
            source_direction=source_direction,
            continuation_direction=continuation_direction,
            liquidity_level=liquidity_level,
            acceptance_level=acceptance_level,
            atr=atr,
            armed_index=index,
            timeout_bars=self._failed_timeout_bars,
            source_target=source_target,
            boundary_touched=boundary_touched,
        )
        self._failed[source_scenario_id] = state
        return state

    def observe(
        self,
        bar: FlowSignalBar,
        index: int,
        *,
        eligible: bool,
    ) -> _OverlayObservation:
        if bar.ts_event_ns <= self._last_ts:
            raise ValueError("overlay signal bars must be strictly monotonic")
        self._last_ts = bar.ts_event_ns
        transitions: list[Transition] = []
        diagnostics: dict[str, Any] = {
            "index": index,
            "history": len(self._history),
            "eligible": eligible,
            "failed_states": len(self._failed),
            "external_consumed": len(self._external_consumed),
        }

        if len(self._history) < self.config.min_history:
            self._history.append(bar)
            diagnostics["reason"] = "AUCTION_OVERLAY_WARMUP"
            return _OverlayObservation(None, tuple(), diagnostics)

        atr = self._atr()
        flow_z = self._flow_z(abs(bar.delta))
        efficiency, slope = self._trend_state()
        upper, lower, upper_ns, lower_ns = self._external_levels()
        diagnostics.update(
            {
                "atr": atr,
                "aggressor_flow_z": flow_z,
                "trend_efficiency": efficiency,
                "trend_slope": slope,
                "upper_liquidity": upper,
                "lower_liquidity": lower,
                "upper_formed_ns": upper_ns,
                "lower_formed_ns": lower_ns,
            }
        )

        if not eligible:
            if self._external_episode is not None:
                transitions.append(
                    self._external_transition(
                        self._external_episode,
                        bar=bar,
                        next_state=ScenarioState.INVALIDATED.value,
                        reason="EXTERNAL_ACCEPTANCE_SLOT_LOST",
                        details={},
                    )
                )
                self._external_episode = None
            for state in tuple(self._failed.values()):
                transitions.append(
                    self._failed_transition(
                        state,
                        bar=bar,
                        next_state=ScenarioState.INVALIDATED.value,
                        reason="FAILED_ACCEPTANCE_SLOT_LOST",
                        details={},
                    )
                )
            self._failed.clear()
            self._consume_contacted_pools(
                bar=bar,
                atr=atr,
                upper=upper,
                lower=lower,
                upper_ns=upper_ns,
                lower_ns=lower_ns,
            )
            self._history.append(bar)
            diagnostics["reason"] = "AUCTION_OVERLAY_INELIGIBLE"
            return _OverlayObservation(None, tuple(transitions), diagnostics)

        external_plan, external_transitions = self._advance_external(
            bar=bar,
            index=index,
            atr=atr,
        )
        transitions.extend(external_transitions)

        failed_plan, failed_transitions = self._advance_failed(
            bar=bar,
            index=index,
            flow_z=flow_z,
        )
        transitions.extend(failed_transitions)

        if external_plan is None and self._external_episode is None:
            episode, contact = self._detect_external_contact(
                bar=bar,
                index=index,
                atr=atr,
                flow_z=flow_z,
                efficiency=efficiency,
                slope=slope,
                upper=upper,
                lower=lower,
                upper_ns=upper_ns,
                lower_ns=lower_ns,
            )
            if episode is not None and contact is not None:
                self._external_episode = episode
                transitions.append(contact)

        plan = external_plan or failed_plan
        if external_plan is not None and failed_plan is not None:
            transitions.append(
                Transition(
                    scenario_id=failed_plan.scenario_id,
                    event_type="AUCTION_SCENARIO_TRANSITION",
                    previous_state=ScenarioState.ENTRY_READY.value,
                    next_state=ScenarioState.INVALIDATED.value,
                    reason_code="FAILED_ACCEPTANCE_DUPLICATES_EXTERNAL_ROUTE",
                    event_time_ns=bar.ts_event_ns,
                    reference_price=failed_plan.entry_reference,
                    details={
                        "selected_scenario_id": external_plan.scenario_id,
                        "direction": external_plan.direction.value,
                    },
                )
            )

        self._history.append(bar)
        diagnostics["external_active"] = (
            self._external_episode.scenario_id
            if self._external_episode is not None
            else None
        )
        diagnostics["failed_states_after"] = len(self._failed)
        return _OverlayObservation(plan, tuple(transitions), diagnostics)

    def _advance_external(
        self,
        *,
        bar: FlowSignalBar,
        index: int,
        atr: float,
    ) -> tuple[TradePlan | None, list[Transition]]:
        episode = self._external_episode
        if episode is None:
            return None, []
        self._external_episode = None
        transitions: list[Transition] = []
        if episode.direction is Direction.LONG:
            outside_hold = (
                bar.low > episode.liquidity_level
                and bar.close > episode.liquidity_level
            )
            counterflow_mitigation = (
                bar.close < bar.open
                and bar.imbalance <= -self.config.confirmation_min_imbalance
            )
        else:
            outside_hold = (
                bar.high < episode.liquidity_level
                and bar.close < episode.liquidity_level
            )
            counterflow_mitigation = (
                bar.close > bar.open
                and bar.imbalance >= self.config.confirmation_min_imbalance
            )
        if not outside_hold:
            transitions.append(
                self._external_transition(
                    episode,
                    bar=bar,
                    next_state=ScenarioState.INVALIDATED.value,
                    reason="EXTERNAL_ACCEPTANCE_RECLAIMED_NEXT_BAR",
                    details={"age_bars": index - episode.created_index},
                )
            )
            return None, transitions
        if not counterflow_mitigation:
            transitions.append(
                self._external_transition(
                    episode,
                    bar=bar,
                    next_state=ScenarioState.INVALIDATED.value,
                    reason="EXTERNAL_ACCEPTANCE_UNMITIGATED_CHASE",
                    details={
                        "age_bars": index - episode.created_index,
                        "hold_imbalance": bar.imbalance,
                    },
                )
            )
            return None, transitions

        plan = self._continuation_plan(
            scenario_id=episode.scenario_id,
            direction=episode.direction,
            observed_bar=bar,
            liquidity_level=episode.liquidity_level,
            atr=episode.atr,
            details={
                "auction_route": "EXTERNAL_ACCEPTANCE_COUNTERFLOW_MITIGATION",
                "pool_formed_ns": episode.liquidity_formed_ns,
                "contact_close": episode.contact_close,
                "lower_range": episode.lower_range,
                "upper_range": episode.upper_range,
                "hold_imbalance": bar.imbalance,
            },
        )
        transitions.append(
            self._external_transition(
                episode,
                bar=bar,
                next_state=ScenarioState.ENTRY_READY.value,
                reason="COUNTERFLOW_MITIGATION_HELD_OUTSIDE_POOL",
                details={
                    "stop": plan.stop_price,
                    "target": plan.target_price,
                    "expected_rr": plan.expected_rr,
                    "hold_imbalance": bar.imbalance,
                },
            )
        )
        self._clear_failed_duplicates(
            direction=plan.direction,
            event_time_ns=bar.ts_event_ns,
            selected_scenario_id=plan.scenario_id,
            transitions=transitions,
        )
        return plan, transitions

    def _detect_external_contact(
        self,
        *,
        bar: FlowSignalBar,
        index: int,
        atr: float,
        flow_z: float,
        efficiency: float,
        slope: float,
        upper: float,
        lower: float,
        upper_ns: int,
        lower_ns: int,
    ) -> tuple[_ExternalAcceptance | None, Transition | None]:
        penetration = self.config.sweep_min_atr * atr
        upper_contact = bar.high >= upper + penetration
        lower_contact = bar.low <= lower - penetration
        if upper_contact and lower_contact:
            self._external_consumed.add((Direction.LONG, upper_ns))
            self._external_consumed.add((Direction.SHORT, lower_ns))
            return None, None
        if not upper_contact and not lower_contact:
            return None, None

        direction = Direction.LONG if upper_contact else Direction.SHORT
        level = upper if upper_contact else lower
        formed_ns = upper_ns if upper_contact else lower_ns
        key = (direction, formed_ns)
        if key in self._external_consumed:
            return None, None
        self._external_consumed.add(key)

        accepted_close = (
            bar.close >= level + penetration
            if direction is Direction.LONG
            else bar.close <= level - penetration
        )
        matching_flow = (
            bar.imbalance >= self.config.absorption_min_imbalance
            if direction is Direction.LONG
            else bar.imbalance <= -self.config.absorption_min_imbalance
        )
        directional_body = (
            bar.close > bar.open
            if direction is Direction.LONG
            else bar.close < bar.open
        )
        displacement = bar.body >= self.config.confirmation_body_atr * atr
        directional_regime = (
            efficiency >= self.config.reversal_efficiency_max
            and (slope > 0 if direction is Direction.LONG else slope < 0)
        )
        if not (
            accepted_close
            and matching_flow
            and flow_z >= self.config.absorption_flow_z
            and directional_body
            and displacement
            and directional_regime
        ):
            return None, None

        self._external_counter += 1
        scenario_id = f"c07a-{bar.ts_event_ns}-{self._external_counter:06d}"
        episode = _ExternalAcceptance(
            scenario_id=scenario_id,
            direction=direction,
            created_index=index,
            liquidity_level=level,
            liquidity_formed_ns=formed_ns,
            atr=atr,
            lower_range=lower,
            upper_range=upper,
            contact_close=bar.close,
        )
        transition = Transition(
            scenario_id=scenario_id,
            event_type="AUCTION_SCENARIO_TRANSITION",
            previous_state=ScenarioState.IDLE.value,
            next_state="EXTERNAL_ACCEPTANCE_CONTACTED",
            reason_code=(
                "UPPER_POOL_DIRECTIONAL_FLOW_ACCEPTED"
                if direction is Direction.LONG
                else "LOWER_POOL_DIRECTIONAL_FLOW_ACCEPTED"
            ),
            event_time_ns=bar.ts_event_ns,
            reference_price=level,
            details={
                "direction": direction.value,
                "pool_formed_ns": formed_ns,
                "atr": atr,
                "aggressor_imbalance": bar.imbalance,
                "aggressor_flow_z": flow_z,
                "trend_efficiency": efficiency,
                "trend_slope": slope,
                "contact_close": bar.close,
            },
        )
        return episode, transition

    def _advance_failed(
        self,
        *,
        bar: FlowSignalBar,
        index: int,
        flow_z: float,
    ) -> tuple[TradePlan | None, list[Transition]]:
        transitions: list[Transition] = []
        plans: list[TradePlan] = []
        for key, state in tuple(self._failed.items()):
            age = index - state.armed_index
            if age > state.timeout_bars:
                transitions.append(
                    self._failed_transition(
                        state,
                        bar=bar,
                        next_state=ScenarioState.INVALIDATED.value,
                        reason="FAILED_ACCEPTANCE_TIMEOUT",
                        details={"age_bars": age},
                    )
                )
                del self._failed[key]
                continue

            if not state.boundary_touched:
                if self._source_target_reached(state, bar):
                    transitions.append(
                        self._failed_transition(
                            state,
                            bar=bar,
                            next_state=ScenarioState.INVALIDATED.value,
                            reason="SOURCE_ABSORPTION_TARGET_DELIVERED",
                            details={"source_target": state.source_target},
                        )
                    )
                    del self._failed[key]
                    continue
                touched = (
                    bar.high >= state.acceptance_level
                    if state.continuation_direction is Direction.LONG
                    else bar.low <= state.acceptance_level
                )
                if not touched:
                    continue
                transitions.append(
                    Transition(
                        scenario_id=state.scenario_id,
                        event_type="AUCTION_SCENARIO_TRANSITION",
                        previous_state="FAILED_ACCEPTANCE_ARMED",
                        next_state="FAILED_BOUNDARY_TOUCHED",
                        reason_code="ABSORPTION_STRUCTURAL_INVALIDATION_REACHED",
                        event_time_ns=bar.ts_event_ns,
                        reference_price=state.acceptance_level,
                        details={
                            "source_scenario_id": state.source_scenario_id,
                            "age_bars": age,
                        },
                    )
                )
                state.boundary_touched = True

            if state.awaiting_hold:
                held = (
                    bar.low > state.liquidity_level
                    and bar.close > state.acceptance_level
                    if state.continuation_direction is Direction.LONG
                    else bar.high < state.liquidity_level
                    and bar.close < state.acceptance_level
                )
                if held:
                    plan = self._continuation_plan(
                        scenario_id=state.scenario_id,
                        direction=state.continuation_direction,
                        observed_bar=bar,
                        liquidity_level=state.liquidity_level,
                        atr=state.atr,
                        details={
                            "auction_route": "FAILED_ABSORPTION_DIRECT_ACCEPTANCE",
                            "source_scenario_id": state.source_scenario_id,
                            "acceptance_level": state.acceptance_level,
                            "failure_age_bars": age,
                        },
                    )
                    plans.append(plan)
                    transitions.append(
                        self._failed_transition(
                            state,
                            bar=bar,
                            next_state=ScenarioState.ENTRY_READY.value,
                            reason="FAILED_ABSORPTION_ACCEPTANCE_HELD",
                            details={
                                "stop": plan.stop_price,
                                "target": plan.target_price,
                                "expected_rr": plan.expected_rr,
                            },
                        )
                    )
                else:
                    transitions.append(
                        self._failed_transition(
                            state,
                            bar=bar,
                            next_state=ScenarioState.INVALIDATED.value,
                            reason="FAILED_ACCEPTANCE_HOLD_RECLAIMED",
                            details={"age_bars": age},
                        )
                    )
                del self._failed[key]
                continue

            reclaimed = (
                bar.close <= state.liquidity_level
                if state.continuation_direction is Direction.LONG
                else bar.close >= state.liquidity_level
            )
            if reclaimed:
                transitions.append(
                    self._failed_transition(
                        state,
                        bar=bar,
                        next_state=ScenarioState.INVALIDATED.value,
                        reason="FAILED_ACCEPTANCE_RECLAIMED_ORIGINAL_POOL",
                        details={"age_bars": age},
                    )
                )
                del self._failed[key]
                continue

            accepted_close = (
                bar.close > state.acceptance_level
                if state.continuation_direction is Direction.LONG
                else bar.close < state.acceptance_level
            )
            matching_flow = (
                bar.imbalance >= self.config.absorption_min_imbalance
                if state.continuation_direction is Direction.LONG
                else bar.imbalance <= -self.config.absorption_min_imbalance
            )
            directional_body = (
                bar.close > bar.open
                if state.continuation_direction is Direction.LONG
                else bar.close < bar.open
            )
            displacement = (
                bar.body >= self.config.confirmation_body_atr * state.atr
            )
            if (
                accepted_close
                and matching_flow
                and flow_z >= self.config.absorption_flow_z
                and directional_body
                and displacement
            ):
                transitions.append(
                    Transition(
                        scenario_id=state.scenario_id,
                        event_type="AUCTION_SCENARIO_TRANSITION",
                        previous_state="FAILED_BOUNDARY_TOUCHED",
                        next_state="FAILED_ACCEPTANCE_TRIGGERED",
                        reason_code="ORIGINAL_ATTACK_FLOW_ACCEPTED_BEYOND_STOP",
                        event_time_ns=bar.ts_event_ns,
                        reference_price=state.acceptance_level,
                        details={
                            "source_scenario_id": state.source_scenario_id,
                            "age_bars": age,
                            "aggressor_imbalance": bar.imbalance,
                            "aggressor_flow_z": flow_z,
                        },
                    )
                )
                state.awaiting_hold = True
                state.trigger_index = index

        if not plans:
            return None, transitions
        plans.sort(key=lambda item: item.observed_time_ns)
        selected = plans[-1]
        for plan in plans[:-1]:
            transitions.append(
                Transition(
                    scenario_id=plan.scenario_id,
                    event_type="AUCTION_SCENARIO_TRANSITION",
                    previous_state=ScenarioState.ENTRY_READY.value,
                    next_state=ScenarioState.INVALIDATED.value,
                    reason_code="NEWER_FAILED_ACCEPTANCE_ROUTE_SELECTED",
                    event_time_ns=bar.ts_event_ns,
                    reference_price=plan.entry_reference,
                    details={"selected_scenario_id": selected.scenario_id},
                )
            )
        return selected, transitions

    def _continuation_plan(
        self,
        *,
        scenario_id: str,
        direction: Direction,
        observed_bar: FlowSignalBar,
        liquidity_level: float,
        atr: float,
        details: Mapping[str, Any],
    ) -> TradePlan:
        entry = observed_bar.close
        buffer = self.config.stop_buffer_atr * atr
        stop = (
            liquidity_level - buffer
            if direction is Direction.LONG
            else liquidity_level + buffer
        )
        risk = entry - stop if direction is Direction.LONG else stop - entry
        if risk <= 0:
            raise RuntimeError("accepted continuation produced nonpositive risk")
        target = (
            entry + risk * CONTINUATION_TARGET_RR
            if direction is Direction.LONG
            else entry - risk * CONTINUATION_TARGET_RR
        )
        return TradePlan(
            scenario_id=scenario_id,
            kind=ScenarioKind.ACCEPTANCE_CONTINUATION,
            direction=direction,
            observed_time_ns=observed_bar.ts_event_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            liquidity_level=liquidity_level,
            expected_rr=CONTINUATION_TARGET_RR,
            details={
                "atr": atr,
                "measured_target_rr": CONTINUATION_TARGET_RR,
                **dict(details),
            },
        )

    def _consume_contacted_pools(
        self,
        *,
        bar: FlowSignalBar,
        atr: float,
        upper: float,
        lower: float,
        upper_ns: int,
        lower_ns: int,
    ) -> None:
        penetration = self.config.sweep_min_atr * atr
        if bar.high >= upper + penetration:
            self._external_consumed.add((Direction.LONG, upper_ns))
        if bar.low <= lower - penetration:
            self._external_consumed.add((Direction.SHORT, lower_ns))

    def _clear_failed_duplicates(
        self,
        *,
        direction: Direction,
        event_time_ns: int,
        selected_scenario_id: str,
        transitions: list[Transition],
    ) -> None:
        for key, state in tuple(self._failed.items()):
            if state.continuation_direction is not direction:
                continue
            transitions.append(
                Transition(
                    scenario_id=state.scenario_id,
                    event_type="AUCTION_SCENARIO_TRANSITION",
                    previous_state=(
                        "FAILED_ACCEPTANCE_TRIGGERED"
                        if state.awaiting_hold
                        else "FAILED_ACCEPTANCE_ARMED"
                    ),
                    next_state=ScenarioState.INVALIDATED.value,
                    reason_code="EXTERNAL_ACCEPTANCE_ROUTE_HAS_PRIORITY",
                    event_time_ns=event_time_ns,
                    reference_price=state.acceptance_level,
                    details={"selected_scenario_id": selected_scenario_id},
                )
            )
            del self._failed[key]

    def _source_target_reached(
        self,
        state: _FailedAbsorption,
        bar: FlowSignalBar,
    ) -> bool:
        target = state.source_target
        if target is None:
            return False
        return (
            bar.low <= target
            if state.source_direction is Direction.SHORT
            else bar.high >= target
        )

    @staticmethod
    def _nearest_source_target(
        *,
        source_direction: Direction,
        liquidity_level: float,
        levels: tuple[float | None, float | None],
    ) -> float | None:
        valid = [
            float(level)
            for level in levels
            if level is not None
            and (
                float(level) < liquidity_level
                if source_direction is Direction.SHORT
                else float(level) > liquidity_level
            )
        ]
        if not valid:
            return None
        return (
            max(valid)
            if source_direction is Direction.SHORT
            else min(valid)
        )

    def _atr(self) -> float:
        bars = list(self._history)[-self.config.atr_period :]
        previous_close: float | None = None
        values: list[float] = []
        for bar in bars:
            true_range = (
                bar.range
                if previous_close is None
                else max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            )
            values.append(true_range)
            previous_close = bar.close
        return max(fmean(values), bars[-1].close * 1e-6)

    def _flow_z(self, current_abs_delta: float) -> float:
        values = [
            abs(bar.delta)
            for bar in list(self._history)[-self.config.flow_period :]
        ]
        mean = fmean(values)
        variance = fmean((value - mean) ** 2 for value in values)
        scale = sqrt(variance)
        if scale <= max(mean * 1e-9, 1e-12):
            return 0.0
        return (current_abs_delta - mean) / scale

    def _trend_state(self) -> tuple[float, float]:
        bars = list(self._history)[-self.config.atr_period :]
        closes = [bar.close for bar in bars]
        path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
        displacement = closes[-1] - closes[0]
        efficiency = abs(displacement) / path if path > 0 else 0.0
        slope = displacement / max(1, len(closes) - 1)
        return efficiency, slope

    def _external_levels(self) -> tuple[float, float, int, int]:
        window = list(self._history)[-self.config.external_lookback :]
        upper_bar = max(window, key=lambda item: item.high)
        lower_bar = min(window, key=lambda item: item.low)
        return (
            upper_bar.high,
            lower_bar.low,
            upper_bar.ts_event_ns,
            lower_bar.ts_event_ns,
        )

    @staticmethod
    def _external_transition(
        episode: _ExternalAcceptance,
        *,
        bar: FlowSignalBar,
        next_state: str,
        reason: str,
        details: Mapping[str, Any],
    ) -> Transition:
        return Transition(
            scenario_id=episode.scenario_id,
            event_type="AUCTION_SCENARIO_TRANSITION",
            previous_state="EXTERNAL_ACCEPTANCE_CONTACTED",
            next_state=next_state,
            reason_code=reason,
            event_time_ns=bar.ts_event_ns,
            reference_price=episode.liquidity_level,
            details=dict(details),
        )

    @staticmethod
    def _failed_transition(
        state: _FailedAbsorption,
        *,
        bar: FlowSignalBar,
        next_state: str,
        reason: str,
        details: Mapping[str, Any],
    ) -> Transition:
        return Transition(
            scenario_id=state.scenario_id,
            event_type="AUCTION_SCENARIO_TRANSITION",
            previous_state=(
                "FAILED_ACCEPTANCE_TRIGGERED"
                if state.awaiting_hold
                else (
                    "FAILED_BOUNDARY_TOUCHED"
                    if state.boundary_touched
                    else "FAILED_ACCEPTANCE_ARMED"
                )
            ),
            next_state=next_state,
            reason_code=reason,
            event_time_ns=bar.ts_event_ns,
            reference_price=state.acceptance_level,
            details={"source_scenario_id": state.source_scenario_id, **dict(details)},
        )


class CombinedAuctionRouter:
    """Route frozen absorption reversal and complementary auction scenarios."""

    def __init__(
        self,
        config: FlowLogicConfig,
        *,
        failed_timeout_bars: int = 24,
    ) -> None:
        self.config = config
        self.base = TargetLadderAggressorFlowRouter(config)
        self.overlay = CausalAuctionContinuationOverlay(
            config,
            failed_timeout_bars=failed_timeout_bars,
        )
        self._source_directions: dict[str, Direction] = {}
        self._last_index = -1

    @property
    def consumed_pool_count(self) -> int:
        return self.base.consumed_pool_count + self.overlay.consumed_pool_count

    @property
    def failed_state_count(self) -> int:
        return self.overlay.failed_state_count

    def arm_failed_from_plan(
        self,
        plan: TradePlan,
        *,
        event_time_ns: int,
        boundary_touched: bool,
    ) -> None:
        self.overlay.arm_failed_from_plan(
            plan,
            index=max(0, self._last_index),
            boundary_touched=boundary_touched,
        )

    def observe(
        self,
        bar: FlowSignalBar,
        index: int,
        *,
        eligible: bool = True,
    ) -> FlowObservation:
        self._last_index = index
        base_observation = self.base.observe(bar, index, eligible=eligible)
        overlay_observation = self.overlay.observe(bar, index, eligible=eligible)
        transitions = [
            *base_observation.transitions,
            *overlay_observation.transitions,
        ]
        self._capture_base_transitions(
            base_observation.transitions,
            index=index,
            transitions=transitions,
            bar=bar,
        )

        plan = base_observation.plan or overlay_observation.plan
        if (
            base_observation.plan is not None
            and overlay_observation.plan is not None
        ):
            transitions.append(
                Transition(
                    scenario_id=overlay_observation.plan.scenario_id,
                    event_type="AUCTION_SCENARIO_TRANSITION",
                    previous_state=ScenarioState.ENTRY_READY.value,
                    next_state=ScenarioState.INVALIDATED.value,
                    reason_code="ABSORPTION_REVERSAL_ROUTE_HAS_PRIORITY",
                    event_time_ns=bar.ts_event_ns,
                    reference_price=overlay_observation.plan.entry_reference,
                    details={
                        "selected_scenario_id": base_observation.plan.scenario_id
                    },
                )
            )
        diagnostics = {
            **dict(base_observation.diagnostics),
            "auction_overlay": dict(overlay_observation.diagnostics),
            "selected_route": (
                plan.kind.value if plan is not None else None
            ),
        }
        return FlowObservation(plan, tuple(transitions), diagnostics)

    def _capture_base_transitions(
        self,
        source: tuple[Transition, ...],
        *,
        index: int,
        transitions: list[Transition],
        bar: FlowSignalBar,
    ) -> None:
        for transition in source:
            reason = transition.reason_code
            if reason == "UPPER_POOL_BUY_AGGRESSION_ABSORBED":
                self._source_directions[transition.scenario_id] = Direction.SHORT
                continue
            if reason == "LOWER_POOL_SELL_AGGRESSION_ABSORBED":
                self._source_directions[transition.scenario_id] = Direction.LONG
                continue
            if reason != "UNTRADEABLE_FLOW_GEOMETRY":
                continue
            direction = self._source_directions.get(transition.scenario_id)
            details = dict(transition.details)
            if direction is None:
                transitions.append(
                    Transition(
                        scenario_id=transition.scenario_id,
                        event_type="AUCTION_SCENARIO_TRANSITION",
                        previous_state=ScenarioState.INVALIDATED.value,
                        next_state=ScenarioState.INVALIDATED.value,
                        reason_code="FAILED_ACCEPTANCE_SOURCE_DIRECTION_MISSING",
                        event_time_ns=bar.ts_event_ns,
                        reference_price=transition.reference_price,
                        details={},
                    )
                )
                continue
            required = ("stop", "atr", "liquidity_level")
            if any(details.get(name) is None for name in required):
                continue
            state = self.overlay._arm_failed(
                source_scenario_id=transition.scenario_id,
                source_direction=direction,
                liquidity_level=float(details["liquidity_level"]),
                acceptance_level=float(details["stop"]),
                atr=float(details["atr"]),
                index=index,
                opposing_internal=(
                    float(details["opposing_internal"])
                    if details.get("opposing_internal") is not None
                    else None
                ),
                opposing_external=(
                    float(details["opposing_external"])
                    if details.get("opposing_external") is not None
                    else None
                ),
                boundary_touched=False,
            )
            transitions.append(
                Transition(
                    scenario_id=state.scenario_id,
                    event_type="AUCTION_SCENARIO_TRANSITION",
                    previous_state=ScenarioState.IDLE.value,
                    next_state="FAILED_ACCEPTANCE_ARMED",
                    reason_code="UNTRADEABLE_ABSORPTION_MONITORED_FOR_ACCEPTANCE",
                    event_time_ns=bar.ts_event_ns,
                    reference_price=state.acceptance_level,
                    details={
                        "source_scenario_id": transition.scenario_id,
                        "source_direction": direction.value,
                        "continuation_direction": state.continuation_direction.value,
                        "geometry_reason": details.get("geometry_reason"),
                        "timeout_bars": state.timeout_bars,
                    },
                )
            )


__all__ = [
    "CONTINUATION_TARGET_RR",
    "CausalAuctionContinuationOverlay",
    "CombinedAuctionRouter",
]
