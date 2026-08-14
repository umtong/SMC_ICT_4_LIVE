"""Five-minute local liquidity sweep with one-minute absorption entry.

The diagonal micro core is strong but does not cover the most common intraday
liquidity event: a visible local five-minute swing is swept and the aggressive
orders fail to move price through it. This module adds that mechanism without a
new visual-pattern checklist.

Causal policy
-------------
* five-minute wick pivots are confirmed only after their right-side bars close;
* the first later one-minute interaction retires every touched pivot level;
* if both a high and a low are swept inside the same minute, the event is
  ambiguous and no setup is created;
* after the first interaction, accumulate completed one-minute Binance flow
  until one typical prior-minute quote volume has traded;
* long reversal requires cumulative sell aggression, non-adverse net price
  progress and a close back above the swept low; short is symmetric;
* the first completed volume bucket is evaluated once; no favorable later
  outcome is searched for;
* stop is beyond the complete observed sweep episode; target is the nearest
  pre-existing unspent opposite five- or fifteen-minute pivot;
* the completed plan must align with the current confirmed fifteen-minute
  structure side (neutral remains allowed).

This is an independent opportunity family. It does not loosen the proven
trend-line/channel core and does not use OB/FVG as mandatory gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import (
    ObjectKind,
    ScenarioPath,
    SetupState,
    StructureZone,
    V5TradePlan,
    provenance,
)
from domain import Candle, Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation, FlowTriggerKind
from easychart_re1_flow_micro_core import EasyChartRE1VolumeClockMicroCoreBundle
from easychart_zones import ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


LOCAL_LIQUIDITY_FLOW_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "CONFIRMED_FIVE_MINUTE_SWING_FIRST_INTERACTION_PLUS_FIRST_TYPICAL_VOLUME_BUCKET_ABSORPTION_DEFINES_LOCAL_LIQUIDITY_REVERSAL"
)
LOCAL_LIQUIDITY_TARGET_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "LOCAL_SWEEP_TARGET_IS_NEAREST_PREEXISTING_UNSPENT_OPPOSITE_FIVE_OR_FIFTEEN_MINUTE_PIVOT"
)
LOCAL_LIQUIDITY_ALIGNMENT_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIVE_MINUTE_LOCAL_LIQUIDITY_REVERSAL_MUST_ALIGN_WITH_CURRENT_CONFIRMED_FIFTEEN_MINUTE_STRUCTURE_OR_NEUTRAL_STATE"
)
if LOCAL_LIQUIDITY_FLOW_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (LOCAL_LIQUIDITY_FLOW_RULE,)
for _rule in (LOCAL_LIQUIDITY_TARGET_RULE, LOCAL_LIQUIDITY_ALIGNMENT_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(slots=True)
class LocalLiquiditySetup:
    setup_id: str
    pivot_id: str
    side: Side
    pivot_price: float
    pivot_span: int
    pivot_strength: float
    pivot_observed_time_ns: int
    interaction_time_ns: int
    interaction_extreme: float
    episode_open: float
    typical_quote_volume: float
    median_abs_delta: float
    target_zone: StructureZone
    target: float
    context_zone: StructureZone
    cumulative_quote_volume: float = 0.0
    cumulative_signed_taker_quote: float = 0.0
    episode_bars: int = 0
    state: SetupState = SetupState.WAITING_DISPLACEMENT
    terminal_reason: str | None = None


class LocalLiquidityFlowEngine:
    """Closed-bar local pivot lifecycle and first-volume-bucket absorption."""

    HIGHER_MINUTES = 15
    LOCAL_MINUTES = 5
    FLOW_MINUTES = 1

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.minimum_gross_rr = minimum_gross_rr
        self.local_structure = NearestAnyPivotStructureBook(
            symbol,
            self.LOCAL_MINUTES,
            tick_size,
        )
        self.higher_structure = NearestAnyPivotStructureBook(
            symbol,
            self.HIGHER_MINUTES,
            tick_size,
        )
        self.flow = CausalFlowAnalyzer(tick_size)
        self.setups: list[LocalLiquiditySetup] = []
        self._active: dict[str, LocalLiquiditySetup] = {}
        self.plans: list[V5TradePlan] = []
        self.audit_zones: list[StructureZone] = []
        self._audit_zone_ids: set[str] = set()
        self.trace_events: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}
        self._retired_pivots: set[str] = set()
        self.sequence = 0

    def _inc(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def _trace(self, kind: str, time_ns: int, **values: Any) -> None:
        self.trace_events.append(
            {
                "scenario_kind": kind,
                "event_time_ns": time_ns,
                "scale_name": "LOCAL_LIQUIDITY",
                "higher_timeframe_minutes": self.LOCAL_MINUTES,
                "decision_timeframe_minutes": self.FLOW_MINUTES,
                "trigger_timeframe_minutes": self.FLOW_MINUTES,
                **values,
            },
        )

    def _audit(self, zone: StructureZone) -> None:
        if zone.zone_id in self._audit_zone_ids:
            return
        self._audit_zone_ids.add(zone.zone_id)
        self.audit_zones.append(zone)

    @staticmethod
    def _intended_progress(side: Side, start: float, end: float) -> float:
        return end - start if side is Side.LONG else start - end

    @staticmethod
    def _opposite_delta(side: Side, value: float) -> bool:
        return value < 0.0 if side is Side.LONG else value > 0.0

    def _finish(
        self,
        setup: LocalLiquiditySetup,
        state: SetupState,
        time_ns: int,
        reason: str,
        **values: Any,
    ) -> None:
        setup.state = state
        setup.terminal_reason = reason
        self._active.pop(setup.setup_id, None)
        self._inc(reason)
        self._trace(
            reason,
            time_ns,
            setup_id=setup.setup_id,
            pivot_id=setup.pivot_id,
            side=setup.side.name,
            interaction_time_ns=setup.interaction_time_ns,
            **values,
        )

    def _retire(self, pivot: Any, time_ns: int) -> None:
        self._retired_pivots.add(pivot.pivot_id)
        pivot.first_touch_time_ns = pivot.first_touch_time_ns or time_ns
        pivot.consumed = True
        pivot.consumed_time_ns = pivot.consumed_time_ns or time_ns

    def _eligible_touched_pivots(self, bar: Candle) -> tuple[list[Any], list[Any]]:
        lows: list[Any] = []
        highs: list[Any] = []
        for pivot in self.local_structure.pivots:
            if (
                pivot.pivot_id in self._retired_pivots
                or pivot.observed_time_ns >= bar.ts_close_ns
            ):
                continue
            if pivot.side == "LOW" and bar.low <= pivot.price:
                lows.append(pivot)
            elif pivot.side == "HIGH" and bar.high >= pivot.price:
                highs.append(pivot)
        return lows, highs

    @staticmethod
    def _deepest(pivots: list[Any], side: Side) -> Any:
        if side is Side.LONG:
            return min(
                pivots,
                key=lambda item: (
                    item.price,
                    -item.span,
                    -item.event_time_ns,
                    item.pivot_id,
                ),
            )
        return max(
            pivots,
            key=lambda item: (
                item.price,
                item.span,
                item.event_time_ns,
                item.pivot_id,
            ),
        )

    def _target_for(
        self,
        side: Side,
        pivot: Any,
        bar: Candle,
    ) -> tuple[StructureZone, float] | None:
        choices: list[tuple[StructureZone, float]] = []
        for book in (self.local_structure, self.higher_structure):
            value = book.target_for(
                side,
                interaction_time_ns=bar.ts_close_ns,
                source_span=pivot.span,
                current_high=bar.high,
                current_low=bar.low,
            )
            if value is None:
                continue
            zone, price = value
            if any(
                existing.source_structure_id == zone.source_structure_id
                or abs(existing_price - price) <= self.tick_size * 0.5
                for existing, existing_price in choices
            ):
                continue
            choices.append((zone, price))
        if not choices:
            return None
        return (
            min(choices, key=lambda item: item[1])
            if side is Side.LONG
            else max(choices, key=lambda item: item[1])
        )

    def _start_setup(
        self,
        pivot: Any,
        side: Side,
        bar: Candle,
        observation: FlowObservation,
    ) -> LocalLiquiditySetup | None:
        target = self._target_for(side, pivot, bar)
        if target is None:
            self._inc("local_liquidity_no_preexisting_target")
            return None
        target_zone, target_price = target
        context = self.local_structure._horizontal_snapshot(  # noqa: SLF001
            pivot,
            bar.ts_close_ns,
        )
        self.sequence += 1
        setup = LocalLiquiditySetup(
            setup_id=f"LOCAL_LIQ:{self.symbol}:{self.sequence:08d}",
            pivot_id=pivot.pivot_id,
            side=side,
            pivot_price=pivot.price,
            pivot_span=pivot.span,
            pivot_strength=pivot.strength_ratio,
            pivot_observed_time_ns=pivot.observed_time_ns,
            interaction_time_ns=bar.ts_close_ns,
            interaction_extreme=bar.low if side is Side.LONG else bar.high,
            episode_open=bar.open,
            typical_quote_volume=max(observation.median_quote_volume, 1e-12),
            median_abs_delta=max(observation.median_abs_delta, 1e-12),
            target_zone=target_zone,
            target=target_price,
            context_zone=context,
        )
        self.setups.append(setup)
        self._active[setup.setup_id] = setup
        self._audit(context)
        self._audit(target_zone)
        self._inc("local_liquidity_setup_created")
        self._trace(
            "local_liquidity_setup_created",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            pivot_id=pivot.pivot_id,
            pivot_span=pivot.span,
            pivot_price=pivot.price,
            side=side.name,
            target_zone_id=target_zone.zone_id,
            target=target_price,
            typical_quote_volume=setup.typical_quote_volume,
            rule_provenance=(
                LOCAL_LIQUIDITY_FLOW_RULE,
                LOCAL_LIQUIDITY_TARGET_RULE,
            ),
        )
        return setup

    def _make_plan(
        self,
        setup: LocalLiquiditySetup,
        bar: Candle,
        observation: FlowObservation,
    ) -> V5TradePlan | None:
        stop = (
            setup.interaction_extreme - self.tick_size
            if setup.side is Side.LONG
            else setup.interaction_extreme + self.tick_size
        )
        entry = bar.close
        risk = entry - stop if setup.side is Side.LONG else stop - entry
        reward = setup.target - entry if setup.side is Side.LONG else entry - setup.target
        if risk <= 0.0 or reward <= 0.0:
            self._finish(
                setup,
                SetupState.NO_TRADE_GEOMETRY,
                bar.ts_close_ns,
                "local_liquidity_nonpositive_geometry",
                entry=entry,
                stop=stop,
                target=setup.target,
            )
            return None
        gross_rr = reward / risk
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                SetupState.NO_TRADE_GEOMETRY,
                bar.ts_close_ns,
                "local_liquidity_below_minimum_rr",
                entry=entry,
                stop=stop,
                target=setup.target,
                gross_rr=gross_rr,
            )
            return None

        self.sequence += 1
        trigger_kind = (
            FlowTriggerKind.REPEATED_SELL_ABSORPTION
            if setup.side is Side.LONG
            else FlowTriggerKind.REPEATED_BUY_ABSORPTION
        )
        volume_ratio = setup.cumulative_quote_volume / setup.typical_quote_volume
        delta_ratio = abs(setup.cumulative_signed_taker_quote) / setup.median_abs_delta
        rules = provenance()
        plan = V5TradePlan(
            plan_id=f"ecv5-local-flow-{self.symbol}-{self.sequence:08d}",
            setup_id=setup.setup_id,
            causal_event_id=(
                f"{self.symbol}:LOCAL_5M_LIQUIDITY:{setup.pivot_id}:"
                f"{setup.interaction_time_ns}"
            ),
            family="LOCAL_5M_SWEEP_ABSORPTION",
            scale_name="LOCAL_LIQUIDITY",
            scenario_path=ScenarioPath.REJECTION.value,
            symbol=self.symbol,
            side=setup.side,
            higher_timeframe_minutes=self.LOCAL_MINUTES,
            decision_timeframe_minutes=self.FLOW_MINUTES,
            trigger_timeframe_minutes=self.FLOW_MINUTES,
            higher_zone_id=setup.context_zone.zone_id,
            higher_zone_kind=setup.context_zone.kind,
            higher_strength_ratio=setup.pivot_strength,
            lower_zone_id=setup.context_zone.zone_id,
            lower_zone_kind=setup.context_zone.kind,
            lower_strength_ratio=setup.pivot_strength,
            target_zone_id=setup.target_zone.zone_id,
            target_zone_kind=setup.target_zone.kind,
            trigger_zone_id=(
                f"FLOW_LOCAL:{setup.pivot_id}:{bar.ts_close_ns}"
            ),
            trigger_zone_kind=trigger_kind,
            trigger_strength_ratio=volume_ratio * delta_ratio,
            overlap_lower=setup.context_zone.lower,
            overlap_upper=setup.context_zone.upper,
            interaction_time_ns=setup.interaction_time_ns,
            setup_observed_time_ns=setup.pivot_observed_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=setup.target,
            gross_rr=gross_rr,
            rule_provenance=rules,
            source_rule_count=len(rules),
        )
        self.plans.append(plan)
        setup.state = SetupState.PLANNED
        setup.terminal_reason = "local_liquidity_flow_plan_created"
        self._active.pop(setup.setup_id, None)
        self._inc("local_liquidity_flow_plan_created")
        self._trace(
            "local_liquidity_flow_plan_created",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            plan_id=plan.plan_id,
            pivot_id=setup.pivot_id,
            side=setup.side.name,
            interaction_time_ns=setup.interaction_time_ns,
            entry=entry,
            stop=stop,
            target=setup.target,
            gross_rr=gross_rr,
            flow_kind=trigger_kind.value,
            flow_mechanism="LOCAL_FIRST_VOLUME_BUCKET_ABSORPTION",
            flow_episode_bars=setup.episode_bars,
            flow_cumulative_quote_volume=setup.cumulative_quote_volume,
            flow_episode_cumulative_delta=setup.cumulative_signed_taker_quote,
            flow_volume_clock_ratio=volume_ratio,
            flow_cumulative_delta_ratio=delta_ratio,
            flow_activity_ratio=observation.activity_ratio,
            flow_delta_ratio=observation.delta_ratio,
            flow_delta_share=observation.delta_share,
            flow_body_ratio=observation.body_ratio,
            flow_trade_size_ratio=observation.trade_size_ratio,
            flow_impact_per_activity=observation.impact_per_activity,
            rule_provenance=(
                LOCAL_LIQUIDITY_FLOW_RULE,
                LOCAL_LIQUIDITY_TARGET_RULE,
            ),
        )
        return plan

    def _advance_setup(
        self,
        setup: LocalLiquiditySetup,
        bar: Candle,
        observation: FlowObservation,
    ) -> V5TradePlan | None:
        if setup.side is Side.LONG:
            setup.interaction_extreme = min(setup.interaction_extreme, bar.low)
            target_spent = bar.high >= setup.target
            reclaimed = bar.close > setup.pivot_price
        else:
            setup.interaction_extreme = max(setup.interaction_extreme, bar.high)
            target_spent = bar.low <= setup.target
            reclaimed = bar.close < setup.pivot_price
        if target_spent:
            self._finish(
                setup,
                SetupState.TARGET_SPENT,
                bar.ts_close_ns,
                "local_liquidity_target_spent_before_entry",
            )
            return None

        setup.cumulative_quote_volume += observation.quote_volume
        setup.cumulative_signed_taker_quote += observation.signed_taker_quote
        setup.episode_bars += 1
        if setup.cumulative_quote_volume < setup.typical_quote_volume:
            self._inc("local_liquidity_volume_bucket_accumulating")
            return None

        intended_progress = self._intended_progress(
            setup.side,
            setup.episode_open,
            bar.close,
        )
        absorbed = (
            reclaimed
            and self._opposite_delta(
                setup.side,
                setup.cumulative_signed_taker_quote,
            )
            and intended_progress >= 0.0
        )
        if not absorbed:
            self._finish(
                setup,
                SetupState.UNRESOLVED,
                bar.ts_close_ns,
                "local_liquidity_first_volume_bucket_not_absorbed",
                reclaimed=reclaimed,
                intended_progress=intended_progress,
                cumulative_signed_taker_quote=setup.cumulative_signed_taker_quote,
                cumulative_quote_volume=setup.cumulative_quote_volume,
            )
            return None
        return self._make_plan(setup, bar, observation)

    def _discover(
        self,
        bar: Candle,
        observation: FlowObservation | None,
    ) -> list[LocalLiquiditySetup]:
        lows, highs = self._eligible_touched_pivots(bar)
        if not lows and not highs:
            return []
        for pivot in lows + highs:
            self._retire(pivot, bar.ts_close_ns)
        if lows and highs:
            self._inc("local_liquidity_ambiguous_two_sided_sweep")
            self._trace(
                "local_liquidity_ambiguous_two_sided_sweep",
                bar.ts_close_ns,
                low_pivot_ids=[item.pivot_id for item in lows],
                high_pivot_ids=[item.pivot_id for item in highs],
            )
            return []
        if observation is None:
            self._inc("local_liquidity_interaction_without_flow_baseline")
            return []
        if lows:
            pivot = self._deepest(lows, Side.LONG)
            setup = self._start_setup(pivot, Side.LONG, bar, observation)
        else:
            pivot = self._deepest(highs, Side.SHORT)
            setup = self._start_setup(pivot, Side.SHORT, bar, observation)
        return [] if setup is None else [setup]

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.HIGHER_MINUTES:
            self.higher_structure.on_bar(bar)
            self._inc("higher_structure_bar")
            return []
        if timeframe_minutes == self.LOCAL_MINUTES:
            self.local_structure.on_bar(bar)
            self._inc("local_structure_bar")
            return []
        if timeframe_minutes != self.FLOW_MINUTES:
            return []

        observation = self.flow.observe(bar)
        created = self._discover(bar, observation)
        if observation is None:
            return []
        plans: list[V5TradePlan] = []
        for setup in list(self._active.values()):
            # Include the interaction bar exactly once for a newly created setup.
            if setup in created or bar.ts_close_ns > setup.interaction_time_ns:
                plan = self._advance_setup(setup, bar, observation)
                if plan is not None:
                    plans.append(plan)
        return sorted(
            plans,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        output, self.trace_events = self.trace_events, []
        return output

    def find_zone(self, zone_id: str) -> StructureZone | None:
        return next(
            (zone for zone in self.audit_zones if zone.zone_id == zone_id),
            None,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self.counts.items())),
            "setups": len(self.setups),
            "active_setups": len(self._active),
            "plans": len(self.plans),
            "retired_pivots": len(self._retired_pivots),
            "local_structure": dict(self.local_structure.diagnostics),
            "higher_structure": dict(self.higher_structure.diagnostics),
            "flow": self.flow.diagnostics,
            "rules": (
                LOCAL_LIQUIDITY_FLOW_RULE,
                LOCAL_LIQUIDITY_TARGET_RULE,
                LOCAL_LIQUIDITY_ALIGNMENT_RULE,
            ),
        }


class EasyChartRE1LocalLiquidityFlowBundle(EasyChartRE1VolumeClockMicroCoreBundle):
    """Local-liquidity family evaluated alone under the same account contract."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.local_liquidity = LocalLiquidityFlowEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self._local_liquidity_counts: dict[str, int] = {}
        self._local_liquidity_trace: list[dict[str, Any]] = []

    def _llinc(self, key: str) -> None:
        self._local_liquidity_counts[key] = self._local_liquidity_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return self.local_liquidity.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return self.local_liquidity.plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes in self.detectors:
            self.detectors[timeframe_minutes].on_bar(bar)
        if timeframe_minutes == self.CONTEXT_MINUTES:
            self._update_macro_context(bar)
        elif timeframe_minutes == self.LOCAL_CONTEXT_MINUTES:
            self._update_local_direction(bar)
            self._update_decision_footprints(bar)

        raw = self.local_liquidity.on_bar(timeframe_minutes, bar)
        for zone in self.local_liquidity.audit_zones:
            destination = zone.timeframe_minutes if zone.timeframe_minutes in self.detectors else 5
            self.detectors[destination].register(zone)
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._local_side is not None and plan.side is not self._local_side:
                self._llinc("local_liquidity_against_confirmed_15m_structure_suppressed")
                self._local_liquidity_trace.append(
                    {
                        "scenario_kind": "local_liquidity_against_confirmed_15m_structure_suppressed",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "local_side": self._local_side.name,
                        "rule_provenance": LOCAL_LIQUIDITY_ALIGNMENT_RULE,
                    },
                )
                continue
            if self._duplicate_episode(plan):
                self._llinc("local_liquidity_duplicate_episode_suppressed")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._llinc("local_liquidity_plan_allowed")
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            self.local_liquidity.drain_trace()
            + self._bundle_trace
            + self._local_liquidity_trace
        )
        self._bundle_trace = []
        self._local_liquidity_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.local_liquidity.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "local_liquidity_flow_family": self.local_liquidity.diagnostics,
            "router_counts": dict(sorted(self._local_liquidity_counts.items())),
            "rules": (
                LOCAL_LIQUIDITY_FLOW_RULE,
                LOCAL_LIQUIDITY_TARGET_RULE,
                LOCAL_LIQUIDITY_ALIGNMENT_RULE,
            ),
        }


MultiScaleScenarioBundle = EasyChartRE1LocalLiquidityFlowBundle
