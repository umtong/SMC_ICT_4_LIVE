#!/usr/bin/env python3
"""Detect failed auctions from causally confirmed adaptive balance ranges.

Clock-aligned four-hour blocks mix rotation, trend, and event shocks.  This
module first asks whether the market has actually balanced before assigning
liquidity meaning to its extremes.  A balance is frozen only after a completed
rolling window exhibits:

* low directional path efficiency,
* repeated midpoint crossings,
* a central close,
* bounded width in ATR units, and
* roughly balanced aggressive quote flow.

Only future bars may probe the frozen boundary.  A trade requires a close back
inside, then opposite displacement through internal structure with aligned
aggressive flow.  Entries are delayed one completed bar, costs are stressed at
7 bps per side, and each profile holds at most one position.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
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


@dataclass(frozen=True, slots=True)
class BalanceProfile:
    name: str
    window_bars: int
    maximum_path_efficiency: float
    minimum_width_atr: float
    maximum_width_atr: float
    minimum_mid_crosses: int
    maximum_abs_flow_imbalance: float
    maximum_extreme_residence: float
    minimum_close_location: float
    maximum_close_location: float
    validity_bars: int
    minimum_probe_atr: float
    maximum_probe_atr: float
    attempt_flow_z: float
    attempt_volume_z: float
    confirmation_bars: int
    minimum_displacement_body_atr: float
    maximum_displacement_body_atr: float
    minimum_reversal_flow_z: float
    maximum_structure_overshoot_atr: float
    stop_buffer_atr: float
    minimum_stop_atr: float
    target_mode: str
    cooldown_bars: int = 15
    max_hold_bars: int = 120


PROFILES = (
    BalanceProfile(
        name="balance-60-strong-midpoint",
        window_bars=60,
        maximum_path_efficiency=0.18,
        minimum_width_atr=4.0,
        maximum_width_atr=12.0,
        minimum_mid_crosses=4,
        maximum_abs_flow_imbalance=0.08,
        maximum_extreme_residence=0.55,
        minimum_close_location=0.20,
        maximum_close_location=0.80,
        validity_bars=120,
        minimum_probe_atr=0.08,
        maximum_probe_atr=1.8,
        attempt_flow_z=0.50,
        attempt_volume_z=0.25,
        confirmation_bars=6,
        minimum_displacement_body_atr=0.30,
        maximum_displacement_body_atr=1.20,
        minimum_reversal_flow_z=1.50,
        maximum_structure_overshoot_atr=0.90,
        stop_buffer_atr=0.15,
        minimum_stop_atr=0.75,
        target_mode="MIDPOINT",
    ),
    BalanceProfile(
        name="balance-60-strong-opposite",
        window_bars=60,
        maximum_path_efficiency=0.18,
        minimum_width_atr=4.0,
        maximum_width_atr=12.0,
        minimum_mid_crosses=4,
        maximum_abs_flow_imbalance=0.08,
        maximum_extreme_residence=0.55,
        minimum_close_location=0.20,
        maximum_close_location=0.80,
        validity_bars=120,
        minimum_probe_atr=0.08,
        maximum_probe_atr=1.8,
        attempt_flow_z=0.50,
        attempt_volume_z=0.25,
        confirmation_bars=6,
        minimum_displacement_body_atr=0.30,
        maximum_displacement_body_atr=1.20,
        minimum_reversal_flow_z=1.50,
        maximum_structure_overshoot_atr=0.90,
        stop_buffer_atr=0.15,
        minimum_stop_atr=0.75,
        target_mode="OPPOSITE",
    ),
    BalanceProfile(
        name="balance-90-strong-midpoint",
        window_bars=90,
        maximum_path_efficiency=0.18,
        minimum_width_atr=4.0,
        maximum_width_atr=14.0,
        minimum_mid_crosses=5,
        maximum_abs_flow_imbalance=0.08,
        maximum_extreme_residence=0.55,
        minimum_close_location=0.20,
        maximum_close_location=0.80,
        validity_bars=180,
        minimum_probe_atr=0.08,
        maximum_probe_atr=1.8,
        attempt_flow_z=0.50,
        attempt_volume_z=0.25,
        confirmation_bars=6,
        minimum_displacement_body_atr=0.30,
        maximum_displacement_body_atr=1.20,
        minimum_reversal_flow_z=1.50,
        maximum_structure_overshoot_atr=0.90,
        stop_buffer_atr=0.15,
        minimum_stop_atr=0.75,
        target_mode="MIDPOINT",
    ),
    BalanceProfile(
        name="balance-90-strong-opposite",
        window_bars=90,
        maximum_path_efficiency=0.18,
        minimum_width_atr=4.0,
        maximum_width_atr=14.0,
        minimum_mid_crosses=5,
        maximum_abs_flow_imbalance=0.08,
        maximum_extreme_residence=0.55,
        minimum_close_location=0.20,
        maximum_close_location=0.80,
        validity_bars=180,
        minimum_probe_atr=0.08,
        maximum_probe_atr=1.8,
        attempt_flow_z=0.50,
        attempt_volume_z=0.25,
        confirmation_bars=6,
        minimum_displacement_body_atr=0.30,
        maximum_displacement_body_atr=1.20,
        minimum_reversal_flow_z=1.50,
        maximum_structure_overshoot_atr=0.90,
        stop_buffer_atr=0.15,
        minimum_stop_atr=0.75,
        target_mode="OPPOSITE",
    ),
    BalanceProfile(
        name="balance-120-strong-midpoint",
        window_bars=120,
        maximum_path_efficiency=0.18,
        minimum_width_atr=4.0,
        maximum_width_atr=16.0,
        minimum_mid_crosses=6,
        maximum_abs_flow_imbalance=0.08,
        maximum_extreme_residence=0.55,
        minimum_close_location=0.20,
        maximum_close_location=0.80,
        validity_bars=240,
        minimum_probe_atr=0.08,
        maximum_probe_atr=1.8,
        attempt_flow_z=0.50,
        attempt_volume_z=0.25,
        confirmation_bars=6,
        minimum_displacement_body_atr=0.30,
        maximum_displacement_body_atr=1.20,
        minimum_reversal_flow_z=1.50,
        maximum_structure_overshoot_atr=0.90,
        stop_buffer_atr=0.15,
        minimum_stop_atr=0.75,
        target_mode="MIDPOINT",
    ),
    BalanceProfile(
        name="balance-120-strong-opposite",
        window_bars=120,
        maximum_path_efficiency=0.18,
        minimum_width_atr=4.0,
        maximum_width_atr=16.0,
        minimum_mid_crosses=6,
        maximum_abs_flow_imbalance=0.08,
        maximum_extreme_residence=0.55,
        minimum_close_location=0.20,
        maximum_close_location=0.80,
        validity_bars=240,
        minimum_probe_atr=0.08,
        maximum_probe_atr=1.8,
        attempt_flow_z=0.50,
        attempt_volume_z=0.25,
        confirmation_bars=6,
        minimum_displacement_body_atr=0.30,
        maximum_displacement_body_atr=1.20,
        minimum_reversal_flow_z=1.50,
        maximum_structure_overshoot_atr=0.90,
        stop_buffer_atr=0.15,
        minimum_stop_atr=0.75,
        target_mode="OPPOSITE",
    ),
    BalanceProfile(
        name="balance-60-compact-midpoint",
        window_bars=60,
        maximum_path_efficiency=0.18,
        minimum_width_atr=4.0,
        maximum_width_atr=12.0,
        minimum_mid_crosses=4,
        maximum_abs_flow_imbalance=0.08,
        maximum_extreme_residence=0.55,
        minimum_close_location=0.20,
        maximum_close_location=0.80,
        validity_bars=120,
        minimum_probe_atr=0.08,
        maximum_probe_atr=1.8,
        attempt_flow_z=0.50,
        attempt_volume_z=0.25,
        confirmation_bars=6,
        minimum_displacement_body_atr=0.30,
        maximum_displacement_body_atr=0.65,
        minimum_reversal_flow_z=1.00,
        maximum_structure_overshoot_atr=0.90,
        stop_buffer_atr=0.15,
        minimum_stop_atr=0.75,
        target_mode="MIDPOINT",
    ),
    BalanceProfile(
        name="balance-90-compact-midpoint",
        window_bars=90,
        maximum_path_efficiency=0.18,
        minimum_width_atr=4.0,
        maximum_width_atr=14.0,
        minimum_mid_crosses=5,
        maximum_abs_flow_imbalance=0.08,
        maximum_extreme_residence=0.55,
        minimum_close_location=0.20,
        maximum_close_location=0.80,
        validity_bars=180,
        minimum_probe_atr=0.08,
        maximum_probe_atr=1.8,
        attempt_flow_z=0.50,
        attempt_volume_z=0.25,
        confirmation_bars=6,
        minimum_displacement_body_atr=0.30,
        maximum_displacement_body_atr=0.65,
        minimum_reversal_flow_z=1.00,
        maximum_structure_overshoot_atr=0.90,
        stop_buffer_atr=0.15,
        minimum_stop_atr=0.75,
        target_mode="MIDPOINT",
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
    signed_flow: float
    aggressive_imbalance: float
    atr: float
    flow_z: float
    volume_z: float


@dataclass(frozen=True, slots=True)
class Balance:
    balance_id: str
    confirmed_index: int
    expiry_index: int
    high: float
    low: float
    midpoint: float
    width: float
    path_efficiency: float
    mid_crosses: int
    flow_imbalance: float


@dataclass(slots=True)
class Probe:
    scenario_id: str
    side: Side
    balance: Balance
    start_index: int
    expiry_index: int
    sweep_extreme: float
    internal_break: float


@dataclass(frozen=True, slots=True)
class Plan:
    scenario_id: str
    side: Side
    signal_index: int
    signal_time_ns: int
    balance_high: float
    balance_low: float
    balance_midpoint: float
    sweep_extreme: float
    expected_entry: float
    stop: float
    target: float
    atr: float
    displacement_flow_z: float
    displacement_body_atr: float
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
                signed_flow=item.signed_aggressive_quote,
                aggressive_imbalance=item.aggressive_imbalance,
                atr=atr,
                flow_z=_zscore(item.aggressive_imbalance, flow_history),
                volume_z=_zscore(item.quote_volume, volume_history),
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


def _balance(window: list[FeatureBar], profile: BalanceProfile) -> Balance | None:
    if len(window) != profile.window_bars:
        return None
    atr = window[-1].atr
    if not np.isfinite(atr) or atr <= 0.0:
        return None
    closes = np.asarray([item.close for item in window], dtype=float)
    highs = np.asarray([item.high for item in window], dtype=float)
    lows = np.asarray([item.low for item in window], dtype=float)
    high = float(highs.max())
    low = float(lows.min())
    width = high - low
    width_atr = width / atr
    if not profile.minimum_width_atr <= width_atr <= profile.maximum_width_atr:
        return None
    path = float(np.abs(np.diff(closes)).sum())
    efficiency = abs(float(closes[-1] - closes[0])) / path if path > 0.0 else 0.0
    if efficiency > profile.maximum_path_efficiency:
        return None
    close_location = (float(closes[-1]) - low) / width if width > 0.0 else 0.5
    if not profile.minimum_close_location <= close_location <= profile.maximum_close_location:
        return None
    midpoint = 0.5 * (high + low)
    signs = np.sign(closes - midpoint)
    signs = signs[signs != 0]
    mid_crosses = int(np.sum(signs[1:] != signs[:-1])) if len(signs) > 1 else 0
    if mid_crosses < profile.minimum_mid_crosses:
        return None
    quote = sum(item.quote_volume for item in window)
    flow = sum(item.signed_flow for item in window)
    flow_imbalance = flow / quote if quote > 0.0 else 0.0
    if abs(flow_imbalance) > profile.maximum_abs_flow_imbalance:
        return None
    upper = float(np.mean(closes >= low + 0.75 * width))
    lower = float(np.mean(closes <= low + 0.25 * width))
    if upper + lower > profile.maximum_extreme_residence:
        return None
    confirmed_index = window[-1].index
    return Balance(
        balance_id=f"balance:{profile.name}:{window[0].ts_ns}:{window[-1].ts_ns}",
        confirmed_index=confirmed_index,
        expiry_index=confirmed_index + profile.validity_bars,
        high=high,
        low=low,
        midpoint=midpoint,
        width=width,
        path_efficiency=efficiency,
        mid_crosses=mid_crosses,
        flow_imbalance=flow_imbalance,
    )


def _detect(features: list[FeatureBar], profile: BalanceProfile) -> tuple[list[Plan], list[dict[str, Any]]]:
    plans: list[Plan] = []
    events: list[dict[str, Any]] = []
    balance: Balance | None = None
    probe: Probe | None = None
    cooldown_until = -1

    for item in features:
        if balance is None and probe is None and item.index >= max(profile.window_bars - 1, cooldown_until):
            candidate = _balance(features[item.index - profile.window_bars + 1 : item.index + 1], profile)
            if candidate is not None:
                balance = candidate
                events.append(
                    {
                        "scenario_id": candidate.balance_id,
                        "event_type": "ADAPTIVE_BALANCE_CONFIRMED",
                        "event_time_ns": item.ts_ns,
                        "observed_time_ns": item.ts_ns,
                        "high": candidate.high,
                        "low": candidate.low,
                        "path_efficiency": candidate.path_efficiency,
                        "mid_crosses": candidate.mid_crosses,
                        "flow_imbalance": candidate.flow_imbalance,
                    },
                )
                continue

        if balance is not None and probe is None:
            if item.index > balance.expiry_index:
                balance = None
                continue
            prior = features[max(0, item.index - 6) : item.index]
            if len(prior) < 6 or not np.isfinite(item.atr) or item.atr <= 0.0:
                continue
            high_penetration = item.high - balance.high
            low_penetration = balance.low - item.low
            high_probe = (
                profile.minimum_probe_atr * item.atr <= high_penetration <= profile.maximum_probe_atr * item.atr
                and item.close <= balance.high
                and (item.flow_z >= profile.attempt_flow_z or item.volume_z >= profile.attempt_volume_z)
            )
            low_probe = (
                profile.minimum_probe_atr * item.atr <= low_penetration <= profile.maximum_probe_atr * item.atr
                and item.close >= balance.low
                and (item.flow_z <= -profile.attempt_flow_z or item.volume_z >= profile.attempt_volume_z)
            )
            if high_probe and low_probe:
                balance = None
                continue
            if not high_probe and not low_probe:
                continue
            side = Side.SHORT if high_probe else Side.LONG
            extreme = item.high if side is Side.SHORT else item.low
            internal_break = min(value.low for value in prior) if side is Side.SHORT else max(value.high for value in prior)
            scenario_id = f"{balance.balance_id}:probe:{item.ts_ns}:{side.value.lower()}"
            probe = Probe(
                scenario_id=scenario_id,
                side=side,
                balance=balance,
                start_index=item.index,
                expiry_index=item.index + profile.confirmation_bars,
                sweep_extreme=extreme,
                internal_break=internal_break,
            )
            events.append(
                {
                    "scenario_id": scenario_id,
                    "event_type": "BALANCE_BOUNDARY_PROBED",
                    "event_time_ns": item.ts_ns,
                    "observed_time_ns": item.ts_ns,
                    "side": side.value,
                    "sweep_extreme": extreme,
                    "internal_break": internal_break,
                },
            )
            continue

        if probe is None:
            continue
        if item.index > probe.expiry_index:
            probe = None
            continue
        if not np.isfinite(item.atr) or item.atr <= 0.0:
            continue
        if probe.side is Side.SHORT:
            probe.sweep_extreme = max(probe.sweep_extreme, item.high)
            invalid = item.high > probe.sweep_extreme + profile.stop_buffer_atr * item.atr
            body_atr = abs(item.close - item.open) / item.atr
            flow = -item.flow_z
            overshoot = (probe.internal_break - item.close) / item.atr
            displaced = (
                item.close < item.open
                and item.close < probe.internal_break
                and profile.minimum_displacement_body_atr <= body_atr <= profile.maximum_displacement_body_atr
                and flow >= profile.minimum_reversal_flow_z
                and overshoot <= profile.maximum_structure_overshoot_atr
            )
        else:
            probe.sweep_extreme = min(probe.sweep_extreme, item.low)
            invalid = item.low < probe.sweep_extreme - profile.stop_buffer_atr * item.atr
            body_atr = abs(item.close - item.open) / item.atr
            flow = item.flow_z
            overshoot = (item.close - probe.internal_break) / item.atr
            displaced = (
                item.close > item.open
                and item.close > probe.internal_break
                and profile.minimum_displacement_body_atr <= body_atr <= profile.maximum_displacement_body_atr
                and flow >= profile.minimum_reversal_flow_z
                and overshoot <= profile.maximum_structure_overshoot_atr
            )
        if invalid:
            probe = None
            balance = None
            continue
        if not displaced:
            continue

        if probe.side is Side.LONG:
            stop = min(
                probe.sweep_extreme - profile.stop_buffer_atr * item.atr,
                item.close - profile.minimum_stop_atr * item.atr,
            )
            target = probe.balance.midpoint if profile.target_mode == "MIDPOINT" else probe.balance.high
        else:
            stop = max(
                probe.sweep_extreme + profile.stop_buffer_atr * item.atr,
                item.close + profile.minimum_stop_atr * item.atr,
            )
            target = probe.balance.midpoint if profile.target_mode == "MIDPOINT" else probe.balance.low
        plans.append(
            Plan(
                scenario_id=probe.scenario_id,
                side=probe.side,
                signal_index=item.index,
                signal_time_ns=item.ts_ns,
                balance_high=probe.balance.high,
                balance_low=probe.balance.low,
                balance_midpoint=probe.balance.midpoint,
                sweep_extreme=probe.sweep_extreme,
                expected_entry=item.close,
                stop=stop,
                target=target,
                atr=item.atr,
                displacement_flow_z=flow,
                displacement_body_atr=body_atr,
                reason="ADAPTIVE_BALANCE_FAILED_AUCTION",
            ),
        )
        events.append(
            {
                "scenario_id": probe.scenario_id,
                "event_type": "TRADE_PLAN_EMITTED",
                "event_time_ns": item.ts_ns,
                "observed_time_ns": item.ts_ns,
                "side": probe.side.value,
                "stop": stop,
                "target": target,
                "displacement_flow_z": flow,
                "displacement_body_atr": body_atr,
            },
        )
        cooldown_until = item.index + profile.cooldown_bars
        probe = None
        balance = None

    return plans, events


def _loss(entry: float, stop: float, cost: float) -> float:
    return abs(entry - stop) + entry * cost + stop * cost


def _net_r(side: Side, entry: float, exit_price: float, stop: float, cost: float) -> float:
    gross = (exit_price - entry) * side.sign
    return (gross - entry * cost - exit_price * cost) / _loss(entry, stop, cost)


def _simulate(
    features: list[FeatureBar],
    plans: list[Plan],
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
    rejections = {"occupied": 0, "geometry": 0, "cost": 0, "rr": 0}
    for plan in plans:
        entry_index = plan.signal_index + 1
        if entry_index >= len(features) or entry_index <= occupied_until:
            rejections["occupied"] += 1
            continue
        entry_bar = features[entry_index]
        if not start_ns <= entry_bar.ts_ns < end_ns:
            continue
        entry = entry_bar.close
        geometry = (
            plan.stop < entry < plan.target
            if plan.side is Side.LONG
            else plan.target < entry < plan.stop
        )
        if not geometry:
            rejections["geometry"] += 1
            continue
        planned_loss = _loss(entry, plan.stop, cost)
        price_fraction = abs(entry - plan.stop) / planned_loss if planned_loss > 0.0 else 0.0
        planned_gain = abs(plan.target - entry) - entry * cost - plan.target * cost
        net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
        if price_fraction < minimum_price_risk_fraction:
            rejections["cost"] += 1
            continue
        if planned_gain <= 0.0 or net_rr < minimum_net_reward_risk:
            rejections["rr"] += 1
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
                "entry_time_ns": entry_bar.ts_ns,
                "entry": entry,
                "price_risk_fraction": price_fraction,
                "net_reward_risk_at_entry": net_rr,
                "exit_reason": exit_reason,
                "exit_price": exit_price,
                "bars_held": exit_offset,
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
    metrics_rows: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
        frame, _ = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=max(int(research.get("warmup_minutes", 420)), 300),
        )
        features = _features(to_auction_bars(frame), candidate)
        start_ns = int(pd.Timestamp(start).value)
        end_ns = int(pd.Timestamp(end).value)
        for profile in PROFILES:
            plans, events = _detect(features, profile)
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
            metrics_rows.append(metrics)

    table = pd.DataFrame(metrics_rows)
    table.to_csv(output / "adaptive_balance_metrics.csv", index=False)
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
    summary = {"rows": len(metrics_rows), "aggregate": aggregate}
    _atomic_json(output / "adaptive_balance_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-adaptive-balance")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-adaptive-balance")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
