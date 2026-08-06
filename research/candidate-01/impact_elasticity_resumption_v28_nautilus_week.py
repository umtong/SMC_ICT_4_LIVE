#!/usr/bin/env python3
"""Causal impact-elasticity decay/resumption continuation candidate.

This scenario is independent of the failed-sweep/MSS and funding-window
families.  A strong equal-notional order-flow initiative which creates value
outside a completed twenty-event structure is an observation, not an entry.
The market must then reveal a transient impact-decay phase and a renewed
permanent component:

1. Three completed equal-notional events carry a directionally coherent flow
   shock, move at least the cost-resolved 40-bp structural distance, and close
   beyond completed external structure with an impact elasticity no weaker
   than the preceding causal distribution.
2. A later completed event contains opposite aggressive flow and an actual
   pullback, but the close preserves outside value.  In the primary rule the
   adverse price response per unit of counterflow must be lower than the
   initiative response, identifying replenishment rather than value failure.
3. A subsequent aligned-flow event closes through the pullback extreme while
   outside value still holds.  Its impact elasticity must exceed the adverse
   pullback elasticity.  This is the entry signal.
4. The stop invalidates the complete initiative/pullback path and accepted
   boundary, plus one 7-bp side-cost buffer.  The target is one completed
   pre-initiative structure width beyond the swept boundary.

The ``sequence-only-control`` removes the impact-elasticity comparisons while
preserving every event, state transition, entry, stop and target rule.  It is
the single core-variable ablation if the primary first week fails.

Candidate logic emits immutable ScenarioPlan objects only.  Official Binance
Vision USD-M aggregate trades are converted one-for-one to NautilusTrader
TradeTick objects; orders, fills, commissions, margin, positions and account NAV
are exclusively NautilusTrader-owned.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
from statistics import median
import sys
from typing import Any, Deque, Iterable, Literal

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from adaptive_aggtrade_clock import build_daily_cost_resolved_bars  # noqa: E402
from aggtrade_data import AggTrade, download_aggtrade_days, iter_downloads  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402
from directional_change_failed_sweep_week import MAXIMUM_HOLD_NS  # noqa: E402
from impact_regime_probe import (  # noqa: E402
    EventFeature,
    ImpactRegimeDetector,
    ScenarioPlan,
    path_efficiency,
)
from intrinsic_external_liquidity_v2_daily_week import ROUND_TRIP_COST_BPS  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402


RULES = ("elasticity-resumption", "sequence-only-control")
CONTEXT_DAYS = 4
NS_PER_DAY = 86_400_000_000_000
FLUSH_TICKS = 3
STRUCTURE_BARS = 20
PULSE_BARS = 3
FLOW_SCORE_THRESHOLD = 3.0
ELASTICITY_HISTORY = 120
MINIMUM_ELASTICITY_HISTORY = 40
RESPONSE_BARS = 6
COST_RESOLVED_MOVE_BPS = (
    ROUND_TRIP_COST_BPS * 0.65 / (1.0 - 0.65) + ROUND_TRIP_COST_BPS
)
COST_RESOLVED_MOVE_FRACTION = COST_RESOLVED_MOVE_BPS / 10_000.0
SIDE_COST_BUFFER_FRACTION = 7.0 / 10_000.0


@dataclass(frozen=True, slots=True)
class InitiativeEvent:
    scenario_id: str
    event_index: int
    event_time_ns: int
    side: str
    boundary: float
    structure_high: float
    structure_low: float
    structure_width: float
    pulse_high: float
    pulse_low: float
    pulse_move_fraction: float
    pulse_flow_score: float
    raw_flow_sum: float
    path_efficiency: float
    initiative_elasticity: float
    prior_median_elasticity: float
    classification: str
    reason_code: str


@dataclass(slots=True)
class InitiativeSetup:
    scenario_id: str
    side: Side
    created_index: int
    created_time_ns: int
    expiry_index: int
    boundary: float
    structure_high: float
    structure_low: float
    structure_width: float
    pulse_high: float
    pulse_low: float
    path_high: float
    path_low: float
    initiative_elasticity: float
    pulse_flow_score: float
    pulse_move_fraction: float
    pulse_efficiency: float
    pullback_index: int | None = None
    pullback_time_ns: int | None = None
    pullback_high: float | None = None
    pullback_low: float | None = None
    adverse_elasticity: float | None = None


@dataclass(frozen=True, slots=True)
class ScenarioTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    side: str
    boundary: float
    path_high: float
    path_low: float
    close: float
    imbalance_z: float | None
    aligned_close_change: float | None
    event_elasticity: float | None
    initiative_elasticity: float
    adverse_elasticity: float | None


def _one_bar_elasticity(feature: EventFeature, previous: EventFeature) -> float | None:
    previous_close = float(previous.bar.close)
    if previous_close <= 0.0:
        return None
    signed_flow = float(feature.bar.imbalance)
    if abs(signed_flow) <= 1e-9:
        return None
    return abs(float(feature.bar.close) - previous_close) / previous_close / abs(signed_flow)


class ImpactElasticityStateMachine:
    """Outside initiative -> weak counter-impact -> strong resumption."""

    def __init__(self, *, rule: str) -> None:
        if rule not in RULES:
            raise ValueError(f"unknown rule {rule}")
        self.rule = rule
        self.active: list[InitiativeSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.initiatives: list[InitiativeEvent] = []
        self.transitions: list[ScenarioTransition] = []
        self.counts: Counter[str] = Counter()
        self.elasticity_history: Deque[float] = deque(maxlen=ELASTICITY_HISTORY)

    @staticmethod
    def _aligned_z(side: Side, feature: EventFeature) -> float | None:
        if feature.imbalance_z is None:
            return None
        return side.sign * float(feature.imbalance_z)

    def _transition(
        self,
        *,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
        aligned_close_change: float | None,
        event_elasticity: float | None,
    ) -> None:
        self.transitions.append(
            ScenarioTransition(
                scenario_id=setup.scenario_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=int(feature.bar.end_time_ns),
                reason_code=reason_code,
                side=setup.side.value,
                boundary=float(setup.boundary),
                path_high=float(setup.path_high),
                path_low=float(setup.path_low),
                close=float(feature.bar.close),
                imbalance_z=(
                    float(feature.imbalance_z)
                    if feature.imbalance_z is not None
                    else None
                ),
                aligned_close_change=aligned_close_change,
                event_elasticity=event_elasticity,
                initiative_elasticity=float(setup.initiative_elasticity),
                adverse_elasticity=setup.adverse_elasticity,
            ),
        )

    @staticmethod
    def _outside_holds(setup: InitiativeSetup, feature: EventFeature) -> bool:
        return (
            float(feature.bar.close) > setup.boundary
            if setup.side is Side.LONG
            else float(feature.bar.close) < setup.boundary
        )

    @staticmethod
    def _target(setup: InitiativeSetup) -> float:
        return (
            setup.boundary + setup.structure_width
            if setup.side is Side.LONG
            else setup.boundary - setup.structure_width
        )

    @staticmethod
    def _target_touched(setup: InitiativeSetup, feature: EventFeature) -> bool:
        target = ImpactElasticityStateMachine._target(setup)
        return (
            float(feature.bar.high) >= target
            if setup.side is Side.LONG
            else float(feature.bar.low) <= target
        )

    @staticmethod
    def _resumption_break(setup: InitiativeSetup, feature: EventFeature) -> bool:
        if setup.pullback_high is None or setup.pullback_low is None:
            return False
        return (
            float(feature.bar.close) > setup.pullback_high
            if setup.side is Side.LONG
            else float(feature.bar.close) < setup.pullback_low
        )

    def _emit_plan(
        self,
        *,
        setup: InitiativeSetup,
        feature: EventFeature,
        index: int,
    ) -> None:
        target = self._target(setup)
        if setup.side is Side.LONG:
            invalidation = min(setup.path_low, setup.boundary)
            stop = invalidation * (1.0 - SIDE_COST_BUFFER_FRACTION)
        else:
            invalidation = max(setup.path_high, setup.boundary)
            stop = invalidation * (1.0 + SIDE_COST_BUFFER_FRACTION)
        geometry = (
            stop < float(feature.bar.close) < target
            if setup.side is Side.LONG
            else target < float(feature.bar.close) < stop
        )
        if not geometry:
            self.counts["invalid_plan_geometry"] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="RESUMPTION_PLAN_GEOMETRY_INVALID",
                aligned_close_change=None,
                event_elasticity=None,
            )
            return
        plan = ScenarioPlan(
            scenario_id=setup.scenario_id + f":resumption:{index}",
            response="CONTINUATION",
            side=setup.side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=float(stop),
            target_price=float(target),
            confirmation_hold_price=float(setup.boundary),
            structure_high=float(setup.structure_high),
            structure_low=float(setup.structure_low),
            structure_midpoint=0.5 * (setup.structure_high + setup.structure_low),
            pulse_high=float(setup.path_high),
            pulse_low=float(setup.path_low),
            pulse_flow_score=float(setup.pulse_flow_score),
            pulse_move_atr=0.0,
            pulse_path_efficiency=float(setup.pulse_efficiency),
            pulse_close_location=0.0,
            reason_code="OUTSIDE_IMPACT_DECAYED_AND_ELASTICITY_RESUMED",
        )
        self.plans.append(plan)
        self.counts["plans_emitted"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="PLAN_EMITTED",
            reason_code=plan.reason_code,
            aligned_close_change=None,
            event_elasticity=None,
        )

    def _update_active(
        self,
        *,
        index: int,
        feature: EventFeature,
        previous: EventFeature,
    ) -> None:
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
                    reason_code="IMPACT_DECAY_RESUMPTION_WINDOW_EXPIRED",
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

            if setup.pullback_index is None:
                counterflow_pullback = (
                    aligned_z is not None
                    and aligned_z < 0.0
                    and aligned_change < 0.0
                    and event_elasticity is not None
                )
                elasticity_ok = (
                    self.rule == "sequence-only-control"
                    or (
                        event_elasticity is not None
                        and event_elasticity < setup.initiative_elasticity
                    )
                )
                if counterflow_pullback and elasticity_ok:
                    setup.pullback_index = index
                    setup.pullback_time_ns = int(feature.bar.end_time_ns)
                    setup.pullback_high = float(feature.bar.high)
                    setup.pullback_low = float(feature.bar.low)
                    setup.adverse_elasticity = float(event_elasticity)
                    self.counts["weak_counterimpact_pullbacks"] += 1
                    self._transition(
                        setup=setup,
                        feature=feature,
                        index=index,
                        event_type="PULLBACK_ACCEPTED",
                        reason_code=(
                            "COUNTERFLOW_PRICE_IMPACT_WEAKER_THAN_INITIATIVE"
                            if self.rule == "elasticity-resumption"
                            else "COUNTERFLOW_PULLBACK_OUTSIDE_VALUE_HELD"
                        ),
                        aligned_close_change=aligned_change,
                        event_elasticity=event_elasticity,
                    )
                    remaining.append(setup)
                    continue
                if counterflow_pullback and not elasticity_ok:
                    self.counts["counterimpact_too_efficient"] += 1
                remaining.append(setup)
                continue

            assert setup.pullback_high is not None
            assert setup.pullback_low is not None
            # Compare the completed resumption close with the pullback extreme
            # known before this event. Updating the pullback extreme first
            # would require a close above its own high (or below its own low).
            prior_pullback_high = float(setup.pullback_high)
            prior_pullback_low = float(setup.pullback_low)
            resumption_break = (
                float(feature.bar.close) > prior_pullback_high
                if setup.side is Side.LONG
                else float(feature.bar.close) < prior_pullback_low
            )
            adverse = setup.adverse_elasticity
            resumed = (
                aligned_z is not None
                and aligned_z > 0.0
                and aligned_change > 0.0
                and resumption_break
                and event_elasticity is not None
            )
            elasticity_ok = (
                self.rule == "sequence-only-control"
                or (
                    adverse is not None
                    and event_elasticity is not None
                    and event_elasticity > adverse
                )
            )
            if resumed and elasticity_ok:
                self.counts["elasticity_resumptions"] += 1
                self._emit_plan(setup=setup, feature=feature, index=index)
                continue
            if resumed and not elasticity_ok:
                self.counts["resumption_impact_not_recovered"] += 1
            # Events which did not complete resumption remain part of the
            # evolving pullback path for a later causal break.
            setup.pullback_high = max(prior_pullback_high, float(feature.bar.high))
            setup.pullback_low = min(prior_pullback_low, float(feature.bar.low))
            remaining.append(setup)
        self.active = remaining

    def _try_arm(self, *, index: int, features: list[EventFeature]) -> None:
        minimum = max(
            STRUCTURE_BARS + PULSE_BARS + 1,
            MINIMUM_ELASTICITY_HISTORY + PULSE_BARS + 1,
        )
        if index < minimum or len(self.elasticity_history) < MINIMUM_ELASTICITY_HISTORY:
            return
        pulse_start = index - PULSE_BARS + 1
        previous_pulse_end = index - 1
        pulse_features = features[pulse_start : index + 1]
        if any(item.imbalance_z is None for item in pulse_features):
            return
        score = sum(float(item.imbalance_z) for item in pulse_features)
        previous_features = features[pulse_start - 1 : previous_pulse_end + 1]
        previous_score = (
            sum(float(item.imbalance_z) for item in previous_features)
            if all(item.imbalance_z is not None for item in previous_features)
            else 0.0
        )
        if abs(score) < FLOW_SCORE_THRESHOLD:
            return
        if abs(previous_score) >= FLOW_SCORE_THRESHOLD and previous_score * score > 0.0:
            return
        side = Side.LONG if score > 0.0 else Side.SHORT
        same_direction = sum(
            1
            for item in pulse_features
            if item.imbalance_z is not None and side.sign * float(item.imbalance_z) > 0.0
        )
        if same_direction < 2:
            return

        structure_start = pulse_start - STRUCTURE_BARS
        structure = [row.bar for row in features[structure_start:pulse_start]]
        start_close = float(features[pulse_start - 1].bar.close)
        current = features[index].bar
        structure_high = max(float(row.high) for row in structure)
        structure_low = min(float(row.low) for row in structure)
        structure_width = structure_high - structure_low
        if structure_width <= 0.0 or start_close <= 0.0:
            return
        boundary = structure_high if side is Side.LONG else structure_low
        outside = (
            float(current.close) > boundary
            if side is Side.LONG
            else float(current.close) < boundary
        )
        if not outside:
            self.counts["flow_shock_without_outside_value"] += 1
            return
        move_fraction = side.sign * (float(current.close) - start_close) / start_close
        if move_fraction < COST_RESOLVED_MOVE_FRACTION:
            self.counts["flow_shock_below_cost_resolved_move"] += 1
            return
        closes = [start_close, *[float(row.bar.close) for row in pulse_features]]
        efficiency = path_efficiency(closes)
        if efficiency < 0.55:
            self.counts["flow_shock_path_inefficient"] += 1
            return
        raw_flow_sum = sum(float(row.bar.imbalance) for row in pulse_features)
        if side.sign * raw_flow_sum <= 0.0 or abs(raw_flow_sum) <= 1e-9:
            self.counts["flow_shock_raw_flow_incoherent"] += 1
            return
        initiative_elasticity = move_fraction / abs(raw_flow_sum)
        prior_median = float(median(self.elasticity_history))
        elasticity_ok = (
            self.rule == "sequence-only-control"
            or initiative_elasticity >= prior_median
        )
        scenario_id = f"v28-impact:{index}:{side.value.lower()}:{current.end_time_ns}"
        pulse_high = max(float(row.bar.high) for row in pulse_features)
        pulse_low = min(float(row.bar.low) for row in pulse_features)
        if not elasticity_ok:
            self.counts["initiative_impact_below_causal_median"] += 1
            self.initiatives.append(
                InitiativeEvent(
                    scenario_id=scenario_id,
                    event_index=index,
                    event_time_ns=int(current.end_time_ns),
                    side=side.value,
                    boundary=float(boundary),
                    structure_high=structure_high,
                    structure_low=structure_low,
                    structure_width=structure_width,
                    pulse_high=pulse_high,
                    pulse_low=pulse_low,
                    pulse_move_fraction=move_fraction,
                    pulse_flow_score=score,
                    raw_flow_sum=raw_flow_sum,
                    path_efficiency=efficiency,
                    initiative_elasticity=initiative_elasticity,
                    prior_median_elasticity=prior_median,
                    classification="REJECTED",
                    reason_code="INITIATIVE_IMPACT_NOT_ABOVE_CAUSAL_MEDIAN",
                ),
            )
            return

        setup = InitiativeSetup(
            scenario_id=scenario_id,
            side=side,
            created_index=index,
            created_time_ns=int(current.end_time_ns),
            expiry_index=index + RESPONSE_BARS,
            boundary=float(boundary),
            structure_high=structure_high,
            structure_low=structure_low,
            structure_width=structure_width,
            pulse_high=pulse_high,
            pulse_low=pulse_low,
            path_high=pulse_high,
            path_low=pulse_low,
            initiative_elasticity=initiative_elasticity,
            pulse_flow_score=score,
            pulse_move_fraction=move_fraction,
            pulse_efficiency=efficiency,
        )
        self.active.append(setup)
        self.counts["initiatives_armed"] += 1
        self.initiatives.append(
            InitiativeEvent(
                scenario_id=scenario_id,
                event_index=index,
                event_time_ns=int(current.end_time_ns),
                side=side.value,
                boundary=float(boundary),
                structure_high=structure_high,
                structure_low=structure_low,
                structure_width=structure_width,
                pulse_high=pulse_high,
                pulse_low=pulse_low,
                pulse_move_fraction=move_fraction,
                pulse_flow_score=score,
                raw_flow_sum=raw_flow_sum,
                path_efficiency=efficiency,
                initiative_elasticity=initiative_elasticity,
                prior_median_elasticity=prior_median,
                classification="ARMED",
                reason_code="COST_RESOLVED_OUTSIDE_IMPACT_WITH_HIGH_ELASTICITY",
            ),
        )
        self._transition(
            setup=setup,
            feature=features[index],
            index=index,
            event_type="INITIATIVE_ARMED",
            reason_code="COST_RESOLVED_OUTSIDE_IMPACT_WITH_HIGH_ELASTICITY",
            aligned_close_change=None,
            event_elasticity=initiative_elasticity,
        )

    def on_feature(self, *, index: int, features: list[EventFeature]) -> None:
        feature = features[index]
        if index > 0:
            previous = features[index - 1]
            self._update_active(
                index=index,
                feature=feature,
                previous=previous,
            )
            self._try_arm(index=index, features=features)
            elasticity = _one_bar_elasticity(feature, previous)
            if elasticity is not None:
                self.elasticity_history.append(float(elasticity))


def execution_trade_windows(
    records: Iterable[Any],
    *,
    plans: list[ScenarioPlan],
    start_ns: int,
    end_ns: int,
    maximum_hold_ns: int,
) -> tuple[list[AggTrade], list[tuple[int, int]]]:
    before_ns = 60_000_000_000
    after_ns = 120_000_000_000
    intervals = sorted(
        (
            max(start_ns, int(plan.signal_time_ns) - before_ns),
            min(end_ns - 1, int(plan.signal_time_ns) + maximum_hold_ns + after_ns),
        )
        for plan in plans
        if start_ns <= int(plan.signal_time_ns) < end_ns
    )
    merged: list[tuple[int, int]] = []
    for left, right in intervals:
        if right < left:
            continue
        if not merged or left > merged[-1][1] + 1:
            merged.append((left, right))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))

    selected: list[AggTrade] = []
    marker_days: set[int] = set()
    interval_index = 0
    flush = 0
    for trade in iter_downloads(records):
        ts_ns = int(trade.ts_event_ns)
        if ts_ns < start_ns:
            continue
        if ts_ns >= end_ns:
            if flush < FLUSH_TICKS:
                selected.append(trade)
                flush += 1
                continue
            break
        day_id = ts_ns // NS_PER_DAY
        if day_id not in marker_days:
            marker_days.add(day_id)
            selected.append(trade)
            continue
        while interval_index < len(merged) and ts_ns > merged[interval_index][1]:
            interval_index += 1
        if (
            interval_index < len(merged)
            and merged[interval_index][0] <= ts_ns <= merged[interval_index][1]
        ):
            selected.append(trade)

    expected_days = (end_ns - start_ns) // NS_PER_DAY
    if len(marker_days) != expected_days:
        raise RuntimeError(f"expected {expected_days} daily markers, found {len(marker_days)}")
    if flush != FLUSH_TICKS:
        raise RuntimeError(f"expected {FLUSH_TICKS} flush ticks, found {flush}")
    return selected, merged


def run(args: argparse.Namespace) -> int:
    execution = load_execution(args.execution_config)
    evaluation_start = parse_utc_date(args.week)
    evaluation_end = evaluation_start + timedelta(days=7)
    context_start = evaluation_start - timedelta(days=CONTEXT_DAYS)
    clock_source_start = context_start - timedelta(days=1)
    download_end = evaluation_end + timedelta(minutes=1)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=clock_source_start,
        end=download_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    bars, calibrations = build_daily_cost_resolved_bars(
        records,
        bar_start=context_start,
        bar_end=evaluation_end,
        minimum_range_bps=ROUND_TRIP_COST_BPS,
    )
    feature_detector = ImpactRegimeDetector()
    scenario = ImpactElasticityStateMachine(rule=args.rule)
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        scenario.on_feature(index=index, features=feature_detector.features)

    plans = [
        plan
        for plan in scenario.plans
        if start_ns <= int(plan.signal_time_ns) < end_ns
    ]
    execution_trades, windows = execution_trade_windows(
        records,
        plans=plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_tick_plan_backtest(
        label=f"BTCUSDT-v28-{args.rule}-{evaluation_start.date().isoformat()}-7d",
        trades=execution_trades,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )

    pd.DataFrame(asdict(row) for row in scenario.initiatives).to_csv(
        output / "initiative_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        output / "scenario_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(plan) for plan in plans).to_csv(
        output / "scenario_plans.csv",
        index=False,
    )
    atomic_json(
        output / "daily_clock_calibrations.json",
        {"calibrations": [row.to_dict() for row in calibrations]},
    )

    payload = {
        "candidate": "outside impact elasticity decay and resumption continuation",
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_days": CONTEXT_DAYS,
        "structure_bars": STRUCTURE_BARS,
        "pulse_bars": PULSE_BARS,
        "flow_score_threshold": FLOW_SCORE_THRESHOLD,
        "cost_resolved_move_bps": COST_RESOLVED_MOVE_BPS,
        "response_bars": RESPONSE_BARS,
        "state_counts": dict(scenario.counts),
        "selected_plan_count": len(plans),
        "initiative_event_count": len(scenario.initiatives),
        "official_execution_trade_ticks": len(execution_trades),
        "execution_tick_windows": [list(row) for row in windows],
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "metrics": evidence.metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_elasticity_resumption_v28_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--rule", required=True, choices=RULES)
    parser.add_argument(
        "--execution-config",
        type=Path,
        default=HERE / "nautilus_execution.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-v28-impact-elasticity",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v28-impact-elasticity",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
