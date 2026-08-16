"""First efficient pullback after a causally accepted five-minute structure break.

The integrated EasyChart policy already owns reversal, event-local OB/FVG
continuation, horizontal S/R flips and mature diagonal acceptance.  Its remaining
opportunity gap is the ordinary trend pullback which a skilled chart trader sees
without insisting that every impulse print a textbook order block or FVG.

This family translates the complete auction rather than loosening an existing
pattern:

1. an accepted fifteen-minute swing break supplies the active local leg;
2. a completed five-minute candle breaks a previously confirmed five-minute
   swing in that leg and its constituent one-minute taker flow produces real net
   progress;
3. the required next five-minute candle opens and closes beyond the broken swing;
4. the first later one-minute return to the broken level is consumed once;
5. the first following completed minute must close beyond the return extreme and
   show either aligned initiative or adverse-flow absorption;
6. the executable stop sits beyond the completed return/response excursion and
   the immutable objective is the nearest pre-existing unspent significant
   one-minute, five-minute or fifteen-minute opposing swing.

A live BTC/ETH-led common impulse may veto an opposing setup, but is never an
extra aligned-entry requirement.  The family is independent from OB/FVG and
horizontal/diagonal owners, so frequency grows through another causal mechanism
rather than weaker thresholds.  No session rule, ATR rule, fitted score, clock
expiry, partial exit, stop movement or additional account slot is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import Pivot, StructureZone, V5TradePlan, provenance
from domain import Candle, Side
from easychart_re1_auction_router_v2 import EasyChartRE1AuctionRouterV2Bundle
from easychart_re1_auction_router_v3 import EasyChartRE1AuctionRouterV3Bundle
from easychart_re1_auction_router_v5 import EasyChartRE1AuctionRouterV5Bundle
from easychart_re1_efficient_objective import PivotOnlyObjectiveBook
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_re1_local_auction_continuation import (
    COMMON_FACTOR_VETO_ONLY_RULE,
    EasyChartRE1LocalAuctionStrategy,
)
from execution_re1_market_factor import CommonFactorState
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


EFFICIENT_PULLBACK_LOCAL_LEG_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIFTEEN_MINUTE_DIRECTION_CHANGES_ONLY_AFTER_CLOSE_BREAK_AND_NEXT_COMPLETED_BAR_OPEN_CLOSE_ACCEPTANCE_OF_A_CONFIRMED_SWING"
)
EFFICIENT_PULLBACK_IMPULSE_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "ALIGNED_ACCEPTED_FIVE_MINUTE_SWING_BREAK_WITH_CONSTITUENT_TAKER_FLOW_AND_NET_PROGRESS_CREATES_A_FIRST_PULLBACK_CONTINUATION"
)
EFFICIENT_PULLBACK_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIRST_RETURN_TO_THE_BROKEN_FIVE_MINUTE_SWING_ENTERS_ONLY_AFTER_THE_FIRST_LATER_MICRO_CLOSE_EXTENDS_WITH_ALIGNED_INITIATIVE_OR_ADVERSE_FLOW_ABSORPTION"
)
EFFICIENT_PULLBACK_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "EFFICIENT_PULLBACK_TARGETS_THE_NEAREST_PREEXISTING_UNSPENT_SPAN6_ONE_MINUTE_OR_FIVE_FIFTEEN_MINUTE_OPPOSING_SWING"
)
for _rule in (
    EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
    EFFICIENT_PULLBACK_IMPULSE_RULE,
    EFFICIENT_PULLBACK_RESPONSE_RULE,
    EFFICIENT_PULLBACK_OBJECTIVE_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class PendingAcceptedDirection:
    side: Side
    pivot: Pivot
    break_time_ns: int
    break_close: float


@dataclass(slots=True)
class EfficientPullbackSetup:
    setup_id: str
    side: Side
    local_pivot: Pivot
    break_pivot: Pivot
    break_time_ns: int
    break_high: float
    break_low: float
    break_close: float
    state: str = "WAITING_HOLD"
    hold_time_ns: int | None = None
    retest_time_ns: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    retest_close: float | None = None
    terminal_reason: str | None = None

    @property
    def active(self) -> bool:
        return self.terminal_reason is None


class EfficientPullbackEngine:
    """Small causal state machine for accepted micro-structure pullbacks."""

    LOCAL_SPAN = 2
    BREAK_SPAN = 2
    NS_PER_MINUTE = 60_000_000_000

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.minimum_gross_rr = minimum_gross_rr
        self.higher_minutes = 15
        self.decision_minutes = 5
        self.trigger_minutes = 1

        self.local_structure = NearestAnyPivotStructureBook(
            symbol,
            self.higher_minutes,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.decision_structure = NearestAnyPivotStructureBook(
            symbol,
            self.decision_minutes,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.micro_objectives = PivotOnlyObjectiveBook(
            symbol,
            self.trigger_minutes,
            tick_size,
            pivot_spans=(6,),
        )
        self.flow_analyzer = CausalFlowAnalyzer(tick_size)

        self.local_side: Side | None = None
        self.local_pivot: Pivot | None = None
        self.pending_local_direction: PendingAcceptedDirection | None = None
        self.common_factor: CommonFactorState | None = None

        self._used_local_pivots: set[str] = set()
        self._used_break_pivots: set[str] = set()
        self._pending_break_flow: dict[str, EfficientPullbackSetup] = {}
        self._active: dict[str, EfficientPullbackSetup] = {}
        self.setups: list[EfficientPullbackSetup] = []
        self.plans: list[V5TradePlan] = []
        self.audit_zones: list[Any] = []
        self._zones: dict[str, Any] = {}
        self.trace_events: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}
        self.sequence = 0

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _trace(self, kind: str, time_ns: int, **values: Any) -> None:
        self.trace_events.append(
            {
                "scenario_kind": kind,
                "event_time_ns": time_ns,
                "symbol": self.symbol,
                **values,
            }
        )

    def _audit(self, zone: Any) -> None:
        zone_id = getattr(zone, "zone_id", None)
        if zone_id and zone_id not in self._zones:
            self._zones[zone_id] = zone
            self.audit_zones.append(zone)

    def set_market_factor_state(self, state: CommonFactorState | None) -> None:
        self.common_factor = state

    @staticmethod
    def _breaks(side: Side, pivot: Pivot, previous: Candle, bar: Candle) -> bool:
        if side is Side.LONG:
            return previous.close <= pivot.price and bar.close > pivot.price and bar.close > bar.open
        return previous.close >= pivot.price and bar.close < pivot.price and bar.close < bar.open

    @staticmethod
    def _holds(side: Side, pivot: Pivot, bar: Candle) -> bool:
        if side is Side.LONG:
            return bar.open > pivot.price and bar.close > pivot.price
        return bar.open < pivot.price and bar.close < pivot.price

    @staticmethod
    def _aligned(side: Side, value: float) -> bool:
        return value > 0.0 if side is Side.LONG else value < 0.0

    @staticmethod
    def _progress(side: Side, start: float, end: float) -> float:
        return end - start if side is Side.LONG else start - end

    @staticmethod
    def _response_price(side: Side, setup: EfficientPullbackSetup, bar: Candle) -> bool:
        if setup.retest_high is None or setup.retest_low is None:
            return False
        return bar.close > setup.retest_high if side is Side.LONG else bar.close < setup.retest_low

    @staticmethod
    def _flow_mechanism(
        side: Side,
        observation: FlowObservation | None,
    ) -> str | None:
        if observation is None or not observation.active or not observation.directed:
            return None
        intended_body = observation.body > 0.0 if side is Side.LONG else observation.body < 0.0
        aligned_delta = (
            observation.signed_taker_quote > 0.0
            if side is Side.LONG
            else observation.signed_taker_quote < 0.0
        )
        adverse_delta = (
            observation.signed_taker_quote < 0.0
            if side is Side.LONG
            else observation.signed_taker_quote > 0.0
        )
        if aligned_delta and intended_body and observation.material_progress:
            return "FIRST_RESPONSE_ALIGNED_INITIATIVE"
        if adverse_delta and intended_body:
            return "FIRST_RESPONSE_ADVERSE_FLOW_ABSORBED"
        return None

    def _eligible_local_breaks(self, side: Side, previous: Candle, bar: Candle) -> list[Pivot]:
        wanted = "HIGH" if side is Side.LONG else "LOW"
        return [
            pivot
            for pivot in self.local_structure.pivots
            if pivot.span == self.LOCAL_SPAN
            and pivot.side == wanted
            and pivot.pivot_id not in self._used_local_pivots
            and pivot.observed_time_ns < bar.ts_close_ns
            and self._breaks(side, pivot, previous, bar)
        ]

    def _confirm_or_fail_local_direction(self, bar: Candle) -> None:
        pending = self.pending_local_direction
        if pending is None:
            return
        self.pending_local_direction = None
        if not self._holds(pending.side, pending.pivot, bar):
            self._used_local_pivots.discard(pending.pivot.pivot_id)
            self._inc("local_direction_failed_next_bar_hold")
            self._trace(
                "efficient_pullback_local_direction_failed",
                bar.ts_close_ns,
                candidate_side=pending.side.name,
                pivot_id=pending.pivot.pivot_id,
                pivot_price=pending.pivot.price,
                break_time_ns=pending.break_time_ns,
                hold_open=bar.open,
                hold_close=bar.close,
                retained_side=None if self.local_side is None else self.local_side.name,
                rule_provenance=EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
            )
            return
        self.local_side = pending.side
        self.local_pivot = pending.pivot
        self._inc("local_direction_accepted")
        self._trace(
            "efficient_pullback_local_direction_accepted",
            bar.ts_close_ns,
            side=pending.side.name,
            pivot_id=pending.pivot.pivot_id,
            pivot_price=pending.pivot.price,
            break_time_ns=pending.break_time_ns,
            break_close=pending.break_close,
            hold_open=bar.open,
            hold_close=bar.close,
            rule_provenance=EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
        )

    def _arm_local_direction(self, previous: Candle, bar: Candle) -> None:
        candidates: list[tuple[Side, Pivot]] = []
        for side in (Side.LONG, Side.SHORT):
            candidates.extend((side, p) for p in self._eligible_local_breaks(side, previous, bar))
        if not candidates:
            return
        side, pivot = max(
            candidates,
            key=lambda item: (
                item[1].event_time_ns,
                item[1].observed_time_ns,
                item[1].pivot_id,
            ),
        )
        self._used_local_pivots.add(pivot.pivot_id)
        if self.local_side is side:
            self.local_pivot = pivot
            self._inc("same_side_local_direction_refreshed")
            return
        self.pending_local_direction = PendingAcceptedDirection(
            side=side,
            pivot=pivot,
            break_time_ns=bar.ts_close_ns,
            break_close=bar.close,
        )
        self._inc("local_direction_waiting_next_bar_hold")

    def _on_fifteen(self, bar: Candle) -> None:
        previous = self.local_structure.bars[-1] if self.local_structure.bars else None
        self.local_structure.on_bar(bar)
        self._confirm_or_fail_local_direction(bar)
        if previous is not None:
            self._arm_local_direction(previous, bar)
        self.local_structure.observe_price(bar)

    def _eligible_decision_breaks(self, previous: Candle, bar: Candle) -> list[Pivot]:
        side = self.local_side
        if side is None:
            return []
        wanted = "HIGH" if side is Side.LONG else "LOW"
        return [
            pivot
            for pivot in self.decision_structure.pivots
            if pivot.span == self.BREAK_SPAN
            and pivot.side == wanted
            and pivot.pivot_id not in self._used_break_pivots
            and pivot.observed_time_ns < bar.ts_close_ns
            and self._breaks(side, pivot, previous, bar)
        ]

    def _advance_holds(self, bar: Candle) -> None:
        expected_delta = self.decision_minutes * self.NS_PER_MINUTE
        for setup in list(self._active.values()):
            if setup.state != "WAITING_HOLD":
                continue
            if bar.ts_close_ns <= setup.break_time_ns:
                continue
            if bar.ts_close_ns != setup.break_time_ns + expected_delta:
                self._finish(setup, "missing_immediate_next_decision_hold", bar.ts_close_ns)
                continue
            if not self._holds(setup.side, setup.break_pivot, bar):
                self._used_break_pivots.discard(setup.break_pivot.pivot_id)
                self._finish(setup, "decision_break_failed_next_bar_hold", bar.ts_close_ns)
                continue
            setup.state = "WAITING_RETEST"
            setup.hold_time_ns = bar.ts_close_ns
            self._inc("efficient_pullback_hold_confirmed")
            self._trace(
                "efficient_pullback_hold_confirmed",
                bar.ts_close_ns,
                setup_id=setup.setup_id,
                side=setup.side.name,
                pivot_id=setup.break_pivot.pivot_id,
                pivot_price=setup.break_pivot.price,
                hold_open=bar.open,
                hold_close=bar.close,
                rule_provenance=EFFICIENT_PULLBACK_IMPULSE_RULE,
            )

    def _arm_decision_break(self, previous: Candle, bar: Candle) -> None:
        candidates = self._eligible_decision_breaks(previous, bar)
        if not candidates or self.local_side is None or self.local_pivot is None:
            return
        pivot = max(
            candidates,
            key=lambda item: (
                item.event_time_ns,
                item.observed_time_ns,
                item.pivot_id,
            ),
        )
        self._used_break_pivots.add(pivot.pivot_id)
        setup_id = f"EFFICIENT_PULLBACK:{self.symbol}:{pivot.pivot_id}:{bar.ts_close_ns}"
        setup = EfficientPullbackSetup(
            setup_id=setup_id,
            side=self.local_side,
            local_pivot=self.local_pivot,
            break_pivot=pivot,
            break_time_ns=bar.ts_close_ns,
            break_high=bar.high,
            break_low=bar.low,
            break_close=bar.close,
        )
        self.setups.append(setup)
        self._active[setup_id] = setup
        self._pending_break_flow[setup_id] = setup
        self._inc("decision_break_waiting_complete_constituent_flow")
        self._trace(
            "efficient_pullback_decision_break_waiting_flow",
            bar.ts_close_ns,
            setup_id=setup_id,
            side=setup.side.name,
            local_pivot_id=setup.local_pivot.pivot_id,
            break_pivot_id=pivot.pivot_id,
            break_pivot_price=pivot.price,
            break_close=bar.close,
            rule_provenance=EFFICIENT_PULLBACK_IMPULSE_RULE,
        )

    def _on_five(self, bar: Candle) -> None:
        previous = self.decision_structure.bars[-1] if self.decision_structure.bars else None
        self.decision_structure.on_bar(bar)
        self._advance_holds(bar)
        if previous is not None:
            self._arm_decision_break(previous, bar)
        self.decision_structure.observe_price(bar)

    def _break_flow(self, setup: EfficientPullbackSetup, close_ns: int) -> tuple[list[FlowObservation], float, float] | None:
        start_ns = close_ns - self.decision_minutes * self.NS_PER_MINUTE
        observations = [
            item
            for item in self.flow_analyzer.history
            if start_ns < item.ts_close_ns <= close_ns
        ]
        if not observations:
            return None
        cumulative = sum(item.signed_taker_quote for item in observations)
        progress = self._progress(setup.side, observations[0].open, observations[-1].close)
        aligned = [
            item
            for item in observations
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned(setup.side, item.signed_taker_quote)
            and (item.body > 0.0 if setup.side is Side.LONG else item.body < 0.0)
        ]
        if not self._aligned(setup.side, cumulative) or progress <= 0.0 or not aligned:
            return None
        return observations, cumulative, progress

    def _finalize_pending_break_flow(self, bar: Candle) -> None:
        for setup_id, setup in list(self._pending_break_flow.items()):
            if setup.break_time_ns > bar.ts_close_ns:
                continue
            self._pending_break_flow.pop(setup_id, None)
            if setup.break_time_ns != bar.ts_close_ns:
                self._finish(setup, "decision_break_missed_same_close_flow", bar.ts_close_ns)
                continue
            evidence = self._break_flow(setup, bar.ts_close_ns)
            if evidence is None:
                self._used_break_pivots.discard(setup.break_pivot.pivot_id)
                self._finish(setup, "decision_break_rejected_without_aligned_flow", bar.ts_close_ns)
                continue
            observations, cumulative, progress = evidence
            self._inc("decision_break_flow_validated")
            self._trace(
                "efficient_pullback_decision_break_flow_validated",
                bar.ts_close_ns,
                setup_id=setup.setup_id,
                side=setup.side.name,
                break_pivot_id=setup.break_pivot.pivot_id,
                formation_bars=len(observations),
                cumulative_signed_taker_quote=cumulative,
                net_price_progress=progress,
                rule_provenance=EFFICIENT_PULLBACK_IMPULSE_RULE,
            )

    def _finish(self, setup: EfficientPullbackSetup, reason: str, time_ns: int, **values: Any) -> None:
        setup.terminal_reason = reason
        self._active.pop(setup.setup_id, None)
        self._pending_break_flow.pop(setup.setup_id, None)
        self._inc(reason)
        self._trace(
            reason,
            time_ns,
            setup_id=setup.setup_id,
            side=setup.side.name,
            break_pivot_id=setup.break_pivot.pivot_id,
            **values,
        )

    def _target(self, setup: EfficientPullbackSetup, bar: Candle) -> tuple[StructureZone, float] | None:
        choices: list[tuple[str, StructureZone, float]] = []
        for name, book in (
            ("1M_SPAN6", self.micro_objectives),
            ("5M", self.decision_structure),
            ("15M", self.local_structure),
        ):
            value = book.target_for(
                setup.side,
                interaction_time_ns=bar.ts_close_ns,
                source_span=6,
                current_high=bar.high,
                current_low=bar.low,
            )
            if value is not None:
                choices.append((name, value[0], value[1]))
        if not choices:
            return None
        selected = (
            min(choices, key=lambda item: (item[2], item[0]))
            if setup.side is Side.LONG
            else max(choices, key=lambda item: (item[2], item[0]))
        )
        self._audit(selected[1])
        self._inc(f"objective_{selected[0].lower()}")
        return selected[1], selected[2]

    def _factor_opposes(self, setup: EfficientPullbackSetup) -> bool:
        return self.common_factor is not None and self.common_factor.side is not setup.side

    def _plan(self, setup: EfficientPullbackSetup, bar: Candle, mechanism: str, observation: FlowObservation) -> V5TradePlan | None:
        target = self._target(setup, bar)
        if target is None:
            self._finish(setup, "efficient_pullback_no_preexisting_target", bar.ts_close_ns)
            return None
        target_zone, target_price = target
        if setup.retest_low is None or setup.retest_high is None:
            raise RuntimeError("efficient pullback response lost retest geometry")
        if setup.side is Side.LONG:
            stop = min(setup.retest_low, bar.low, setup.break_pivot.price - self.tick_size) - self.tick_size
            risk = bar.close - stop
            reward = target_price - bar.close
        else:
            stop = max(setup.retest_high, bar.high, setup.break_pivot.price + self.tick_size) + self.tick_size
            risk = stop - bar.close
            reward = bar.close - target_price
        if risk <= 0.0 or reward <= 0.0:
            self._finish(setup, "efficient_pullback_nonpositive_geometry", bar.ts_close_ns)
            return None
        gross_rr = reward / risk
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                "efficient_pullback_below_minimum_gross_rr",
                bar.ts_close_ns,
                gross_rr=gross_rr,
            )
            return None

        higher = self.local_structure._horizontal_snapshot(setup.local_pivot, bar.ts_close_ns)
        decision = self.decision_structure._horizontal_snapshot(setup.break_pivot, bar.ts_close_ns)
        self._audit(higher)
        self._audit(decision)
        self.sequence += 1
        plan = V5TradePlan(
            plan_id=f"ec-re1-efficient-pullback-{self.symbol}-{self.sequence:08d}",
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="MICRO_5M_ACCEPTED_BREAK_FIRST_EFFICIENT_PULLBACK",
            side=setup.side,
            observed_time_ns=bar.ts_close_ns,
            entry=bar.close,
            stop=stop,
            target=target_price,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=higher.zone_id,
            higher_zone_kind=higher.kind,
            higher_strength_ratio=higher.strength_ratio,
            lower_zone_id=decision.zone_id,
            lower_zone_kind=decision.kind,
            lower_strength_ratio=decision.strength_ratio,
            trigger_zone_id=decision.zone_id,
            trigger_strength_ratio=max(1.0, observation.activity_ratio * observation.delta_ratio),
            target_zone_id=target_zone.zone_id,
            target_zone_kind=target_zone.kind,
            overlap_lower=decision.lower,
            overlap_upper=decision.upper,
            interaction_time_ns=setup.break_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path="ACCEPTANCE",
            setup_observed_time_ns=setup.break_pivot.observed_time_ns,
            trigger_zone_kind=f"EFFICIENT_PULLBACK_{mechanism}",
            source_rule_count=len(provenance()),
            rule_provenance=provenance() + (
                EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
                EFFICIENT_PULLBACK_IMPULSE_RULE,
                EFFICIENT_PULLBACK_RESPONSE_RULE,
                EFFICIENT_PULLBACK_OBJECTIVE_RULE,
                COMMON_FACTOR_VETO_ONLY_RULE,
            ),
            scale_name="EFFICIENT_PULLBACK",
            higher_timeframe_minutes=15,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        self.plans.append(plan)
        self._finish(
            setup,
            "efficient_pullback_plan_created",
            bar.ts_close_ns,
            plan_id=plan.plan_id,
            mechanism=mechanism,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            gross_rr=plan.gross_rr,
            signed_taker_quote=observation.signed_taker_quote,
            activity_ratio=observation.activity_ratio,
            delta_ratio=observation.delta_ratio,
        )
        return plan

    def _advance_micro_setups(self, bar: Candle, observation: FlowObservation | None) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        band_low_shift = self.tick_size
        for setup in list(self._active.values()):
            if setup.state not in {"WAITING_RETEST", "WAITING_RESPONSE"}:
                continue
            if setup.hold_time_ns is None or bar.ts_close_ns <= setup.hold_time_ns:
                continue
            if self._factor_opposes(setup):
                pivot = setup.break_pivot.price
                touched = bar.low <= pivot + self.tick_size and bar.high >= pivot - self.tick_size
                if touched:
                    self._finish(
                        setup,
                        "efficient_pullback_first_touch_vetoed_by_common_factor",
                        bar.ts_close_ns,
                        factor_side=self.common_factor.side.name if self.common_factor else None,
                        factor_event_time_ns=self.common_factor.event_time_ns if self.common_factor else None,
                    )
                continue

            if setup.state == "WAITING_RETEST":
                pivot = setup.break_pivot.price
                touched = bar.low <= pivot + band_low_shift and bar.high >= pivot - band_low_shift
                if not touched:
                    continue
                held = bar.close > pivot if setup.side is Side.LONG else bar.close < pivot
                if not held:
                    self._finish(setup, "efficient_pullback_first_return_failed", bar.ts_close_ns)
                    continue
                setup.retest_time_ns = bar.ts_close_ns
                setup.retest_high = bar.high
                setup.retest_low = bar.low
                setup.retest_close = bar.close
                setup.state = "WAITING_RESPONSE"
                self._inc("efficient_pullback_waiting_first_response")
                continue

            if setup.retest_time_ns is None or bar.ts_close_ns <= setup.retest_time_ns:
                continue
            if not self._response_price(setup.side, setup, bar):
                self._finish(setup, "efficient_pullback_first_response_failed_price", bar.ts_close_ns)
                continue
            mechanism = self._flow_mechanism(setup.side, observation)
            if mechanism is None:
                self._finish(setup, "efficient_pullback_first_response_failed_flow", bar.ts_close_ns)
                continue
            plan = self._plan(setup, bar, mechanism, observation)
            if plan is not None:
                output.append(plan)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.higher_minutes:
            self._on_fifteen(bar)
            return []
        if timeframe_minutes == self.decision_minutes:
            self._on_five(bar)
            return []
        if timeframe_minutes != self.trigger_minutes:
            return []
        self.micro_objectives.on_bar(bar)
        observation = self.flow_analyzer.observe(bar)
        self._finalize_pending_break_flow(bar)
        output = self._advance_micro_setups(bar, observation)
        self.micro_objectives.observe_price(bar)
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output, self.trace_events = self.trace_events, []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self._zones.get(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._counts.items())),
            "active": len(self._active),
            "pending_break_flow": len(self._pending_break_flow),
            "local_side": None if self.local_side is None else self.local_side.name,
            "local_pivot_id": None if self.local_pivot is None else self.local_pivot.pivot_id,
            "common_factor": None
            if self.common_factor is None
            else {
                "side": self.common_factor.side.name,
                "event_time_ns": self.common_factor.event_time_ns,
                "sequence": self.common_factor.sequence,
            },
            "flow": self.flow_analyzer.diagnostics,
            "micro_objectives": dict(self.micro_objectives.diagnostics),
            "rules": (
                EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
                EFFICIENT_PULLBACK_IMPULSE_RULE,
                EFFICIENT_PULLBACK_RESPONSE_RULE,
                EFFICIENT_PULLBACK_OBJECTIVE_RULE,
            ),
        }


class EasyChartRE1EfficientPullbackBundle(EasyChartRE1AuctionRouterV5Bundle):
    """Specifically-owned integrated policy plus residual 5m trend pullback."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.efficient_pullback = EfficientPullbackEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self._pullback_counts: dict[str, int] = {}
        self._pullback_trace: list[dict[str, Any]] = []
        self._factor_state: CommonFactorState | None = None

    def _pinc(self, key: str) -> None:
        self._pullback_counts[key] = self._pullback_counts.get(key, 0) + 1

    def set_market_factor_state(self, state: CommonFactorState | None) -> None:
        self._factor_state = state
        super().set_market_factor_state(state)
        self.efficient_pullback.set_market_factor_state(state)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.efficient_pullback.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.efficient_pullback.plans

    def _route_pullback(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._pinc("efficient_pullback_duplicate_episode")
                continue
            factor = self._factor_state
            if factor is not None and factor.side is not plan.side:
                self._pinc("efficient_pullback_rejected_by_opposing_common_factor")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._pinc("efficient_pullback_plan_allowed")
            self._pullback_trace.append(
                {
                    "scenario_kind": "efficient_pullback_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "macro_side": None if self._macro_side is None else self._macro_side.name,
                    "factor_side": None if factor is None else factor.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": (
                        EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
                        EFFICIENT_PULLBACK_IMPULSE_RULE,
                        EFFICIENT_PULLBACK_RESPONSE_RULE,
                        EFFICIENT_PULLBACK_OBJECTIVE_RULE,
                        COMMON_FACTOR_VETO_ONLY_RULE,
                    ),
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        # Run all specific existing owners without v3/v4's diagonal-first path.
        core = EasyChartRE1AuctionRouterV2Bundle.on_bar(self, timeframe_minutes, bar)
        pullback = self._route_pullback(
            self.efficient_pullback.on_bar(timeframe_minutes, bar)
        )
        # Generic mature diagonal remains the residual owner after specific core
        # and efficient-pullback episodes have claimed their evidence.
        diagonal = self._route_diagonal(
            self.mature_diagonal_acceptance.on_bar(timeframe_minutes, bar)
        )
        return sorted(
            core + pullback + diagonal,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            super().drain_trace()
            + self.efficient_pullback.drain_trace()
            + self._pullback_trace
        )
        self._pullback_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.efficient_pullback.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["efficient_pullback"] = {
            "bundle_counts": dict(sorted(self._pullback_counts.items())),
            "engine": self.efficient_pullback.diagnostics,
            "rules": (
                EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
                EFFICIENT_PULLBACK_IMPULSE_RULE,
                EFFICIENT_PULLBACK_RESPONSE_RULE,
                EFFICIENT_PULLBACK_OBJECTIVE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1EfficientPullbackBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
