#!/usr/bin/env python3
"""Causal failed-sweep MSS absorption/reacceleration BTC weekly candidate.

This candidate preserves the cost-resolved intrinsic-time failed-sweep and MSS
logic which removed the v21 control loss, and preserves the unconsumed completed
prior-day/prior-week liquidity hierarchy which repaired the v22 target geometry.
It changes one scenario component: an MSS is no longer an entry by itself.

After a failed sweep closes through the nearest opposing confirmed intrinsic
pivot, the market must show an equal-notional counterflow event whose aggressive
flow is opposite the trade direction but whose close does not move against the
trade.  That is observable effort without adverse result.  A later aligned-flow
close through the absorbed event extreme confirms reacceleration.  The plan is
then submitted on the first subsequent official venue trade.

The invalidation is the complete failed-sweep-to-reacceleration path extreme
plus one 7-bp side-cost buffer.  The target is the nearest still-unconsumed
completed prior-day/prior-week level beyond the deeper local intrinsic pivot.
No PnL-tuned retracement, model score, discretionary leverage multiplier or
custom execution simulator is used.  Orders, fills, commissions, margin,
positions and NAV are exclusively NautilusTrader-owned.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Literal

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
from directional_change_failed_sweep_week import (  # noqa: E402
    DIRECTIONAL_CHANGE_FRACTION,
    MAXIMUM_HOLD_NS,
    DirectionalChangeDetector,
    DirectionalChangeEvent,
)
from impact_regime_probe import EventFeature, ImpactRegimeDetector, ScenarioPlan  # noqa: E402
from intrinsic_external_liquidity_v2_daily_week import ROUND_TRIP_COST_BPS  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402


RULES = ("absorption-reacceleration", "immediate-mss-control")
CONTEXT_DAYS = 14
FLUSH_TICKS = 3
NS_PER_DAY = 86_400_000_000_000
STOP_BUFFER_FRACTION = 7.0 / 10_000.0


@dataclass(slots=True)
class PeriodRange:
    key: str
    start_time_ns: int
    high: float
    low: float
    bars: int = 1

    def update(self, feature: EventFeature) -> None:
        self.high = max(self.high, float(feature.bar.high))
        self.low = min(self.low, float(feature.bar.low))
        self.bars += 1


@dataclass(slots=True)
class CalendarPool:
    pool_id: str
    period: Literal["DAY", "WEEK"]
    edge: Literal["HIGH", "LOW"]
    period_key: str
    level: float
    activated_time_ns: int
    consumed: bool = False
    consumed_time_ns: int | None = None


class CalendarPoolBook:
    """Completed UTC day/week levels with causal activation and consumption."""

    def __init__(self) -> None:
        self.current_day_key: str | None = None
        self.current_week_key: str | None = None
        self.day_builder: PeriodRange | None = None
        self.week_builder: PeriodRange | None = None
        self.pools: list[CalendarPool] = []
        self._by_id: dict[str, CalendarPool] = {}

    @staticmethod
    def _keys(ts_ns: int) -> tuple[str, str]:
        observed = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        day_key = observed.date().isoformat()
        monday = observed.date() - timedelta(days=observed.weekday())
        return day_key, monday.isoformat()

    def _activate(self, period: Literal["DAY", "WEEK"], row: PeriodRange, ts_ns: int) -> None:
        for edge, level in (("HIGH", row.high), ("LOW", row.low)):
            pool_id = f"{period}:{row.key}:{edge}"
            if pool_id in self._by_id:
                raise RuntimeError(f"duplicate calendar pool {pool_id}")
            pool = CalendarPool(
                pool_id=pool_id,
                period=period,
                edge=edge,
                period_key=row.key,
                level=float(level),
                activated_time_ns=ts_ns,
            )
            self.pools.append(pool)
            self._by_id[pool_id] = pool

    def on_feature(self, feature: EventFeature) -> None:
        bar = feature.bar
        ts_ns = int(bar.end_time_ns)
        day_key, week_key = self._keys(ts_ns)

        if self.current_day_key is None:
            self.current_day_key = day_key
            self.day_builder = PeriodRange(day_key, ts_ns, float(bar.high), float(bar.low))
        elif day_key != self.current_day_key:
            assert self.day_builder is not None
            self._activate("DAY", self.day_builder, ts_ns)
            self.current_day_key = day_key
            self.day_builder = PeriodRange(day_key, ts_ns, float(bar.high), float(bar.low))
        else:
            assert self.day_builder is not None
            self.day_builder.update(feature)

        if self.current_week_key is None:
            self.current_week_key = week_key
            self.week_builder = PeriodRange(week_key, ts_ns, float(bar.high), float(bar.low))
        elif week_key != self.current_week_key:
            assert self.week_builder is not None
            self._activate("WEEK", self.week_builder, ts_ns)
            self.current_week_key = week_key
            self.week_builder = PeriodRange(week_key, ts_ns, float(bar.high), float(bar.low))
        else:
            assert self.week_builder is not None
            self.week_builder.update(feature)

        for pool in self.pools:
            if pool.consumed or pool.activated_time_ns > ts_ns:
                continue
            touched = (
                float(bar.high) >= pool.level
                if pool.edge == "HIGH"
                else float(bar.low) <= pool.level
            )
            if touched:
                pool.consumed = True
                pool.consumed_time_ns = ts_ns

    def nearest_external_target(
        self,
        *,
        side: Side,
        current_price: float,
        beyond_local_level: float,
    ) -> CalendarPool | None:
        if side is Side.LONG:
            floor = max(float(current_price), float(beyond_local_level))
            values = [
                pool
                for pool in self.pools
                if not pool.consumed and pool.edge == "HIGH" and pool.level > floor
            ]
            return min(values, key=lambda item: (item.level, item.activated_time_ns), default=None)
        ceiling = min(float(current_price), float(beyond_local_level))
        values = [
            pool
            for pool in self.pools
            if not pool.consumed and pool.edge == "LOW" and pool.level < ceiling
        ]
        return max(values, key=lambda item: (item.level, -item.activated_time_ns), default=None)

    def is_consumed(self, pool_id: str) -> bool:
        pool = self._by_id.get(pool_id)
        return True if pool is None else bool(pool.consumed)


@dataclass(slots=True)
class FailedSweepSetup:
    scenario_id: str
    side: Side
    created_index: int
    created_time_ns: int
    expiry_index: int
    boundary: float
    sweep_extreme: float
    mss_level: float
    local_external_level: float
    path_high: float
    path_low: float
    trend_flow_imbalance: float
    reversal_flow_imbalance: float


@dataclass(slots=True)
class PostMSSSetup:
    scenario_id: str
    side: Side
    mss_index: int
    mss_time_ns: int
    expiry_index: int
    boundary: float
    mss_level: float
    local_external_level: float
    target_pool_id: str
    target_price: float
    path_high: float
    path_low: float
    trend_flow_imbalance: float
    reversal_flow_imbalance: float
    absorption_index: int | None = None
    absorption_time_ns: int | None = None
    absorption_high: float | None = None
    absorption_low: float | None = None
    absorption_events: int = 0


@dataclass(frozen=True, slots=True)
class ScenarioTransition:
    scenario_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    side: str
    boundary: float
    mss_level: float
    local_external_level: float
    target_pool_id: str | None
    target_price: float | None
    path_high: float
    path_low: float
    imbalance_z: float | None
    aligned_close_change: float | None
    close: float


class MSSAbsorptionStateMachine:
    """Failed sweep -> MSS -> absorbed counterflow -> reacceleration."""

    def __init__(self, *, rule: str, calendar: CalendarPoolBook) -> None:
        if rule not in RULES:
            raise ValueError(f"unknown rule {rule}")
        self.rule = rule
        self.calendar = calendar
        self.detector = DirectionalChangeDetector(
            threshold_fraction=DIRECTIONAL_CHANGE_FRACTION,
        )
        self.high_events: list[DirectionalChangeEvent] = []
        self.low_events: list[DirectionalChangeEvent] = []
        self.failed: list[FailedSweepSetup] = []
        self.post_mss: list[PostMSSSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[ScenarioTransition] = []
        self.counts: Counter[str] = Counter()

    def _transition(
        self,
        *,
        setup: FailedSweepSetup | PostMSSSetup,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
        target_pool_id: str | None = None,
        target_price: float | None = None,
        aligned_close_change: float | None = None,
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
                mss_level=float(setup.mss_level),
                local_external_level=float(setup.local_external_level),
                target_pool_id=target_pool_id,
                target_price=target_price,
                path_high=float(setup.path_high),
                path_low=float(setup.path_low),
                imbalance_z=(
                    float(feature.imbalance_z)
                    if feature.imbalance_z is not None
                    else None
                ),
                aligned_close_change=aligned_close_change,
                close=float(feature.bar.close),
            ),
        )

    @staticmethod
    def _aligned_z(side: Side, feature: EventFeature) -> float | None:
        return (
            side.sign * float(feature.imbalance_z)
            if feature.imbalance_z is not None
            else None
        )

    @staticmethod
    def _mss_confirmed(setup: FailedSweepSetup, feature: EventFeature) -> bool:
        aligned = MSSAbsorptionStateMachine._aligned_z(setup.side, feature)
        if aligned is None or aligned <= 0.0:
            return False
        return (
            float(feature.bar.close) > setup.mss_level
            if setup.side is Side.LONG
            else float(feature.bar.close) < setup.mss_level
        )

    @staticmethod
    def _failed_invalidated(setup: FailedSweepSetup, feature: EventFeature) -> bool:
        return (
            float(feature.bar.close) < setup.sweep_extreme
            if setup.side is Side.LONG
            else float(feature.bar.close) > setup.sweep_extreme
        )

    def _emit_plan(
        self,
        *,
        setup: PostMSSSetup,
        feature: EventFeature,
        index: int,
        reason_code: str,
    ) -> ScenarioPlan:
        side = setup.side
        stop = (
            setup.path_low * (1.0 - STOP_BUFFER_FRACTION)
            if side is Side.LONG
            else setup.path_high * (1.0 + STOP_BUFFER_FRACTION)
        )
        plan = ScenarioPlan(
            scenario_id=setup.scenario_id + f":entry:{index}",
            response="EXHAUSTION_REVERSAL",
            side=side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=float(stop),
            target_price=float(setup.target_price),
            confirmation_hold_price=float(setup.mss_level),
            structure_high=max(setup.path_high, setup.target_price, setup.boundary),
            structure_low=min(setup.path_low, setup.target_price, setup.boundary),
            structure_midpoint=0.5 * (setup.boundary + setup.local_external_level),
            pulse_high=float(setup.path_high),
            pulse_low=float(setup.path_low),
            pulse_flow_score=float(setup.trend_flow_imbalance),
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code=reason_code,
        )
        self.plans.append(plan)
        self.counts["plans_emitted"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="PLAN_EMITTED",
            reason_code=reason_code,
            target_pool_id=setup.target_pool_id,
            target_price=setup.target_price,
        )
        return plan

    def _resolve_mss(
        self,
        *,
        setup: FailedSweepSetup,
        feature: EventFeature,
        index: int,
    ) -> None:
        target = self.calendar.nearest_external_target(
            side=setup.side,
            current_price=float(feature.bar.close),
            beyond_local_level=setup.local_external_level,
        )
        if target is None:
            self.counts["mss_without_external_target"] += 1
            self._transition(
                setup=setup,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="MSS_WITHOUT_UNCONSUMED_CALENDAR_TARGET",
            )
            return
        response_span = max(1, index - setup.created_index)
        post = PostMSSSetup(
            scenario_id=setup.scenario_id + f":mss:{index}",
            side=setup.side,
            mss_index=index,
            mss_time_ns=int(feature.bar.end_time_ns),
            expiry_index=index + response_span,
            boundary=setup.boundary,
            mss_level=setup.mss_level,
            local_external_level=setup.local_external_level,
            target_pool_id=target.pool_id,
            target_price=target.level,
            path_high=max(setup.path_high, float(feature.bar.high)),
            path_low=min(setup.path_low, float(feature.bar.low)),
            trend_flow_imbalance=setup.trend_flow_imbalance,
            reversal_flow_imbalance=setup.reversal_flow_imbalance,
        )
        self.counts["mss_confirmed"] += 1
        self._transition(
            setup=post,
            feature=feature,
            index=index,
            event_type="MSS_CONFIRMED",
            reason_code="FAILED_SWEEP_INTERNAL_PIVOT_DISPLACED",
            target_pool_id=target.pool_id,
            target_price=target.level,
        )
        if self.rule == "immediate-mss-control":
            self._emit_plan(
                setup=post,
                feature=feature,
                index=index,
                reason_code="FAILED_SWEEP_MSS_IMMEDIATE_CALENDAR_TARGET",
            )
        else:
            self.post_mss.append(post)

    def _update_failed(self, *, index: int, feature: EventFeature) -> None:
        remaining: list[FailedSweepSetup] = []
        for setup in self.failed:
            if index <= setup.created_index:
                remaining.append(setup)
                continue
            setup.path_high = max(setup.path_high, float(feature.bar.high))
            setup.path_low = min(setup.path_low, float(feature.bar.low))
            if index > setup.expiry_index:
                self.counts["failed_sweep_mss_expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="FAILED_SWEEP_MSS_RESPONSE_WINDOW_EXPIRED",
                )
                continue
            if self._failed_invalidated(setup, feature):
                self.counts["failed_sweep_reclaimed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="FAILED_SWEEP_EXTREME_RECLAIMED",
                )
                continue
            if self._mss_confirmed(setup, feature):
                self._resolve_mss(setup=setup, feature=feature, index=index)
                continue
            remaining.append(setup)
        self.failed = remaining

    def _update_post_mss(
        self,
        *,
        index: int,
        feature: EventFeature,
        features: list[EventFeature],
    ) -> None:
        remaining: list[PostMSSSetup] = []
        for setup in self.post_mss:
            if index <= setup.mss_index:
                remaining.append(setup)
                continue
            previous_close = float(features[index - 1].bar.close)
            aligned_change = setup.side.sign * (
                float(feature.bar.close) - previous_close
            )
            aligned_z = self._aligned_z(setup.side, feature)
            setup.path_high = max(setup.path_high, float(feature.bar.high))
            setup.path_low = min(setup.path_low, float(feature.bar.low))

            if self.calendar.is_consumed(setup.target_pool_id):
                self.counts["calendar_target_consumed_before_entry"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="CALENDAR_TARGET_CONSUMED_BEFORE_ENTRY",
                    target_pool_id=setup.target_pool_id,
                    target_price=setup.target_price,
                    aligned_close_change=aligned_change,
                )
                continue
            if index > setup.expiry_index:
                self.counts["absorption_window_expired"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="ABSORPTION_REACCELERATION_WINDOW_EXPIRED",
                    target_pool_id=setup.target_pool_id,
                    target_price=setup.target_price,
                    aligned_close_change=aligned_change,
                )
                continue
            hold = (
                float(feature.bar.close) > setup.mss_level
                if setup.side is Side.LONG
                else float(feature.bar.close) < setup.mss_level
            )
            if not hold:
                self.counts["mss_level_lost_before_entry"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="MSS_LEVEL_LOST_BEFORE_ENTRY",
                    target_pool_id=setup.target_pool_id,
                    target_price=setup.target_price,
                    aligned_close_change=aligned_change,
                )
                continue
            buffered_stop = (
                setup.path_low * (1.0 - STOP_BUFFER_FRACTION)
                if setup.side is Side.LONG
                else setup.path_high * (1.0 + STOP_BUFFER_FRACTION)
            )
            stop_touched = (
                float(feature.bar.low) <= buffered_stop
                if setup.side is Side.LONG
                else float(feature.bar.high) >= buffered_stop
            )
            if stop_touched:
                self.counts["path_invalidation_before_entry"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="FAILED_SWEEP_TO_MSS_PATH_INVALIDATED",
                    target_pool_id=setup.target_pool_id,
                    target_price=setup.target_price,
                    aligned_close_change=aligned_change,
                )
                continue

            absorbed = (
                aligned_z is not None
                and aligned_z < 0.0
                and aligned_change >= 0.0
            )
            if absorbed:
                setup.absorption_events += 1
                setup.absorption_index = index
                setup.absorption_time_ns = int(feature.bar.end_time_ns)
                setup.absorption_high = (
                    float(feature.bar.high)
                    if setup.absorption_high is None
                    else max(setup.absorption_high, float(feature.bar.high))
                )
                setup.absorption_low = (
                    float(feature.bar.low)
                    if setup.absorption_low is None
                    else min(setup.absorption_low, float(feature.bar.low))
                )
                self.counts["counterflow_absorbed"] += 1
                self._transition(
                    setup=setup,
                    feature=feature,
                    index=index,
                    event_type="COUNTERFLOW_ABSORBED",
                    reason_code="OPPOSITE_FLOW_WITHOUT_ADVERSE_CLOSE",
                    target_pool_id=setup.target_pool_id,
                    target_price=setup.target_price,
                    aligned_close_change=aligned_change,
                )
                remaining.append(setup)
                continue

            if setup.absorption_index is not None and aligned_z is not None and aligned_z > 0.0:
                assert setup.absorption_high is not None
                assert setup.absorption_low is not None
                reaccelerated = (
                    float(feature.bar.close) > setup.absorption_high
                    if setup.side is Side.LONG
                    else float(feature.bar.close) < setup.absorption_low
                )
                if reaccelerated:
                    self.counts["reacceleration_confirmed"] += 1
                    self._emit_plan(
                        setup=setup,
                        feature=feature,
                        index=index,
                        reason_code="MSS_COUNTERFLOW_ABSORBED_AND_REACCELERATED",
                    )
                    continue
            remaining.append(setup)
        self.post_mss = remaining

    def _arm_from_directional_change(
        self,
        *,
        event: DirectionalChangeEvent,
        feature: EventFeature,
        index: int,
    ) -> None:
        if event.event_type == "DOWN":
            prior_same = self.high_events[-1] if self.high_events else None
            opposing = self.low_events[-2:]
            self.high_events.append(event)
            if prior_same is None or len(opposing) < 2:
                self.counts["insufficient_confirmed_intrinsic_liquidity"] += 1
                return
            sweep = event.pivot_price > prior_same.pivot_price
            reentered = event.confirmation_price < prior_same.pivot_price
            flow_reversed = (
                event.trend_flow_imbalance > 0.0
                and event.reversal_flow_imbalance < 0.0
            )
            side = Side.SHORT
            mss_level = opposing[-1].pivot_price
            local_external = min(row.pivot_price for row in opposing)
            boundary = prior_same.pivot_price
            sweep_extreme = event.path_high
        else:
            prior_same = self.low_events[-1] if self.low_events else None
            opposing = self.high_events[-2:]
            self.low_events.append(event)
            if prior_same is None or len(opposing) < 2:
                self.counts["insufficient_confirmed_intrinsic_liquidity"] += 1
                return
            sweep = event.pivot_price < prior_same.pivot_price
            reentered = event.confirmation_price > prior_same.pivot_price
            flow_reversed = (
                event.trend_flow_imbalance < 0.0
                and event.reversal_flow_imbalance > 0.0
            )
            side = Side.LONG
            mss_level = opposing[-1].pivot_price
            local_external = max(row.pivot_price for row in opposing)
            boundary = prior_same.pivot_price
            sweep_extreme = event.path_low

        if not sweep:
            self.counts["no_same_side_intrinsic_sweep"] += 1
            return
        if not reentered:
            self.counts["swept_value_retained"] += 1
            return
        if not flow_reversed:
            self.counts["failed_sweep_without_flow_reversal"] += 1
            return

        response_span = max(1, event.confirmation_index - event.trend_start_index)
        setup = FailedSweepSetup(
            scenario_id=(
                f"v24-failed-sweep:{event.confirmation_index}:"
                f"{side.value.lower()}:{event.confirmation_time_ns}"
            ),
            side=side,
            created_index=index,
            created_time_ns=int(feature.bar.end_time_ns),
            expiry_index=index + response_span,
            boundary=float(boundary),
            sweep_extreme=float(sweep_extreme),
            mss_level=float(mss_level),
            local_external_level=float(local_external),
            path_high=float(event.path_high),
            path_low=float(event.path_low),
            trend_flow_imbalance=float(event.trend_flow_imbalance),
            reversal_flow_imbalance=float(event.reversal_flow_imbalance),
        )
        self.counts["failed_sweeps_armed"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=index,
            event_type="FAILED_SWEEP_ARMED",
            reason_code="INTRINSIC_SWEEP_FAILED_WITH_FLOW_REVERSAL",
        )
        if self._mss_confirmed(setup, feature):
            self._resolve_mss(setup=setup, feature=feature, index=index)
        else:
            self.failed.append(setup)

    def on_feature(
        self,
        *,
        index: int,
        features: list[EventFeature],
    ) -> None:
        feature = features[index]
        self.calendar.on_feature(feature)
        self._update_failed(index=index, feature=feature)
        self._update_post_mss(index=index, feature=feature, features=features)
        event = self.detector.on_feature(index=index, features=features)
        if event is not None:
            self.counts[f"directional_change_{event.event_type.lower()}"] += 1
            self._arm_from_directional_change(
                event=event,
                feature=feature,
                index=index,
            )


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
    calendar = CalendarPoolBook()
    scenario = MSSAbsorptionStateMachine(rule=args.rule, calendar=calendar)
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
        label=f"BTCUSDT-v24-{args.rule}-{evaluation_start.date().isoformat()}-7d",
        trades=execution_trades,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=output,
    )

    pd.DataFrame(asdict(row) for row in scenario.detector.events).to_csv(
        output / "directional_change_events.csv",
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
    pd.DataFrame(asdict(pool) for pool in calendar.pools).to_csv(
        output / "calendar_pools.csv",
        index=False,
    )
    atomic_json(
        output / "daily_clock_calibrations.json",
        {"calibrations": [row.to_dict() for row in calibrations]},
    )

    payload = {
        "candidate": "failed-sweep MSS counterflow absorption and reacceleration",
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_days": CONTEXT_DAYS,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "directional_change_fraction": DIRECTIONAL_CHANGE_FRACTION,
        "state_counts": dict(scenario.counts),
        "selected_plan_count": len(plans),
        "calendar_pool_count": len(calendar.pools),
        "unconsumed_calendar_pool_count": sum(not row.consumed for row in calendar.pools),
        "official_execution_trade_ticks": len(execution_trades),
        "execution_tick_windows": [list(row) for row in windows],
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "metrics": evidence.metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "mss_absorption_reacceleration_v24_summary.json", payload)
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
        default=ROOT / ".cache" / "candidate-01-v24-absorption",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v24-absorption",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
