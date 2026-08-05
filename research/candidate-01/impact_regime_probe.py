#!/usr/bin/env python3
"""First-week aggregate-trade impact-regime day-trading candidate.

The candidate operates on a dollar-volume information clock calibrated only
from the UTC day before evaluation. Every event bar carries approximately equal
quote notional, so aggressive buy/sell imbalance is comparable without a fixed
time bucket.

A three-event sequential order-flow change is related to the immediately prior
20-event price structure. The same causal pulse has two mutually exclusive
market responses:

1. Efficient impact continuation
   Aggressive flow changes regime, price closes beyond prior external
   liquidity with an efficient directional path, and the next event still
   holds outside value. The target is one prior-range measured move.

2. Impact-exhaustion failed auction
   Aggressive flow changes regime and sweeps external liquidity, but price
   closes back inside the prior range. Opposite aggressive flow and a close
   through the pulse midpoint confirm the reversal. The target is the opposite
   edge of the pre-pulse range.

Signals, entries, invalidations and targets are state transitions rather than
stand-alone candle patterns. The first BTC week only is evaluated at 3% NAV
risk, one global position and 7 bps per side. No long-period evaluation is
available from this program.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from math import isfinite
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

from aggtrade_clock import (  # noqa: E402
    VolumeBar,
    calibrate_target_from_minutes,
    iter_volume_bars,
    minute_quote_totals,
)
from aggtrade_data import download_aggtrade_days, iter_downloads  # noqa: E402
from core import Side  # noqa: E402
from data import parse_utc_date  # noqa: E402


RISK_RATE = 0.03
COST_PER_SIDE = 0.0007
MAINTENANCE_MARGIN_RATE = 1.0 / 125.0 / 2.0
CLOCK_CALIBRATION_MINUTES = 1
FLOW_HISTORY = 120
ATR_HISTORY = 60
STRUCTURE_BARS = 20
PULSE_BARS = 3
PULSE_THRESHOLD = 3.0
EXHAUSTION_CONFIRM_BARS = 3
MAX_HOLD_BARS = 45
MINIMUM_PRICE_RISK_FRACTION = 0.65
MINIMUM_NET_REWARD_RISK = 1.35


@dataclass(frozen=True, slots=True)
class EventFeature:
    bar: VolumeBar
    true_range: float
    atr: float | None
    imbalance_z: float | None


@dataclass(frozen=True, slots=True)
class ScenarioPlan:
    scenario_id: str
    response: Literal["CONTINUATION", "EXHAUSTION_REVERSAL"]
    side: Side
    signal_bar_index: int
    signal_time_ns: int
    stop_price: float
    target_price: float
    confirmation_hold_price: float
    structure_high: float
    structure_low: float
    structure_midpoint: float
    pulse_high: float
    pulse_low: float
    pulse_flow_score: float
    pulse_move_atr: float
    pulse_path_efficiency: float
    pulse_close_location: float
    reason_code: str


@dataclass(slots=True)
class ExhaustionSetup:
    scenario_id: str
    outward_side: Side
    reversal_side: Side
    created_index: int
    expiry_index: int
    boundary: float
    structure_high: float
    structure_low: float
    structure_midpoint: float
    pulse_high: float
    pulse_low: float
    pulse_midpoint: float
    atr: float
    pulse_flow_score: float
    pulse_move_atr: float
    pulse_path_efficiency: float
    pulse_close_location: float


@dataclass(frozen=True, slots=True)
class PulseEvent:
    scenario_id: str
    bar_index: int
    event_time_ns: int
    direction: str
    flow_score: float
    previous_flow_score: float
    same_direction_bars: int
    atr: float
    structure_high: float
    structure_low: float
    structure_width_atr: float
    pulse_high: float
    pulse_low: float
    pulse_close: float
    move_atr: float
    path_efficiency: float
    aligned_close_location: float
    outward_excursion_atr: float
    close_beyond_boundary_atr: float
    classification: str
    reason: str


@dataclass(frozen=True, slots=True)
class ExecutionRejection:
    scenario_id: str
    response: str
    signal_time_ns: int
    entry_time_ns: int
    reason: str
    entry: float
    stop: float
    target: float
    price_risk_fraction: float | None
    net_reward_risk: float | None


@dataclass(frozen=True, slots=True)
class ExecutedTrade:
    scenario_id: str
    response: str
    side: str
    signal_time_ns: int
    entry_time_ns: int
    entry: float
    stop: float
    target: float
    confirmation_hold_price: float
    quantity: float
    entry_nav: float
    price_risk_fraction: float
    net_reward_risk_at_entry: float
    exit_time_ns: int
    exit_price: float
    exit_reason: str
    bars_held: int
    realized_r: float
    minimum_mark_r: float
    maximum_mark_r: float
    exit_nav: float


@dataclass(slots=True)
class ActivePosition:
    plan: ScenarioPlan
    entry_time_ns: int
    entry: float
    quantity: float
    entry_nav: float
    planned_loss_per_unit: float
    price_risk_fraction: float
    net_reward_risk: float
    bars_held: int = 0
    minimum_mark_r: float = 0.0
    maximum_mark_r: float = 0.0


def robust_z(value: float, history: Iterable[float]) -> float | None:
    values = [item for item in history if isfinite(item)]
    if len(values) < 40:
        return None
    center = median(values)
    deviations = [abs(item - center) for item in values]
    scale = 1.4826 * median(deviations)
    if scale <= 1e-9:
        return 0.0
    return (value - center) / scale


def true_range(bar: VolumeBar, previous_close: float | None) -> float:
    if previous_close is None:
        return bar.high - bar.low
    return max(
        bar.high - bar.low,
        abs(bar.high - previous_close),
        abs(bar.low - previous_close),
    )


def path_efficiency(closes: list[float]) -> float:
    if len(closes) < 2:
        return 0.0
    gross = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    return abs(closes[-1] - closes[0]) / gross if gross > 0.0 else 0.0


class ImpactRegimeDetector:
    def __init__(self) -> None:
        self.features: list[EventFeature] = []
        self.imbalance_history: Deque[float] = deque(maxlen=FLOW_HISTORY)
        self.tr_history: Deque[float] = deque(maxlen=ATR_HISTORY)
        self.pulse_events: list[PulseEvent] = []
        self.continuation_plans: list[ScenarioPlan] = []
        self.exhaustion_plans: list[ScenarioPlan] = []
        self.exhaustion_setups: list[ExhaustionSetup] = []
        self.counts: Counter[str] = Counter()

    def _flow_score(self, end_index: int) -> float | None:
        if end_index < PULSE_BARS - 1:
            return None
        values = [
            self.features[index].imbalance_z
            for index in range(end_index - PULSE_BARS + 1, end_index + 1)
        ]
        if any(value is None for value in values):
            return None
        return float(sum(value for value in values if value is not None))

    def _record_pulse(
        self,
        *,
        index: int,
        direction: Side,
        score: float,
        previous_score: float,
        atr: float,
        structure: list[VolumeBar],
        pulse: list[VolumeBar],
        move_atr: float,
        efficiency: float,
        aligned_location: float,
        outward_atr: float,
        close_beyond_atr: float,
        classification: str,
        reason: str,
    ) -> str:
        scenario_id = f"impact:{index}:{direction.value.lower()}:{self.features[index].bar.end_time_ns}"
        self.pulse_events.append(
            PulseEvent(
                scenario_id=scenario_id,
                bar_index=index,
                event_time_ns=self.features[index].bar.end_time_ns,
                direction=direction.value,
                flow_score=score,
                previous_flow_score=previous_score,
                same_direction_bars=sum(
                    1
                    for feature in self.features[index - PULSE_BARS + 1 : index + 1]
                    if feature.imbalance_z is not None
                    and direction.sign * feature.imbalance_z > 0.0
                ),
                atr=atr,
                structure_high=max(item.high for item in structure),
                structure_low=min(item.low for item in structure),
                structure_width_atr=(
                    max(item.high for item in structure)
                    - min(item.low for item in structure)
                ) / atr,
                pulse_high=max(item.high for item in pulse),
                pulse_low=min(item.low for item in pulse),
                pulse_close=pulse[-1].close,
                move_atr=move_atr,
                path_efficiency=efficiency,
                aligned_close_location=aligned_location,
                outward_excursion_atr=outward_atr,
                close_beyond_boundary_atr=close_beyond_atr,
                classification=classification,
                reason=reason,
            ),
        )
        return scenario_id

    def _process_exhaustion_setups(self, index: int) -> list[ScenarioPlan]:
        if not self.exhaustion_setups:
            return []
        feature = self.features[index]
        bar = feature.bar
        emitted: list[ScenarioPlan] = []
        remaining: list[ExhaustionSetup] = []
        for setup in self.exhaustion_setups:
            if index <= setup.created_index:
                remaining.append(setup)
                continue
            if index > setup.expiry_index:
                self.counts["exhaustion_expired"] += 1
                continue
            if setup.reversal_side is Side.SHORT:
                setup.pulse_high = max(setup.pulse_high, bar.high)
                invalidated = bar.close > setup.pulse_high + 0.20 * setup.atr
                opposite_flow = feature.imbalance_z is not None and feature.imbalance_z <= -0.50
                midpoint_break = bar.close < setup.pulse_midpoint
                inside = bar.close < setup.boundary
                stop = setup.pulse_high + 0.15 * setup.atr
                target = setup.structure_low
                hold = setup.boundary
            else:
                setup.pulse_low = min(setup.pulse_low, bar.low)
                invalidated = bar.close < setup.pulse_low - 0.20 * setup.atr
                opposite_flow = feature.imbalance_z is not None and feature.imbalance_z >= 0.50
                midpoint_break = bar.close > setup.pulse_midpoint
                inside = bar.close > setup.boundary
                stop = setup.pulse_low - 0.15 * setup.atr
                target = setup.structure_high
                hold = setup.boundary
            if invalidated:
                self.counts["exhaustion_invalidated_before_confirmation"] += 1
                continue
            if opposite_flow and midpoint_break and inside:
                plan = ScenarioPlan(
                    scenario_id=setup.scenario_id + f":confirm:{index}",
                    response="EXHAUSTION_REVERSAL",
                    side=setup.reversal_side,
                    signal_bar_index=index,
                    signal_time_ns=bar.end_time_ns,
                    stop_price=stop,
                    target_price=target,
                    confirmation_hold_price=hold,
                    structure_high=setup.structure_high,
                    structure_low=setup.structure_low,
                    structure_midpoint=setup.structure_midpoint,
                    pulse_high=setup.pulse_high,
                    pulse_low=setup.pulse_low,
                    pulse_flow_score=setup.pulse_flow_score,
                    pulse_move_atr=setup.pulse_move_atr,
                    pulse_path_efficiency=setup.pulse_path_efficiency,
                    pulse_close_location=setup.pulse_close_location,
                    reason_code="FAILED_IMPACT_OPPOSITE_FLOW_CONFIRMED",
                )
                emitted.append(plan)
                self.exhaustion_plans.append(plan)
                self.counts["exhaustion_confirmed"] += 1
                continue
            remaining.append(setup)
        self.exhaustion_setups = remaining
        return emitted

    def on_bar(self, bar: VolumeBar) -> list[ScenarioPlan]:
        previous_close = self.features[-1].bar.close if self.features else None
        tr = true_range(bar, previous_close)
        atr = float(median(self.tr_history)) if len(self.tr_history) >= 40 else None
        imbalance_z = robust_z(bar.imbalance, self.imbalance_history)
        self.features.append(
            EventFeature(
                bar=bar,
                true_range=tr,
                atr=atr,
                imbalance_z=imbalance_z,
            ),
        )
        index = len(self.features) - 1
        emitted = self._process_exhaustion_setups(index)

        minimum = FLOW_HISTORY + STRUCTURE_BARS + PULSE_BARS + 2
        if index < minimum or atr is None or atr <= 0.0:
            self.imbalance_history.append(bar.imbalance)
            self.tr_history.append(tr)
            return emitted

        score = self._flow_score(index)
        previous_score = self._flow_score(index - 1)
        if score is None or previous_score is None:
            self.imbalance_history.append(bar.imbalance)
            self.tr_history.append(tr)
            return emitted
        direction = Side.LONG if score > 0.0 else Side.SHORT
        same_direction = sum(
            1
            for feature in self.features[index - PULSE_BARS + 1 : index + 1]
            if feature.imbalance_z is not None
            and direction.sign * feature.imbalance_z > 0.0
        )
        crossed = abs(score) >= PULSE_THRESHOLD and (
            abs(previous_score) < PULSE_THRESHOLD
            or previous_score * score <= 0.0
        )
        if not crossed or same_direction < 2:
            self.imbalance_history.append(bar.imbalance)
            self.tr_history.append(tr)
            return emitted

        self.counts["flow_pulses"] += 1
        pulse_start = index - PULSE_BARS + 1
        structure_start = pulse_start - STRUCTURE_BARS
        structure = [feature.bar for feature in self.features[structure_start:pulse_start]]
        pulse = [feature.bar for feature in self.features[pulse_start : index + 1]]
        structure_high = max(item.high for item in structure)
        structure_low = min(item.low for item in structure)
        structure_mid = 0.5 * (structure_high + structure_low)
        structure_width = structure_high - structure_low
        pulse_high = max(item.high for item in pulse)
        pulse_low = min(item.low for item in pulse)
        start_close = self.features[pulse_start - 1].bar.close
        move_atr = direction.sign * (bar.close - start_close) / atr
        efficiency = path_efficiency([start_close, *[item.close for item in pulse]])
        aligned_location = bar.close_location if direction is Side.LONG else 1.0 - bar.close_location
        if direction is Side.LONG:
            boundary = structure_high
            outward_atr = max(pulse_high - boundary, 0.0) / atr
            close_beyond_atr = (bar.close - boundary) / atr
        else:
            boundary = structure_low
            outward_atr = max(boundary - pulse_low, 0.0) / atr
            close_beyond_atr = (boundary - bar.close) / atr

        continuation = (
            close_beyond_atr >= 0.10
            and move_atr >= 0.65
            and efficiency >= 0.55
            and aligned_location >= 0.70
            and structure_width >= 1.25 * atr
        )
        failed_auction = (
            outward_atr >= 0.08
            and close_beyond_atr <= 0.02
            and aligned_location <= 0.55
            and structure_width >= 1.25 * atr
        )
        if continuation:
            scenario_id = self._record_pulse(
                index=index,
                direction=direction,
                score=score,
                previous_score=previous_score,
                atr=atr,
                structure=structure,
                pulse=pulse,
                move_atr=move_atr,
                efficiency=efficiency,
                aligned_location=aligned_location,
                outward_atr=outward_atr,
                close_beyond_atr=close_beyond_atr,
                classification="EFFICIENT_CONTINUATION",
                reason="flow regime produced efficient outside value",
            )
            if direction is Side.LONG:
                stop = boundary - 0.20 * atr
                target = boundary + structure_width
            else:
                stop = boundary + 0.20 * atr
                target = boundary - structure_width
            plan = ScenarioPlan(
                scenario_id=scenario_id + ":continuation",
                response="CONTINUATION",
                side=direction,
                signal_bar_index=index,
                signal_time_ns=bar.end_time_ns,
                stop_price=stop,
                target_price=target,
                confirmation_hold_price=boundary,
                structure_high=structure_high,
                structure_low=structure_low,
                structure_midpoint=structure_mid,
                pulse_high=pulse_high,
                pulse_low=pulse_low,
                pulse_flow_score=score,
                pulse_move_atr=move_atr,
                pulse_path_efficiency=efficiency,
                pulse_close_location=aligned_location,
                reason_code="EFFICIENT_IMPACT_OUTSIDE_VALUE",
            )
            self.continuation_plans.append(plan)
            emitted.append(plan)
            self.counts["continuation_plans"] += 1
        elif failed_auction:
            scenario_id = self._record_pulse(
                index=index,
                direction=direction,
                score=score,
                previous_score=previous_score,
                atr=atr,
                structure=structure,
                pulse=pulse,
                move_atr=move_atr,
                efficiency=efficiency,
                aligned_location=aligned_location,
                outward_atr=outward_atr,
                close_beyond_atr=close_beyond_atr,
                classification="IMPACT_EXHAUSTION",
                reason="aggressive flow swept liquidity but failed to retain outside value",
            )
            setup = ExhaustionSetup(
                scenario_id=scenario_id,
                outward_side=direction,
                reversal_side=Side.SHORT if direction is Side.LONG else Side.LONG,
                created_index=index,
                expiry_index=index + EXHAUSTION_CONFIRM_BARS,
                boundary=boundary,
                structure_high=structure_high,
                structure_low=structure_low,
                structure_midpoint=structure_mid,
                pulse_high=pulse_high,
                pulse_low=pulse_low,
                pulse_midpoint=0.5 * (pulse_high + pulse_low),
                atr=atr,
                pulse_flow_score=score,
                pulse_move_atr=move_atr,
                pulse_path_efficiency=efficiency,
                pulse_close_location=aligned_location,
            )
            self.exhaustion_setups.append(setup)
            self.counts["exhaustion_setups"] += 1
        else:
            self._record_pulse(
                index=index,
                direction=direction,
                score=score,
                previous_score=previous_score,
                atr=atr,
                structure=structure,
                pulse=pulse,
                move_atr=move_atr,
                efficiency=efficiency,
                aligned_location=aligned_location,
                outward_atr=outward_atr,
                close_beyond_atr=close_beyond_atr,
                classification="NO_TRADE",
                reason="flow change lacked efficient acceptance or failed-auction response",
            )
            self.counts["unclassified_pulses"] += 1

        self.imbalance_history.append(bar.imbalance)
        self.tr_history.append(tr)
        return emitted


def net_per_unit(side: Side, entry: float, exit_price: float, cost: float) -> float:
    return side.sign * (exit_price - entry) - entry * cost - exit_price * cost


def mark_r(position: ActivePosition, price: float, cost: float) -> float:
    return net_per_unit(position.plan.side, position.entry, price, cost) / position.planned_loss_per_unit


def close_position(
    position: ActivePosition,
    *,
    exit_time_ns: int,
    exit_price: float,
    reason: str,
    cost: float,
) -> ExecutedTrade:
    realized_r = net_per_unit(
        position.plan.side,
        position.entry,
        exit_price,
        cost,
    ) / position.planned_loss_per_unit
    exit_nav = position.entry_nav * (1.0 + RISK_RATE * realized_r)
    return ExecutedTrade(
        scenario_id=position.plan.scenario_id,
        response=position.plan.response,
        side=position.plan.side.value,
        signal_time_ns=position.plan.signal_time_ns,
        entry_time_ns=position.entry_time_ns,
        entry=position.entry,
        stop=position.plan.stop_price,
        target=position.plan.target_price,
        confirmation_hold_price=position.plan.confirmation_hold_price,
        quantity=position.quantity,
        entry_nav=position.entry_nav,
        price_risk_fraction=position.price_risk_fraction,
        net_reward_risk_at_entry=position.net_reward_risk,
        exit_time_ns=exit_time_ns,
        exit_price=exit_price,
        exit_reason=reason,
        bars_held=position.bars_held,
        realized_r=realized_r,
        minimum_mark_r=position.minimum_mark_r,
        maximum_mark_r=position.maximum_mark_r,
        exit_nav=exit_nav,
    )


def simulate(
    *,
    features: list[EventFeature],
    plans: list[ScenarioPlan],
    evaluation_start_ns: int,
    evaluation_end_ns: int,
    starting_nav: float,
    cost: float,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame, pd.DataFrame]:
    schedules: dict[int, list[ScenarioPlan]] = {}
    for plan in plans:
        schedules.setdefault(plan.signal_bar_index, []).append(plan)
    nav = starting_nav
    high_water = starting_nav
    max_drawdown = 0.0
    minimum_margin_ratio: float | None = None
    pending: list[ScenarioPlan] = []
    active: ActivePosition | None = None
    trades: list[ExecutedTrade] = []
    rejections: list[ExecutionRejection] = []
    counters: Counter[str] = Counter()
    daily_rows: list[dict[str, Any]] = []
    current_day: str | None = None
    current_day_nav = starting_nav

    for index, feature in enumerate(features):
        bar = feature.bar
        if bar.end_time_ns < evaluation_start_ns:
            continue
        if bar.end_time_ns >= evaluation_end_ns:
            break

        occupied_at_start = active is not None
        if active is not None:
            active.bars_held += 1
            adverse = bar.low if active.plan.side is Side.LONG else bar.high
            favorable = bar.high if active.plan.side is Side.LONG else bar.low
            active.minimum_mark_r = min(active.minimum_mark_r, mark_r(active, adverse, cost))
            active.maximum_mark_r = max(active.maximum_mark_r, mark_r(active, favorable, cost))
            if active.plan.side is Side.LONG:
                stop_hit = bar.low <= active.plan.stop_price
                target_hit = bar.high >= active.plan.target_price
                stop_fill = min(active.plan.stop_price, bar.open) if bar.open <= active.plan.stop_price else active.plan.stop_price
            else:
                stop_hit = bar.high >= active.plan.stop_price
                target_hit = bar.low <= active.plan.target_price
                stop_fill = max(active.plan.stop_price, bar.open) if bar.open >= active.plan.stop_price else active.plan.stop_price
            closed: ExecutedTrade | None = None
            if stop_hit:
                closed = close_position(
                    active,
                    exit_time_ns=bar.end_time_ns,
                    exit_price=stop_fill,
                    reason="STOP",
                    cost=cost,
                )
            elif target_hit:
                closed = close_position(
                    active,
                    exit_time_ns=bar.end_time_ns,
                    exit_price=active.plan.target_price,
                    reason="TARGET",
                    cost=cost,
                )
            elif active.bars_held >= MAX_HOLD_BARS:
                closed = close_position(
                    active,
                    exit_time_ns=bar.end_time_ns,
                    exit_price=bar.close,
                    reason="TIME",
                    cost=cost,
                )
            if closed is not None:
                nav = closed.exit_nav
                trades.append(closed)
                active = None

        if not occupied_at_start and active is None and pending:
            viable: list[tuple[float, ScenarioPlan, float, float, float, float]] = []
            for plan in pending:
                entry = bar.open
                hold_ok = (
                    entry >= plan.confirmation_hold_price
                    if plan.side is Side.LONG
                    else entry <= plan.confirmation_hold_price
                )
                if not hold_ok:
                    counters["failed_confirmation_hold"] += 1
                    rejections.append(
                        ExecutionRejection(
                            scenario_id=plan.scenario_id,
                            response=plan.response,
                            signal_time_ns=plan.signal_time_ns,
                            entry_time_ns=bar.start_time_ns,
                            reason="FAILED_CONFIRMATION_HOLD",
                            entry=entry,
                            stop=plan.stop_price,
                            target=plan.target_price,
                            price_risk_fraction=None,
                            net_reward_risk=None,
                        ),
                    )
                    continue
                geometry = (
                    plan.stop_price < entry < plan.target_price
                    if plan.side is Side.LONG
                    else plan.target_price < entry < plan.stop_price
                )
                if not geometry:
                    counters["invalid_geometry"] += 1
                    rejections.append(
                        ExecutionRejection(
                            scenario_id=plan.scenario_id,
                            response=plan.response,
                            signal_time_ns=plan.signal_time_ns,
                            entry_time_ns=bar.start_time_ns,
                            reason="INVALID_DELAYED_GEOMETRY",
                            entry=entry,
                            stop=plan.stop_price,
                            target=plan.target_price,
                            price_risk_fraction=None,
                            net_reward_risk=None,
                        ),
                    )
                    continue
                price_risk = abs(entry - plan.stop_price)
                planned_loss = price_risk + entry * cost + plan.stop_price * cost
                planned_gain = abs(plan.target_price - entry) - entry * cost - plan.target_price * cost
                price_fraction = price_risk / planned_loss if planned_loss > 0.0 else 0.0
                net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
                if price_fraction < MINIMUM_PRICE_RISK_FRACTION:
                    counters["cost_dominated"] += 1
                    reason = "COST_DOMINATED"
                elif planned_gain <= 0.0 or net_rr < MINIMUM_NET_REWARD_RISK:
                    counters["insufficient_net_reward_risk"] += 1
                    reason = "INSUFFICIENT_NET_REWARD_RISK"
                else:
                    viable.append((net_rr, plan, entry, planned_loss, price_fraction, net_rr))
                    continue
                rejections.append(
                    ExecutionRejection(
                        scenario_id=plan.scenario_id,
                        response=plan.response,
                        signal_time_ns=plan.signal_time_ns,
                        entry_time_ns=bar.start_time_ns,
                        reason=reason,
                        entry=entry,
                        stop=plan.stop_price,
                        target=plan.target_price,
                        price_risk_fraction=price_fraction,
                        net_reward_risk=net_rr,
                    ),
                )
            if viable:
                _, plan, entry, planned_loss, price_fraction, net_rr = sorted(
                    viable,
                    key=lambda row: (-row[0], row[1].scenario_id),
                )[0]
                quantity = nav * RISK_RATE / planned_loss
                active = ActivePosition(
                    plan=plan,
                    entry_time_ns=bar.start_time_ns,
                    entry=entry,
                    quantity=quantity,
                    entry_nav=nav,
                    planned_loss_per_unit=planned_loss,
                    price_risk_fraction=price_fraction,
                    net_reward_risk=net_rr,
                )
                counters["entries"] += 1
                counters["occupied_competing_plans"] += max(len(viable) - 1, 0)
                # Entry occurs at this event bar's first trade. Its completed
                # high/low must therefore participate in execution. Resolve a
                # bar touching both stop and target conservatively stop-first.
                adverse = bar.low if active.plan.side is Side.LONG else bar.high
                favorable = bar.high if active.plan.side is Side.LONG else bar.low
                active.minimum_mark_r = min(active.minimum_mark_r, mark_r(active, adverse, cost))
                active.maximum_mark_r = max(active.maximum_mark_r, mark_r(active, favorable, cost))
                if active.plan.side is Side.LONG:
                    entry_stop_hit = bar.low <= active.plan.stop_price
                    entry_target_hit = bar.high >= active.plan.target_price
                else:
                    entry_stop_hit = bar.high >= active.plan.stop_price
                    entry_target_hit = bar.low <= active.plan.target_price
                if entry_stop_hit or entry_target_hit:
                    if entry_stop_hit and entry_target_hit:
                        counters["entry_bar_stop_first"] += 1
                    entry_exit_price = (
                        active.plan.stop_price
                        if entry_stop_hit
                        else active.plan.target_price
                    )
                    entry_exit_reason = "STOP" if entry_stop_hit else "TARGET"
                    closed = close_position(
                        active,
                        exit_time_ns=bar.end_time_ns,
                        exit_price=entry_exit_price,
                        reason=entry_exit_reason,
                        cost=cost,
                    )
                    nav = closed.exit_nav
                    trades.append(closed)
                    active = None
        elif pending:
            counters["occupied_plans"] += len(pending)
        pending = []

        new_plans = schedules.get(index, [])
        if active is None:
            pending = list(new_plans)
        else:
            counters["occupied_plans"] += len(new_plans)

        mark_nav = nav
        if active is not None:
            unrealized = active.quantity * net_per_unit(
                active.plan.side,
                active.entry,
                bar.close,
                cost,
            )
            mark_nav = active.entry_nav + unrealized
            nominal = active.quantity * bar.close
            maintenance = nominal * MAINTENANCE_MARGIN_RATE
            if maintenance > 0.0:
                ratio = mark_nav / maintenance
                minimum_margin_ratio = ratio if minimum_margin_ratio is None else min(minimum_margin_ratio, ratio)
        high_water = max(high_water, mark_nav)
        if high_water > 0.0:
            max_drawdown = min(max_drawdown, mark_nav / high_water - 1.0)
        day = datetime.fromtimestamp(bar.end_time_ns / 1_000_000_000, tz=timezone.utc).date().isoformat()
        if current_day is None:
            current_day = day
        elif day != current_day:
            daily_rows.append({"date": current_day, "nav": current_day_nav})
            current_day = day
        current_day_nav = mark_nav

    if active is not None:
        final_feature = next(
            feature for feature in reversed(features) if feature.bar.end_time_ns < evaluation_end_ns
        )
        closed = close_position(
            active,
            exit_time_ns=final_feature.bar.end_time_ns,
            exit_price=final_feature.bar.close,
            reason="EVALUATION_END",
            cost=cost,
        )
        nav = closed.exit_nav
        trades.append(closed)
    if current_day is not None:
        daily_rows.append({"date": current_day, "nav": nav})

    trade_frame = pd.DataFrame(asdict(item) for item in trades)
    rejection_frame = pd.DataFrame(asdict(item) for item in rejections)
    realized = pd.to_numeric(trade_frame.get("realized_r", pd.Series(dtype=float)), errors="coerce").dropna()
    gains = float(realized[realized > 0.0].sum())
    losses = abs(float(realized[realized < 0.0].sum()))
    days = max((evaluation_end_ns - evaluation_start_ns) / 1_000_000_000 / 86_400.0, 1.0)
    geo = (nav / starting_nav) ** (1.0 / days) - 1.0 if nav > 0.0 else -1.0
    metrics = {
        "calendar_days": days,
        "plans": len(plans),
        "trades": int(len(realized)),
        "trades_per_day": len(realized) / days,
        "win_rate": float((realized > 0.0).mean()) if len(realized) else None,
        "sum_realized_r": float(realized.sum()) if len(realized) else 0.0,
        "mean_realized_r": float(realized.mean()) if len(realized) else None,
        "profit_factor_r": gains / losses if losses > 0.0 else None,
        "exit_counts": trade_frame.get("exit_reason", pd.Series(dtype=str)).value_counts().to_dict(),
        "start_nav": starting_nav,
        "final_nav": nav,
        "total_return": nav / starting_nav - 1.0,
        "geometric_mean_daily_return": geo,
        "max_drawdown": max_drawdown,
        "minimum_equity_to_maintenance_margin": minimum_margin_ratio,
        "target_met": geo >= 0.01,
        "counters": dict(counters),
    }
    return trade_frame, metrics, pd.DataFrame(daily_rows), rejection_frame


def build_features(bars: list[VolumeBar]) -> tuple[list[EventFeature], ImpactRegimeDetector]:
    detector = ImpactRegimeDetector()
    for bar in bars:
        detector.on_bar(bar)
    return detector.features, detector


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    evaluation_start = parse_utc_date(str(research["discovery_week"]))
    evaluation_end = evaluation_start + timedelta(days=7)
    warmup_start = evaluation_start - timedelta(days=1)
    warmup_ns = int(pd.Timestamp(warmup_start).as_unit("ns").value)
    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)

    records = download_aggtrade_days(
        symbol="BTCUSDT",
        start=warmup_start,
        end=evaluation_end,
        cache_dir=args.cache,
        workers=args.workers,
    )
    warmup_minutes = minute_quote_totals(
        iter_downloads(records),
        start_ns=warmup_ns,
        end_ns=start_ns,
    )
    target_quote = calibrate_target_from_minutes(
        warmup_minutes,
        minutes_per_event=CLOCK_CALIBRATION_MINUTES,
    )
    bars = list(
        iter_volume_bars(
            iter_downloads(records),
            target_quote_notional=target_quote,
            include_partial=False,
        ),
    )
    features, detector = build_features(bars)
    variants = {
        "impact-continuation": detector.continuation_plans,
        "impact-exhaustion": detector.exhaustion_plans,
        "impact-combined": sorted(
            [*detector.continuation_plans, *detector.exhaustion_plans],
            key=lambda plan: (plan.signal_bar_index, plan.scenario_id),
        ),
    }
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(asdict(item) for item in detector.pulse_events).to_csv(
        output / "pulse_events.csv",
        index=False,
    )
    results: dict[str, Any] = {}
    for label, plans in variants.items():
        trades, metrics, daily, rejections = simulate(
            features=features,
            plans=plans,
            evaluation_start_ns=start_ns,
            evaluation_end_ns=end_ns,
            starting_nav=float(execution["starting_nav"]),
            cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
        )
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        trades.to_csv(destination / "trades.csv", index=False)
        daily.to_csv(destination / "daily_nav.csv", index=False)
        rejections.to_csv(destination / "rejections.csv", index=False)
        atomic_json(destination / "metrics.json", metrics)
        results[label] = metrics

    evaluation_bars = [bar for bar in bars if start_ns <= bar.end_time_ns < end_ns]
    payload = {
        "candidate": "aggregate-trade impact-regime state machine",
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "warmup_start_utc": warmup_start.isoformat(),
        "clock_calibration_minutes": CLOCK_CALIBRATION_MINUTES,
        "target_quote_notional": target_quote,
        "evaluation_event_bars": len(evaluation_bars),
        "event_bars_per_day": len(evaluation_bars) / 7.0,
        "risk_fraction": RISK_RATE,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "detector_parameters": {
            "flow_history": FLOW_HISTORY,
            "atr_history": ATR_HISTORY,
            "structure_bars": STRUCTURE_BARS,
            "pulse_bars": PULSE_BARS,
            "pulse_threshold": PULSE_THRESHOLD,
            "exhaustion_confirmation_bars": EXHAUSTION_CONFIRM_BARS,
            "max_hold_bars": MAX_HOLD_BARS,
        },
        "detector_counts": dict(detector.counts),
        "results": results,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "impact_regime_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-aggtrades",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-impact-regime",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
