#!/usr/bin/env python3
"""Discover durable outside-value acceptance as an independent scenario.

The rejected continuation implementation treated two outside closes and taker
flow as sufficient evidence of acceptance.  This module instead requires an
auction sequence:

    completed dealing range
    -> initiative breakout with directional flow
    -> sustained outside residence and volume-weighted value
    -> cumulative flow / path confirmation
    -> pullback that holds either the old boundary or accepted value
    -> next-bar continuation entry

All statistics used by the detector are based on bars completed before the
current decision.  Entries occur one completed bar later.  Stops return inside
the old range, targets project a fraction of the completed range, costs are
7 bps per side, and only one position can be active for each evaluated profile.
The profiles are economically distinct hypotheses, not a parameter optimizer.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
from math import sqrt
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from core import CandidateConfig, Side  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402


NS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class AcceptanceProfile:
    name: str
    observation_bars: int
    maximum_test_bars: int
    minimum_outside_fraction: float
    start_depth_atr: float
    start_flow_z: float
    minimum_flow_score: float
    minimum_value_depth_atr: float
    minimum_progress_atr: float
    minimum_path_efficiency: float
    retest_mode: str
    retest_window_bars: int
    retest_zone_atr: float
    hold_depth_atr: float
    minimum_retest_flow_z: float
    minimum_retest_close_location: float
    minimum_stop_atr: float
    stop_buffer_atr: float
    target_range_fraction: float
    max_hold_bars: int = 180


PROFILES = (
    AcceptanceProfile(
        name="fast-boundary-half-range",
        observation_bars=5,
        maximum_test_bars=8,
        minimum_outside_fraction=0.80,
        start_depth_atr=0.10,
        start_flow_z=0.60,
        minimum_flow_score=0.60,
        minimum_value_depth_atr=0.15,
        minimum_progress_atr=0.40,
        minimum_path_efficiency=0.15,
        retest_mode="BOUNDARY",
        retest_window_bars=24,
        retest_zone_atr=0.20,
        hold_depth_atr=0.05,
        minimum_retest_flow_z=-0.25,
        minimum_retest_close_location=0.55,
        minimum_stop_atr=0.75,
        stop_buffer_atr=0.15,
        target_range_fraction=0.50,
    ),
    AcceptanceProfile(
        name="established-boundary-half-range",
        observation_bars=10,
        maximum_test_bars=15,
        minimum_outside_fraction=0.80,
        start_depth_atr=0.10,
        start_flow_z=0.50,
        minimum_flow_score=0.35,
        minimum_value_depth_atr=0.20,
        minimum_progress_atr=0.30,
        minimum_path_efficiency=0.05,
        retest_mode="BOUNDARY",
        retest_window_bars=40,
        retest_zone_atr=0.25,
        hold_depth_atr=0.05,
        minimum_retest_flow_z=-0.35,
        minimum_retest_close_location=0.55,
        minimum_stop_atr=0.75,
        stop_buffer_atr=0.15,
        target_range_fraction=0.50,
    ),
    AcceptanceProfile(
        name="initiative-value-half-range",
        observation_bars=5,
        maximum_test_bars=8,
        minimum_outside_fraction=1.00,
        start_depth_atr=0.15,
        start_flow_z=0.75,
        minimum_flow_score=1.00,
        minimum_value_depth_atr=0.25,
        minimum_progress_atr=0.80,
        minimum_path_efficiency=0.30,
        retest_mode="VALUE",
        retest_window_bars=30,
        retest_zone_atr=0.20,
        hold_depth_atr=0.05,
        minimum_retest_flow_z=-0.15,
        minimum_retest_close_location=0.55,
        minimum_stop_atr=0.75,
        stop_buffer_atr=0.15,
        target_range_fraction=0.50,
    ),
    AcceptanceProfile(
        name="established-value-full-range",
        observation_bars=10,
        maximum_test_bars=15,
        minimum_outside_fraction=0.80,
        start_depth_atr=0.10,
        start_flow_z=0.50,
        minimum_flow_score=0.35,
        minimum_value_depth_atr=0.20,
        minimum_progress_atr=0.30,
        minimum_path_efficiency=0.05,
        retest_mode="VALUE",
        retest_window_bars=40,
        retest_zone_atr=0.20,
        hold_depth_atr=0.05,
        minimum_retest_flow_z=-0.25,
        minimum_retest_close_location=0.55,
        minimum_stop_atr=0.75,
        stop_buffer_atr=0.15,
        target_range_fraction=1.00,
    ),
)


@dataclass(frozen=True, slots=True)
class FeatureBar:
    index: int
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    atr: float
    flow_z: float
    volume_z: float
    block_id: int


@dataclass(frozen=True, slots=True)
class Anchor:
    block_id: int
    start_ns: int
    end_ns: int
    open: float
    high: float
    low: float
    close: float
    bars: int

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(slots=True)
class TestState:
    scenario_id: str
    breakout_side: Side
    anchor: Anchor
    boundary: float
    start_index: int
    start_close: float
    start_atr: float
    closes: list[float]
    quote_volumes: list[float]
    directional_flow_z: list[float]
    path_length: float
    previous_close: float
    outside_count: int
    accepted_index: int | None = None
    accepted_value: float | None = None
    accepted_extreme: float | None = None


@dataclass(frozen=True, slots=True)
class AcceptancePlan:
    scenario_id: str
    side: Side
    signal_index: int
    signal_time_ns: int
    boundary: float
    accepted_value: float
    anchor_high: float
    anchor_low: float
    expected_entry: float
    stop: float
    target: float
    atr: float
    reason: str


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _segments(research: dict[str, Any]) -> list[tuple[str, datetime, datetime, str]]:
    def week(label: str, value: str) -> tuple[str, datetime, datetime, str]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7), "quick"

    return [
        week("discovery", str(research["discovery_week"])),
        *[
            week(f"confirmation-{index + 1}", value)
            for index, value in enumerate(research["confirmation_weeks"])
        ],
        (
            "long-evaluation",
            parse_utc_date(str(research["long_start"])),
            parse_utc_date(str(research["long_end"])),
            "development",
        ),
    ]


def _zscore(value: float, history: deque[float]) -> float:
    values = np.asarray(history, dtype=float)
    if len(values) < 20:
        return 0.0
    std = float(values.std())
    if std <= 1e-12:
        return 0.0
    return (value - float(values.mean())) / std


def _features(bars: list[Any], candidate: CandidateConfig) -> list[FeatureBar]:
    true_ranges: deque[float] = deque(maxlen=candidate.atr_lookback)
    flow_history: deque[float] = deque(maxlen=candidate.flow_lookback)
    volume_history: deque[float] = deque(maxlen=candidate.volume_lookback)
    previous_close: float | None = None
    range_ns = candidate.range_minutes * NS_PER_MINUTE
    rows: list[FeatureBar] = []
    for index, item in enumerate(bars):
        atr = float(np.mean(true_ranges)) if len(true_ranges) >= max(20, candidate.atr_lookback // 2) else np.nan
        rows.append(
            FeatureBar(
                index=index,
                ts_ns=item.ts_event_ns,
                open=item.open,
                high=item.high,
                low=item.low,
                close=item.close,
                quote_volume=item.quote_volume,
                atr=atr,
                flow_z=_zscore(item.aggressive_imbalance, flow_history),
                volume_z=_zscore(item.quote_volume, volume_history),
                block_id=item.ts_event_ns // range_ns,
            ),
        )
        true_range = (
            item.high - item.low
            if previous_close is None
            else max(item.high - item.low, abs(item.high - previous_close), abs(item.low - previous_close))
        )
        true_ranges.append(true_range)
        flow_history.append(item.aggressive_imbalance)
        volume_history.append(item.quote_volume)
        previous_close = item.close
    return rows


def _anchor_from_block(block: list[FeatureBar], range_minutes: int) -> Anchor | None:
    if len(block) < int(range_minutes * 0.90):
        return None
    block_id = block[0].block_id
    range_ns = range_minutes * NS_PER_MINUTE
    return Anchor(
        block_id=block_id,
        start_ns=block_id * range_ns,
        end_ns=(block_id + 1) * range_ns,
        open=block[0].open,
        high=max(item.high for item in block),
        low=min(item.low for item in block),
        close=block[-1].close,
        bars=len(block),
    )


def _directional(side: Side, value: float) -> float:
    return value * side.sign


def _outside(side: Side, close: float, boundary: float, depth: float) -> bool:
    return close >= boundary + depth if side is Side.LONG else close <= boundary - depth


def _detect(features: list[FeatureBar], profile: AcceptanceProfile, range_minutes: int) -> tuple[list[AcceptancePlan], list[dict[str, Any]]]:
    plans: list[AcceptancePlan] = []
    events: list[dict[str, Any]] = []
    current_block: list[FeatureBar] = []
    current_block_id: int | None = None
    anchor: Anchor | None = None
    state: TestState | None = None
    traded_block: int | None = None

    for item in features:
        if current_block_id is None:
            current_block_id = item.block_id
        if item.block_id != current_block_id:
            completed = _anchor_from_block(current_block, range_minutes)
            if completed is not None:
                anchor = completed
                traded_block = None
                events.append(
                    {
                        "event_type": "DEALING_RANGE_CONFIRMED",
                        "event_time_ns": completed.end_ns,
                        "observed_time_ns": item.ts_ns,
                        "block_id": completed.block_id,
                        "high": completed.high,
                        "low": completed.low,
                    },
                )
            else:
                anchor = None
            state = None
            current_block = []
            current_block_id = item.block_id
        current_block.append(item)

        if anchor is None or not np.isfinite(item.atr) or item.atr <= 0.0:
            continue
        if traded_block == anchor.block_id:
            continue

        if state is None:
            long_start = (
                item.close >= anchor.high + profile.start_depth_atr * item.atr
                and item.flow_z >= profile.start_flow_z
                and item.volume_z >= -0.50
            )
            short_start = (
                item.close <= anchor.low - profile.start_depth_atr * item.atr
                and item.flow_z <= -profile.start_flow_z
                and item.volume_z >= -0.50
            )
            if not long_start and not short_start:
                continue
            side = Side.LONG if long_start else Side.SHORT
            boundary = anchor.high if side is Side.LONG else anchor.low
            scenario_id = f"BTCUSDT-PERP.BINANCE:{anchor.block_id}:{item.ts_ns}:accepted:{side.value.lower()}"
            state = TestState(
                scenario_id=scenario_id,
                breakout_side=side,
                anchor=anchor,
                boundary=boundary,
                start_index=item.index,
                start_close=item.close,
                start_atr=item.atr,
                closes=[item.close],
                quote_volumes=[item.quote_volume],
                directional_flow_z=[_directional(side, item.flow_z)],
                path_length=0.0,
                previous_close=item.close,
                outside_count=1,
            )
            events.append(
                {
                    "scenario_id": scenario_id,
                    "event_type": "OUTSIDE_TEST_STARTED",
                    "event_time_ns": item.ts_ns,
                    "observed_time_ns": item.ts_ns,
                    "side": side.value,
                    "boundary": boundary,
                    "atr": item.atr,
                    "flow_z": item.flow_z,
                },
            )
            continue

        side = state.breakout_side
        boundary = state.boundary
        elapsed = item.index - state.start_index + 1

        if state.accepted_index is None:
            state.path_length += abs(item.close - state.previous_close)
            state.previous_close = item.close
            state.closes.append(item.close)
            state.quote_volumes.append(item.quote_volume)
            state.directional_flow_z.append(_directional(side, item.flow_z))
            if _outside(side, item.close, boundary, profile.hold_depth_atr * item.atr):
                state.outside_count += 1

            hard_reentry = (
                item.close < boundary - profile.hold_depth_atr * item.atr
                if side is Side.LONG
                else item.close > boundary + profile.hold_depth_atr * item.atr
            )
            if hard_reentry:
                events.append(
                    {
                        "scenario_id": state.scenario_id,
                        "event_type": "OUTSIDE_TEST_FAILED",
                        "event_time_ns": item.ts_ns,
                        "observed_time_ns": item.ts_ns,
                        "reason": "VALUE_RETURNED_INSIDE_RANGE",
                    },
                )
                state = None
                continue
            if elapsed > profile.maximum_test_bars:
                events.append(
                    {
                        "scenario_id": state.scenario_id,
                        "event_type": "OUTSIDE_TEST_FAILED",
                        "event_time_ns": item.ts_ns,
                        "observed_time_ns": item.ts_ns,
                        "reason": "ACCEPTANCE_NOT_CONFIRMED_IN_TIME",
                    },
                )
                state = None
                continue
            if elapsed < profile.observation_bars:
                continue

            weights = np.asarray(state.quote_volumes, dtype=float)
            closes = np.asarray(state.closes, dtype=float)
            accepted_value = float(np.average(closes, weights=weights)) if float(weights.sum()) > 0.0 else float(closes.mean())
            value_depth = (accepted_value - boundary) * side.sign / item.atr
            progress = (item.close - boundary) * side.sign / item.atr
            outside_fraction = state.outside_count / elapsed
            flow_score = float(sum(state.directional_flow_z)) / sqrt(elapsed)
            net_change = abs(item.close - state.start_close)
            path_efficiency = net_change / state.path_length if state.path_length > 0.0 else 0.0
            accepted = (
                outside_fraction >= profile.minimum_outside_fraction
                and value_depth >= profile.minimum_value_depth_atr
                and progress >= profile.minimum_progress_atr
                and flow_score >= profile.minimum_flow_score
                and path_efficiency >= profile.minimum_path_efficiency
            )
            if not accepted:
                continue
            state.accepted_index = item.index
            state.accepted_value = accepted_value
            state.accepted_extreme = item.high if side is Side.LONG else item.low
            events.append(
                {
                    "scenario_id": state.scenario_id,
                    "event_type": "OUTSIDE_VALUE_ACCEPTED",
                    "event_time_ns": item.ts_ns,
                    "observed_time_ns": item.ts_ns,
                    "side": side.value,
                    "outside_fraction": outside_fraction,
                    "value_depth_atr": value_depth,
                    "progress_atr": progress,
                    "flow_score": flow_score,
                    "path_efficiency": path_efficiency,
                    "accepted_value": accepted_value,
                },
            )
            continue

        assert state.accepted_index is not None and state.accepted_value is not None
        since_acceptance = item.index - state.accepted_index
        if since_acceptance > profile.retest_window_bars:
            events.append(
                {
                    "scenario_id": state.scenario_id,
                    "event_type": "ACCEPTANCE_INVALIDATED",
                    "event_time_ns": item.ts_ns,
                    "observed_time_ns": item.ts_ns,
                    "reason": "NO_CAUSAL_RETEST_IN_TIME",
                },
            )
            state = None
            continue
        if side is Side.LONG and item.close < boundary - profile.hold_depth_atr * item.atr:
            state = None
            continue
        if side is Side.SHORT and item.close > boundary + profile.hold_depth_atr * item.atr:
            state = None
            continue

        retest_level = boundary if profile.retest_mode == "BOUNDARY" else state.accepted_value
        touched = (
            item.low <= retest_level + profile.retest_zone_atr * item.atr
            if side is Side.LONG
            else item.high >= retest_level - profile.retest_zone_atr * item.atr
        )
        held = _outside(side, item.close, boundary, profile.hold_depth_atr * item.atr)
        bar_range = item.high - item.low
        close_location = (
            (item.close - item.low) / bar_range
            if side is Side.LONG and bar_range > 0.0
            else (item.high - item.close) / bar_range
            if side is Side.SHORT and bar_range > 0.0
            else 0.5
        )
        flow_held = _directional(side, item.flow_z) >= profile.minimum_retest_flow_z
        if not (touched and held and close_location >= profile.minimum_retest_close_location and flow_held):
            continue

        if side is Side.LONG:
            stop = min(
                boundary - profile.minimum_stop_atr * item.atr,
                item.low - profile.stop_buffer_atr * item.atr,
            )
            target = boundary + profile.target_range_fraction * anchor.width
        else:
            stop = max(
                boundary + profile.minimum_stop_atr * item.atr,
                item.high + profile.stop_buffer_atr * item.atr,
            )
            target = boundary - profile.target_range_fraction * anchor.width
        plan = AcceptancePlan(
            scenario_id=state.scenario_id,
            side=side,
            signal_index=item.index,
            signal_time_ns=item.ts_ns,
            boundary=boundary,
            accepted_value=state.accepted_value,
            anchor_high=anchor.high,
            anchor_low=anchor.low,
            expected_entry=item.close,
            stop=stop,
            target=target,
            atr=item.atr,
            reason="OUTSIDE_VALUE_RETEST_HELD",
        )
        plans.append(plan)
        events.append(
            {
                "scenario_id": state.scenario_id,
                "event_type": "TRADE_PLAN_EMITTED",
                "event_time_ns": item.ts_ns,
                "observed_time_ns": item.ts_ns,
                "side": side.value,
                "stop": stop,
                "target": target,
                "retest_level": retest_level,
                "retest_mode": profile.retest_mode,
            },
        )
        traded_block = anchor.block_id
        state = None

    return plans, events


def _loss(entry: float, stop: float, cost: float) -> float:
    return abs(entry - stop) + entry * cost + stop * cost


def _net_r(side: Side, entry: float, exit_price: float, stop: float, cost: float) -> float:
    gross = (exit_price - entry) * side.sign
    return (gross - entry * cost - exit_price * cost) / _loss(entry, stop, cost)


def _simulate(
    features: list[FeatureBar],
    plans: list[AcceptancePlan],
    *,
    start_ns: int,
    end_ns: int,
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
    max_hold_bars: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    rows: list[dict[str, Any]] = []
    occupied_until = -1
    rejections = {"occupied": 0, "delayed_geometry": 0, "cost_dominated": 0, "insufficient_rr": 0}
    for plan in plans:
        entry_index = plan.signal_index + 1
        if entry_index >= len(features) or entry_index <= occupied_until:
            rejections["occupied"] += 1
            continue
        entry_bar = features[entry_index]
        if not start_ns <= entry_bar.ts_ns < end_ns:
            continue
        entry = entry_bar.close
        geometry_ok = (
            plan.stop < entry < plan.target
            if plan.side is Side.LONG
            else plan.target < entry < plan.stop
        )
        if not geometry_ok:
            rejections["delayed_geometry"] += 1
            continue
        planned_loss = _loss(entry, plan.stop, cost)
        price_risk = abs(entry - plan.stop)
        planned_gain = abs(plan.target - entry) - entry * cost - plan.target * cost
        price_fraction = price_risk / planned_loss if planned_loss > 0.0 else 0.0
        net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
        if price_fraction < minimum_price_risk_fraction:
            rejections["cost_dominated"] += 1
            continue
        if planned_gain <= 0.0 or net_rr < minimum_net_reward_risk:
            rejections["insufficient_rr"] += 1
            continue

        future = features[entry_index + 1 : entry_index + 1 + max_hold_bars]
        exit_reason = "TIME"
        exit_price = future[-1].close if future else entry
        exit_offset = len(future)
        for offset, item in enumerate(future, start=1):
            stop_hit = item.low <= plan.stop if plan.side is Side.LONG else item.high >= plan.stop
            target_hit = item.high >= plan.target if plan.side is Side.LONG else item.low <= plan.target
            if stop_hit:
                exit_reason = "STOP"
                exit_price = plan.stop
                exit_offset = offset
                break
            if target_hit:
                exit_reason = "TARGET"
                exit_price = plan.target
                exit_offset = offset
                break
        occupied_until = entry_index + exit_offset
        rows.append(
            {
                **asdict(plan),
                "side": plan.side.value,
                "entry_index": entry_index,
                "entry_time_ns": entry_bar.ts_ns,
                "entry": entry,
                "price_risk_fraction": price_fraction,
                "net_reward_risk_at_entry": net_rr,
                "exit_reason": exit_reason,
                "exit_price": exit_price,
                "exit_offset": exit_offset,
                "realized_r": _net_r(plan.side, entry, exit_price, plan.stop, cost),
            },
        )
    return pd.DataFrame(rows), rejections


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
        frame, _ = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=max(int(research.get("warmup_minutes", 420)), candidate.range_minutes + 180),
        )
        bars = to_auction_bars(frame)
        features = _features(bars, candidate)
        start_ns = int(pd.Timestamp(start).value)
        end_ns = int(pd.Timestamp(end).value)
        for profile in PROFILES:
            plans, events = _detect(features, profile, candidate.range_minutes)
            trades, rejections = _simulate(
                features,
                plans,
                start_ns=start_ns,
                end_ns=end_ns,
                cost=cost,
                minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
                minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
                max_hold_bars=profile.max_hold_bars,
            )
            destination = output / profile.name / label
            destination.mkdir(parents=True, exist_ok=True)
            trades.to_csv(destination / "trades.csv", index=False)
            with (destination / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
                for event in events:
                    stream.write(json.dumps(event, sort_keys=True) + "\n")
            values = pd.to_numeric(trades.get("realized_r", pd.Series(dtype=float)), errors="coerce").dropna()
            gross_profit = float(values[values > 0.0].sum())
            gross_loss = abs(float(values[values < 0.0].sum()))
            days = max((end - start).total_seconds() / 86_400.0, 1.0)
            growth = float((1.0 + 0.01 * values).prod()) if len(values) else 1.0
            metrics = {
                "profile": profile.name,
                "segment": label,
                "role": role,
                "calendar_days": days,
                "plans": len(plans),
                "trades": int(len(values)),
                "trades_per_day": len(values) / days,
                "sum_r": float(values.sum()),
                "mean_r": float(values.mean()) if len(values) else None,
                "win_rate": float((values > 0.0).mean()) if len(values) else None,
                "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
                "growth_factor_at_one_percent_risk": growth,
                "geometric_mean_daily_return_at_one_percent_risk": growth ** (1.0 / days) - 1.0,
                "exit_counts": trades.get("exit_reason", pd.Series(dtype=str)).value_counts().to_dict(),
                "rejections": rejections,
                "profile_config": asdict(profile),
            }
            _atomic_json(destination / "metrics.json", metrics)
            summaries.append(metrics)

    table = pd.DataFrame(summaries)
    table.to_csv(output / "accepted_auction_metrics.csv", index=False)
    aggregate: list[dict[str, Any]] = []
    for (role, profile), group in table.groupby(["role", "profile"], sort=True):
        growth = float(np.prod(group["growth_factor_at_one_percent_risk"].astype(float)))
        days = float(group["calendar_days"].sum())
        aggregate.append(
            {
                "role": role,
                "profile": profile,
                "segments": int(len(group)),
                "trades": int(group["trades"].sum()),
                "sum_r": float(group["sum_r"].sum()),
                "growth_factor_at_one_percent_risk": growth,
                "geometric_mean_daily_return_at_one_percent_risk": growth ** (1.0 / days) - 1.0,
            },
        )
    summary = {"rows": len(summaries), "aggregate": aggregate}
    _atomic_json(output / "accepted_auction_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-accepted-auction")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-accepted-auction")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
