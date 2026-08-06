#!/usr/bin/env python3
"""BTC perpetual funding-window liquidity raid and MSS reversal candidate.

The immediately completed Binance-style eight-hour funding window is treated as
one economically meaningful dealing range.  Its high and low become the only
source/target pair for the next funding window.  A source edge must be raided,
then a completed equal-notional event must close back inside with opposite
aggressive-flow sign.  The primary rule waits for a close through the nearest
opposing intrinsic pivot formed in the active funding window, then submits a
market bracket on the first subsequent official venue trade.  The opposite
completed funding-window edge is the target and the full raid-to-MSS path is
the structural invalidation.

The control enters immediately after failed-auction re-entry on the identical
range and event stream.  This isolates whether MSS contributes alpha rather
than merely suppressing trades.  NautilusTrader exclusively owns order
matching, fees, margin, positions, PnL and NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Literal

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from adaptive_aggtrade_clock import build_daily_cost_resolved_bars  # noqa: E402
from aggtrade_data import download_aggtrade_days  # noqa: E402
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
from mss_absorption_reacceleration_v24_nautilus_week import execution_trade_windows  # noqa: E402
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest  # noqa: E402
from resolved_impact_v17_nautilus_week import atomic_json, load_execution  # noqa: E402


RULES = ("funding-window-mss", "failed-auction-control")
CONTEXT_DAYS = 7
STOP_BUFFER_FRACTION = 7.0 / 10_000.0


@dataclass(slots=True)
class FundingRange:
    window_id: str
    start_time_ns: int
    end_time_ns: int
    high: float
    low: float
    bars: int = 1

    def update(self, feature: EventFeature) -> None:
        self.high = max(self.high, float(feature.bar.high))
        self.low = min(self.low, float(feature.bar.low))
        self.bars += 1


class FundingWindowBook:
    """Causal immediately completed 00/08/16 UTC funding-window ranges."""

    def __init__(self) -> None:
        self.current: FundingRange | None = None
        self.reference: FundingRange | None = None
        self.completed: list[FundingRange] = []

    @staticmethod
    def _window(ts_ns: int) -> tuple[str, int, int]:
        observed = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        hour = (observed.hour // 8) * 8
        start = observed.replace(hour=hour, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=8)
        return (
            start.isoformat(),
            int(start.timestamp() * 1_000_000_000),
            int(end.timestamp() * 1_000_000_000),
        )

    def on_feature(self, feature: EventFeature) -> bool:
        window_id, start_ns, end_ns = self._window(int(feature.bar.end_time_ns))
        if self.current is None:
            self.current = FundingRange(
                window_id,
                start_ns,
                end_ns,
                float(feature.bar.high),
                float(feature.bar.low),
            )
            return True
        if window_id != self.current.window_id:
            self.completed.append(self.current)
            self.reference = self.current
            self.current = FundingRange(
                window_id,
                start_ns,
                end_ns,
                float(feature.bar.high),
                float(feature.bar.low),
            )
            return True
        self.current.update(feature)
        return False


@dataclass(slots=True)
class RaidObservation:
    observation_id: str
    window_id: str
    side: Side
    created_index: int
    created_time_ns: int
    window_start_ns: int
    window_end_ns: int
    boundary: float
    target: float
    path_high: float
    path_low: float
    reentered_index: int | None = None
    reentered_time_ns: int | None = None
    reentry_imbalance_z: float | None = None


@dataclass(slots=True)
class FailedAuctionSetup:
    scenario_id: str
    window_id: str
    side: Side
    created_index: int
    created_time_ns: int
    expiry_time_ns: int
    boundary: float
    target: float
    mss_level: float
    path_high: float
    path_low: float
    reentry_imbalance_z: float


@dataclass(frozen=True, slots=True)
class FundingTransition:
    scenario_id: str
    window_id: str
    event_type: str
    event_index: int
    event_time_ns: int
    reason_code: str
    side: str
    boundary: float
    target: float
    mss_level: float | None
    path_high: float
    path_low: float
    imbalance_z: float | None
    close: float


class FundingFailedAuctionStateMachine:
    def __init__(self, *, rule: str, book: FundingWindowBook) -> None:
        if rule not in RULES:
            raise ValueError(rule)
        self.rule = rule
        self.book = book
        self.detector = DirectionalChangeDetector(
            threshold_fraction=DIRECTIONAL_CHANGE_FRACTION,
        )
        self.high_events: list[DirectionalChangeEvent] = []
        self.low_events: list[DirectionalChangeEvent] = []
        self.high_observation: RaidObservation | None = None
        self.low_observation: RaidObservation | None = None
        self.setups: list[FailedAuctionSetup] = []
        self.plans: list[ScenarioPlan] = []
        self.transitions: list[FundingTransition] = []
        self.counts: Counter[str] = Counter()
        self.high_target_consumed = False
        self.low_target_consumed = False
        self.high_source_used = False
        self.low_source_used = False

    def _transition(
        self,
        *,
        scenario_id: str,
        window_id: str,
        feature: EventFeature,
        index: int,
        event_type: str,
        reason_code: str,
        side: Side,
        boundary: float,
        target: float,
        path_high: float,
        path_low: float,
        mss_level: float | None = None,
    ) -> None:
        self.transitions.append(
            FundingTransition(
                scenario_id=scenario_id,
                window_id=window_id,
                event_type=event_type,
                event_index=index,
                event_time_ns=int(feature.bar.end_time_ns),
                reason_code=reason_code,
                side=side.value,
                boundary=float(boundary),
                target=float(target),
                mss_level=(float(mss_level) if mss_level is not None else None),
                path_high=float(path_high),
                path_low=float(path_low),
                imbalance_z=(
                    float(feature.imbalance_z)
                    if feature.imbalance_z is not None
                    else None
                ),
                close=float(feature.bar.close),
            ),
        )

    def _reset_window(self, *, feature: EventFeature, index: int) -> None:
        for observation in (self.high_observation, self.low_observation):
            if observation is not None:
                self.counts["raid_observation_window_expired"] += 1
                self._transition(
                    scenario_id=observation.observation_id,
                    window_id=observation.window_id,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="FUNDING_WINDOW_ENDED_BEFORE_ENTRY",
                    side=observation.side,
                    boundary=observation.boundary,
                    target=observation.target,
                    path_high=observation.path_high,
                    path_low=observation.path_low,
                )
        for setup in self.setups:
            self.counts["mss_setup_window_expired"] += 1
            self._transition(
                scenario_id=setup.scenario_id,
                window_id=setup.window_id,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="FUNDING_WINDOW_ENDED_BEFORE_MSS",
                side=setup.side,
                boundary=setup.boundary,
                target=setup.target,
                mss_level=setup.mss_level,
                path_high=setup.path_high,
                path_low=setup.path_low,
            )
        self.high_observation = None
        self.low_observation = None
        self.setups = []
        self.high_target_consumed = False
        self.low_target_consumed = False
        self.high_source_used = False
        self.low_source_used = False

    @staticmethod
    def _aligned_z(side: Side, feature: EventFeature) -> float | None:
        return (
            side.sign * float(feature.imbalance_z)
            if feature.imbalance_z is not None
            else None
        )

    def _append_directional_event(self, event: DirectionalChangeEvent | None) -> None:
        if event is None:
            return
        if event.event_type == "DOWN":
            self.high_events.append(event)
        else:
            self.low_events.append(event)
        self.counts[f"directional_change_{event.event_type.lower()}"] += 1

    def _nearest_current_window_pivot(
        self,
        *,
        side: Side,
        boundary: float,
        window_start_ns: int,
    ) -> float | None:
        if side is Side.SHORT:
            candidates = [
                event.pivot_price
                for event in self.low_events
                if event.confirmation_time_ns >= window_start_ns
                and event.pivot_price < boundary
            ]
            return candidates[-1] if candidates else None
        candidates = [
            event.pivot_price
            for event in self.high_events
            if event.confirmation_time_ns >= window_start_ns
            and event.pivot_price > boundary
        ]
        return candidates[-1] if candidates else None

    def _emit_control(
        self,
        *,
        observation: RaidObservation,
        feature: EventFeature,
        index: int,
    ) -> None:
        stop = (
            observation.path_low * (1.0 - STOP_BUFFER_FRACTION)
            if observation.side is Side.LONG
            else observation.path_high * (1.0 + STOP_BUFFER_FRACTION)
        )
        plan = ScenarioPlan(
            scenario_id=observation.observation_id + f":control:{index}",
            response="EXHAUSTION_REVERSAL",
            side=observation.side,
            signal_bar_index=index,
            signal_time_ns=int(feature.bar.end_time_ns),
            stop_price=float(stop),
            target_price=float(observation.target),
            confirmation_hold_price=float(observation.boundary),
            structure_high=max(observation.path_high, observation.boundary, observation.target),
            structure_low=min(observation.path_low, observation.boundary, observation.target),
            structure_midpoint=0.5 * (observation.boundary + observation.target),
            pulse_high=float(observation.path_high),
            pulse_low=float(observation.path_low),
            pulse_flow_score=float(observation.reentry_imbalance_z or 0.0),
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code="FUNDING_EDGE_RAID_FAILED_AUCTION_CONTROL",
        )
        self.plans.append(plan)
        self.counts["control_plans_emitted"] += 1
        self._transition(
            scenario_id=plan.scenario_id,
            window_id=observation.window_id,
            feature=feature,
            index=index,
            event_type="PLAN_EMITTED",
            reason_code=plan.reason_code,
            side=plan.side,
            boundary=observation.boundary,
            target=observation.target,
            path_high=observation.path_high,
            path_low=observation.path_low,
        )

    def _arm_mss(
        self,
        *,
        observation: RaidObservation,
        feature: EventFeature,
        index: int,
    ) -> bool:
        mss = self._nearest_current_window_pivot(
            side=observation.side,
            boundary=observation.boundary,
            window_start_ns=observation.window_start_ns,
        )
        if mss is None:
            self.counts["reentry_waiting_for_current_window_pivot"] += 1
            return False
        setup = FailedAuctionSetup(
            scenario_id=observation.observation_id + f":mss:{index}",
            window_id=observation.window_id,
            side=observation.side,
            created_index=index,
            created_time_ns=int(feature.bar.end_time_ns),
            expiry_time_ns=observation.window_end_ns,
            boundary=observation.boundary,
            target=observation.target,
            mss_level=float(mss),
            path_high=observation.path_high,
            path_low=observation.path_low,
            reentry_imbalance_z=float(observation.reentry_imbalance_z or 0.0),
        )
        self.setups.append(setup)
        self.counts["mss_setups_armed"] += 1
        self._transition(
            scenario_id=setup.scenario_id,
            window_id=setup.window_id,
            feature=feature,
            index=index,
            event_type="MSS_SETUP_ARMED",
            reason_code="FUNDING_EDGE_RAID_REENTERED_WITH_OPPOSITE_FLOW",
            side=setup.side,
            boundary=setup.boundary,
            target=setup.target,
            mss_level=setup.mss_level,
            path_high=setup.path_high,
            path_low=setup.path_low,
        )
        return True

    def _process_observation(
        self,
        observation: RaidObservation,
        *,
        feature: EventFeature,
        index: int,
        target_consumed: bool,
    ) -> tuple[RaidObservation | None, bool]:
        observation.path_high = max(observation.path_high, float(feature.bar.high))
        observation.path_low = min(observation.path_low, float(feature.bar.low))
        if target_consumed:
            self.counts["funding_target_consumed_before_entry"] += 1
            self._transition(
                scenario_id=observation.observation_id,
                window_id=observation.window_id,
                feature=feature,
                index=index,
                event_type="INVALIDATED",
                reason_code="OPPOSITE_FUNDING_EDGE_CONSUMED_BEFORE_ENTRY",
                side=observation.side,
                boundary=observation.boundary,
                target=observation.target,
                path_high=observation.path_high,
                path_low=observation.path_low,
            )
            return None, True

        inside = (
            float(feature.bar.close) > observation.boundary
            if observation.side is Side.LONG
            else float(feature.bar.close) < observation.boundary
        )
        aligned_z = self._aligned_z(observation.side, feature)
        if observation.reentered_index is None:
            if inside and aligned_z is not None and aligned_z > 0.0:
                observation.reentered_index = index
                observation.reentered_time_ns = int(feature.bar.end_time_ns)
                observation.reentry_imbalance_z = float(feature.imbalance_z)
                self.counts["failed_auction_reentries"] += 1
                self._transition(
                    scenario_id=observation.observation_id,
                    window_id=observation.window_id,
                    feature=feature,
                    index=index,
                    event_type="FAILED_AUCTION_REENTERED",
                    reason_code="FUNDING_EDGE_RAID_CLOSED_BACK_INSIDE_WITH_OPPOSITE_FLOW",
                    side=observation.side,
                    boundary=observation.boundary,
                    target=observation.target,
                    path_high=observation.path_high,
                    path_low=observation.path_low,
                )
                if self.rule == "failed-auction-control":
                    self._emit_control(
                        observation=observation,
                        feature=feature,
                        index=index,
                    )
                    return None, True
        else:
            if not inside:
                self.counts["failed_auction_reentry_lost"] += 1
                self._transition(
                    scenario_id=observation.observation_id,
                    window_id=observation.window_id,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="FUNDING_EDGE_VALUE_REACCEPTED_OUTSIDE",
                    side=observation.side,
                    boundary=observation.boundary,
                    target=observation.target,
                    path_high=observation.path_high,
                    path_low=observation.path_low,
                )
                return None, True
        if observation.reentered_index is not None and self.rule == "funding-window-mss":
            if self._arm_mss(observation=observation, feature=feature, index=index):
                return None, True
        return observation, False

    def _update_setups(self, *, feature: EventFeature, index: int) -> None:
        remaining: list[FailedAuctionSetup] = []
        for setup in self.setups:
            setup.path_high = max(setup.path_high, float(feature.bar.high))
            setup.path_low = min(setup.path_low, float(feature.bar.low))
            if int(feature.bar.end_time_ns) >= setup.expiry_time_ns:
                self.counts["mss_setup_window_expired"] += 1
                continue
            target_consumed = (
                self.low_target_consumed
                if setup.side is Side.SHORT
                else self.high_target_consumed
            )
            if target_consumed:
                self.counts["funding_target_consumed_before_mss"] += 1
                self._transition(
                    scenario_id=setup.scenario_id,
                    window_id=setup.window_id,
                    feature=feature,
                    index=index,
                    event_type="INVALIDATED",
                    reason_code="OPPOSITE_FUNDING_EDGE_CONSUMED_BEFORE_MSS",
                    side=setup.side,
                    boundary=setup.boundary,
                    target=setup.target,
                    mss_level=setup.mss_level,
                    path_high=setup.path_high,
                    path_low=setup.path_low,
                )
                continue
            inside = (
                float(feature.bar.close) > setup.boundary
                if setup.side is Side.LONG
                else float(feature.bar.close) < setup.boundary
            )
            if not inside:
                self.counts["failed_auction_reentry_lost_before_mss"] += 1
                continue
            aligned_z = self._aligned_z(setup.side, feature)
            mss = (
                float(feature.bar.close) > setup.mss_level
                if setup.side is Side.LONG
                else float(feature.bar.close) < setup.mss_level
            )
            if mss and aligned_z is not None and aligned_z > 0.0:
                stop = (
                    setup.path_low * (1.0 - STOP_BUFFER_FRACTION)
                    if setup.side is Side.LONG
                    else setup.path_high * (1.0 + STOP_BUFFER_FRACTION)
                )
                plan = ScenarioPlan(
                    scenario_id=setup.scenario_id + f":entry:{index}",
                    response="EXHAUSTION_REVERSAL",
                    side=setup.side,
                    signal_bar_index=index,
                    signal_time_ns=int(feature.bar.end_time_ns),
                    stop_price=float(stop),
                    target_price=float(setup.target),
                    confirmation_hold_price=float(setup.boundary),
                    structure_high=max(setup.path_high, setup.boundary, setup.target),
                    structure_low=min(setup.path_low, setup.boundary, setup.target),
                    structure_midpoint=0.5 * (setup.boundary + setup.target),
                    pulse_high=float(setup.path_high),
                    pulse_low=float(setup.path_low),
                    pulse_flow_score=float(setup.reentry_imbalance_z),
                    pulse_move_atr=0.0,
                    pulse_path_efficiency=0.0,
                    pulse_close_location=0.0,
                    reason_code="FUNDING_EDGE_FAILED_AUCTION_AND_CURRENT_WINDOW_MSS",
                )
                self.plans.append(plan)
                self.counts["mss_plans_emitted"] += 1
                self._transition(
                    scenario_id=plan.scenario_id,
                    window_id=setup.window_id,
                    feature=feature,
                    index=index,
                    event_type="PLAN_EMITTED",
                    reason_code=plan.reason_code,
                    side=plan.side,
                    boundary=setup.boundary,
                    target=setup.target,
                    mss_level=setup.mss_level,
                    path_high=setup.path_high,
                    path_low=setup.path_low,
                )
                continue
            remaining.append(setup)
        self.setups = remaining

    def on_feature(
        self,
        *,
        index: int,
        features: list[EventFeature],
    ) -> None:
        feature = features[index]
        changed = self.book.on_feature(feature)
        if changed:
            self._reset_window(feature=feature, index=index)

        event = self.detector.on_feature(index=index, features=features)
        self._append_directional_event(event)
        reference = self.book.reference
        current = self.book.current
        if reference is None or current is None:
            return

        touch_high = float(feature.bar.high) >= reference.high
        touch_low = float(feature.bar.low) <= reference.low
        self.high_target_consumed = self.high_target_consumed or touch_high
        self.low_target_consumed = self.low_target_consumed or touch_low

        self._update_setups(feature=feature, index=index)

        if self.high_observation is not None:
            self.high_observation, _ = self._process_observation(
                self.high_observation,
                feature=feature,
                index=index,
                target_consumed=self.low_target_consumed,
            )
        if self.low_observation is not None:
            self.low_observation, _ = self._process_observation(
                self.low_observation,
                feature=feature,
                index=index,
                target_consumed=self.high_target_consumed,
            )

        if touch_high and not self.high_source_used and self.high_observation is None:
            self.high_source_used = True
            self.high_observation = RaidObservation(
                observation_id=f"v25:{current.window_id}:high-raid:{index}",
                window_id=current.window_id,
                side=Side.SHORT,
                created_index=index,
                created_time_ns=int(feature.bar.end_time_ns),
                window_start_ns=current.start_time_ns,
                window_end_ns=current.end_time_ns,
                boundary=reference.high,
                target=reference.low,
                path_high=float(feature.bar.high),
                path_low=float(feature.bar.low),
            )
            self.counts["high_raids_observed"] += 1
            self._transition(
                scenario_id=self.high_observation.observation_id,
                window_id=current.window_id,
                feature=feature,
                index=index,
                event_type="RAID_OBSERVED",
                reason_code="PRIOR_FUNDING_WINDOW_HIGH_RAIDED",
                side=Side.SHORT,
                boundary=reference.high,
                target=reference.low,
                path_high=float(feature.bar.high),
                path_low=float(feature.bar.low),
            )
            self.high_observation, _ = self._process_observation(
                self.high_observation,
                feature=feature,
                index=index,
                target_consumed=self.low_target_consumed,
            )

        if touch_low and not self.low_source_used and self.low_observation is None:
            self.low_source_used = True
            self.low_observation = RaidObservation(
                observation_id=f"v25:{current.window_id}:low-raid:{index}",
                window_id=current.window_id,
                side=Side.LONG,
                created_index=index,
                created_time_ns=int(feature.bar.end_time_ns),
                window_start_ns=current.start_time_ns,
                window_end_ns=current.end_time_ns,
                boundary=reference.low,
                target=reference.high,
                path_high=float(feature.bar.high),
                path_low=float(feature.bar.low),
            )
            self.counts["low_raids_observed"] += 1
            self._transition(
                scenario_id=self.low_observation.observation_id,
                window_id=current.window_id,
                feature=feature,
                index=index,
                event_type="RAID_OBSERVED",
                reason_code="PRIOR_FUNDING_WINDOW_LOW_RAIDED",
                side=Side.LONG,
                boundary=reference.low,
                target=reference.high,
                path_high=float(feature.bar.high),
                path_low=float(feature.bar.low),
            )
            self.low_observation, _ = self._process_observation(
                self.low_observation,
                feature=feature,
                index=index,
                target_consumed=self.high_target_consumed,
            )


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
    book = FundingWindowBook()
    scenario = FundingFailedAuctionStateMachine(rule=args.rule, book=book)
    for bar in bars:
        feature_detector.on_bar(bar)
        index = len(feature_detector.features) - 1
        scenario.on_feature(index=index, features=feature_detector.features)

    plans = [
        plan
        for plan in scenario.plans
        if start_ns <= int(plan.signal_time_ns) < end_ns
    ]
    trades, windows = execution_trade_windows(
        records,
        plans=plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = run_nautilus_tick_plan_backtest(
        label=f"BTCUSDT-v25-{args.rule}-{evaluation_start.date().isoformat()}-7d",
        trades=trades,
        plans=plans,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        execution=execution,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
        output_dir=args.output,
    )

    pd.DataFrame(asdict(row) for row in scenario.detector.events).to_csv(
        args.output / "directional_change_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in scenario.transitions).to_csv(
        args.output / "funding_window_transitions.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in book.completed).to_csv(
        args.output / "completed_funding_windows.csv",
        index=False,
    )
    pd.DataFrame(asdict(plan) for plan in plans).to_csv(
        args.output / "scenario_plans.csv",
        index=False,
    )
    atomic_json(
        args.output / "daily_clock_calibrations.json",
        {"calibrations": [row.to_dict() for row in calibrations]},
    )
    payload = {
        "candidate": "prior funding-window edge raid, failed auction and MSS reversal",
        "rule": args.rule,
        "authoritative_backtest": True,
        "execution_engine": "NautilusTrader",
        "execution_data_type": "TradeTick",
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "context_days": CONTEXT_DAYS,
        "funding_window_hours": 8,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "directional_change_fraction": DIRECTIONAL_CHANGE_FRACTION,
        "state_counts": dict(scenario.counts),
        "selected_plan_count": len(plans),
        "completed_funding_window_count": len(book.completed),
        "official_execution_trade_ticks": len(trades),
        "execution_tick_windows": [list(row) for row in windows],
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "maximum_hold_hours": MAXIMUM_HOLD_NS / 3_600_000_000_000,
        "metrics": evidence.metrics,
        "downloads": [record.to_dict() for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(args.output / "funding_window_failed_auction_v25_summary.json", payload)
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
        default=ROOT / ".cache" / "candidate-01-v25-funding",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-v25-funding",
    )
    parser.add_argument("--workers", type=int, default=4)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
