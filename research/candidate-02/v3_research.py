"""Prospectively locked research implementation for candidate-02 v3.

The module implements one causal scenario rather than a bag of entry filters:

mature intraday auction -> first external boundary test -> breakout-side
aggression and two-close acceptance -> failed acceptance back inside the
auction -> next-minute entry toward the opposite frozen boundary.

Only completed one-minute and five-minute bars are consumed. The same state
machine is run at the adjacent 8-hour and 9-hour auction horizons. A single
portfolio arbiter permits at most one pending entry or open position.

This file is the exact fast falsification implementation locked before the
prospective BTC holdout was opened. NautilusTrader remains the authoritative
execution path for final validation and live integration.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import argparse
import io
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd


NS_MINUTE = 60_000_000_000
FIVE_MINUTES_NS = 5 * NS_MINUTE
BINANCE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)


@dataclass(frozen=True, slots=True)
class CostModel:
    maker_fee_rate: float = 0.00020
    taker_fee_rate: float = 0.00050
    entry_slippage_rate: float = 0.00015
    stop_slippage_rate: float = 0.00025
    market_impact_rate: float = 0.00005
    funding_allowance_rate: float = 0.00010

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class CandidateV3Config:
    auction_horizons_5m: tuple[int, ...] = (96, 108)
    minimum_source_age_bars: int = 6
    boundary_touch_tolerance_atr: float = 0.20
    minimum_tested_boundary_touches: int = 3
    maximum_source_span_atr: float = 20.0
    maximum_source_path_efficiency: float = 0.16
    minimum_excursion_atr: float = 0.05
    maximum_excursion_atr: float = 3.0
    breakout_flow_quantile: float = 0.65
    activity_quantile: float = 0.50
    acceptance_consecutive_closes: int = 2
    acceptance_deadline_bars: int = 4
    failure_window_bars: int = 12
    minimum_failure_depth_atr: float = 0.10
    failure_close_half_fraction: float = 0.50
    stop_buffer_atr: float = 0.10
    minimum_cost_after_reward_risk: float = 1.0
    maximum_holding_minutes: int = 720
    cooldown_bars: int = 6
    risk_fraction: float = 0.06
    rank_policy: str = "longest_horizon"
    atr_lookback_5m: int = 48
    atr_minimum_observations: int = 24
    flow_lookback_5m: int = 288
    flow_minimum_observations: int = 96
    warmup_days: int = 2
    tail_hours: int = 12

    def __post_init__(self) -> None:
        if not self.auction_horizons_5m or any(value <= 1 for value in self.auction_horizons_5m):
            raise ValueError("auction_horizons_5m must contain positive horizons")
        if tuple(sorted(set(self.auction_horizons_5m))) != self.auction_horizons_5m:
            raise ValueError("auction_horizons_5m must be sorted and unique")
        integer_fields = (
            "minimum_source_age_bars",
            "minimum_tested_boundary_touches",
            "acceptance_consecutive_closes",
            "acceptance_deadline_bars",
            "failure_window_bars",
            "maximum_holding_minutes",
            "cooldown_bars",
            "atr_lookback_5m",
            "atr_minimum_observations",
            "flow_lookback_5m",
            "flow_minimum_observations",
            "warmup_days",
            "tail_hours",
        )
        for name in integer_fields:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0.0 < self.breakout_flow_quantile < 1.0:
            raise ValueError("breakout_flow_quantile must be in (0, 1)")
        if not 0.0 < self.activity_quantile < 1.0:
            raise ValueError("activity_quantile must be in (0, 1)")
        if not 0.0 < self.failure_close_half_fraction < 1.0:
            raise ValueError("failure_close_half_fraction must be in (0, 1)")
        if not 0.0 < self.risk_fraction < 1.0:
            raise ValueError("risk_fraction must be in (0, 1)")
        if self.rank_policy not in {"longest_horizon", "highest_net_rr", "most_touches"}:
            raise ValueError(f"unknown rank_policy: {self.rank_policy}")


@dataclass(slots=True)
class WeekData:
    name: str
    evaluation_start_ns: int
    evaluation_end_ns: int
    one_minute_time_ns: np.ndarray
    one_minute_open: np.ndarray
    one_minute_high: np.ndarray
    one_minute_low: np.ndarray
    one_minute_close: np.ndarray
    five_minute_time_ns: np.ndarray
    five_minute_open: np.ndarray
    five_minute_high: np.ndarray
    five_minute_low: np.ndarray
    five_minute_close: np.ndarray
    five_minute_volume: np.ndarray
    five_minute_trade_count: np.ndarray
    five_minute_buy_share: np.ndarray
    prior_atr: np.ndarray
    prior_flow_high: np.ndarray
    prior_flow_low: np.ndarray
    prior_volume_threshold: np.ndarray
    prior_trade_threshold: np.ndarray


@dataclass(frozen=True, slots=True)
class AuctionSource:
    horizon: int
    boundary: str
    source_index: int
    source_time_ns: int
    tested_level: float
    opposite_level: float
    age_bars: int
    tested_boundary_touches: int
    atr: float
    path_efficiency: float
    span_atr: float


@dataclass(slots=True)
class AcceptanceSetup:
    source: AuctionSource
    test_index: int
    breakout_direction: int
    excursion_extreme: float
    excursion_atr: float
    outside_count: int = 0
    breakout_flow_seen: bool = False
    accepted_index: int | None = None


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    horizon: int
    setup: AcceptanceSetup
    signal_index: int
    trade_side: int
    entry_reference: float
    stop_price: float
    target_price: float
    expected_stop_loss_per_unit: float
    expected_target_reward_per_unit: float
    cost_after_reward_risk: float


@dataclass(slots=True)
class TradeRecord:
    week: str
    horizon: int
    side: int
    source_time_ns: int
    source_level: float
    opposite_level: float
    signal_time_ns: int
    entry_time_ns: int
    exit_time_ns: int
    outcome: str
    pnl: float
    return_on_nav: float
    holding_minutes: int
    entry_reference: float
    entry_fill: float
    exit_fill: float
    stop_price: float
    target_price: float
    cost_after_reward_risk: float
    planned_loss: float
    planned_loss_fraction: float
    quantity: float
    effective_notional_multiple: float
    source_atr: float
    source_age_bars: int
    source_touches: int
    source_efficiency: float
    source_span_atr: float
    excursion_atr: float
    test_buy_share: float
    relative_test_volume: float
    relative_test_trades: float
    maximum_adverse_excursion_fraction: float
    maximum_favorable_excursion_fraction: float


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    exit_time_ns: int
    outcome: str
    pnl: float
    return_on_nav: float
    holding_minutes: int
    quantity: float
    entry_fill: float
    exit_fill: float
    planned_loss: float
    effective_notional_multiple: float
    nav_path: tuple[float, ...]
    maximum_adverse_excursion_fraction: float
    maximum_favorable_excursion_fraction: float


class FailedAcceptanceDetector:
    """Parallel auction-state detectors with persistent first-test consumption."""

    def __init__(self, week: WeekData, config: CandidateV3Config, costs: CostModel) -> None:
        self.week = week
        self.config = config
        self.costs = costs
        self.states: dict[int, AcceptanceSetup | None] = {
            horizon: None for horizon in config.auction_horizons_5m
        }
        self.consumed: dict[int, set[tuple[str, int]]] = {
            horizon: set() for horizon in config.auction_horizons_5m
        }
        self.diagnostics: Counter[str] = Counter()

    def reset_transient_states(self) -> None:
        for horizon in self.states:
            self.states[horizon] = None

    def _source(self, index: int, horizon: int, boundary: str) -> AuctionSource | None:
        start = index - horizon
        if start < 0:
            return None
        week = self.week
        config = self.config
        atr = float(week.prior_atr[index])
        if not math.isfinite(atr) or atr <= 0.0:
            return None

        highs = week.five_minute_high[start:index]
        lows = week.five_minute_low[start:index]
        closes = week.five_minute_close[start:index]
        if len(highs) != horizon:
            return None
        high_level = float(np.max(highs))
        low_level = float(np.min(lows))

        if boundary == "HIGH":
            local_source_index = int(np.argmax(highs))
            tested_level = high_level
            opposite_level = low_level
            touches = int(np.sum(highs >= tested_level - config.boundary_touch_tolerance_atr * atr))
            previous_close_inside = bool(week.five_minute_close[index - 1] <= tested_level)
        elif boundary == "LOW":
            local_source_index = int(np.argmin(lows))
            tested_level = low_level
            opposite_level = high_level
            touches = int(np.sum(lows <= tested_level + config.boundary_touch_tolerance_atr * atr))
            previous_close_inside = bool(week.five_minute_close[index - 1] >= tested_level)
        else:
            raise ValueError(f"unknown boundary: {boundary}")

        source_index = start + local_source_index
        if (boundary, source_index) in self.consumed[horizon]:
            return None
        age_bars = index - source_index
        path_length = float(np.abs(np.diff(closes)).sum())
        path_efficiency = (
            abs(float(closes[-1] - closes[0])) / path_length if path_length > 0.0 else 0.0
        )
        span_atr = (high_level - low_level) / atr
        if (
            not previous_close_inside
            or age_bars < config.minimum_source_age_bars
            or touches < config.minimum_tested_boundary_touches
            or span_atr > config.maximum_source_span_atr
            or path_efficiency > config.maximum_source_path_efficiency
        ):
            return None
        return AuctionSource(
            horizon=horizon,
            boundary=boundary,
            source_index=source_index,
            source_time_ns=int(week.five_minute_time_ns[source_index]),
            tested_level=tested_level,
            opposite_level=opposite_level,
            age_bars=age_bars,
            tested_boundary_touches=touches,
            atr=atr,
            path_efficiency=path_efficiency,
            span_atr=span_atr,
        )

    def _breakout_impulse(self, index: int, breakout_direction: int) -> bool:
        week = self.week
        values = (
            week.prior_flow_high[index],
            week.prior_flow_low[index],
            week.prior_volume_threshold[index],
            week.prior_trade_threshold[index],
        )
        if not all(math.isfinite(float(value)) for value in values):
            return False
        flow_ok = (
            week.five_minute_buy_share[index] >= week.prior_flow_high[index]
            if breakout_direction > 0
            else week.five_minute_buy_share[index] <= week.prior_flow_low[index]
        )
        return bool(
            flow_ok
            and week.five_minute_volume[index] >= week.prior_volume_threshold[index]
            and week.five_minute_trade_count[index] >= week.prior_trade_threshold[index]
        )

    def _cost_geometry(
        self,
        entry: float,
        stop: float,
        target: float,
        side: int,
    ) -> tuple[float, float, float, float, float]:
        costs = self.costs
        entry_fill = entry * (1.0 + side * (costs.entry_slippage_rate + costs.market_impact_rate))
        stop_fill = stop * (1.0 - side * (costs.stop_slippage_rate + costs.market_impact_rate))
        stop_net = (
            side * (stop_fill - entry_fill)
            - entry_fill * costs.taker_fee_rate
            - stop_fill * costs.taker_fee_rate
            - entry * costs.funding_allowance_rate
        )
        expected_stop_loss = -stop_net
        target_reward = (
            side * (target - entry_fill)
            - entry_fill * costs.taker_fee_rate
            - target * costs.maker_fee_rate
            - entry * costs.funding_allowance_rate
        )
        ratio = target_reward / expected_stop_loss if expected_stop_loss > 0.0 else -math.inf
        return expected_stop_loss, target_reward, ratio, entry_fill, stop_fill

    def _candidate(self, horizon: int, setup: AcceptanceSetup, index: int) -> SignalCandidate | None:
        week = self.week
        config = self.config
        signal_time_ns = int(week.five_minute_time_ns[index])
        one_minute_index = int(np.searchsorted(week.one_minute_time_ns, signal_time_ns))
        if (
            one_minute_index >= len(week.one_minute_time_ns)
            or not week.evaluation_start_ns <= signal_time_ns < week.evaluation_end_ns
        ):
            return None
        entry = float(week.one_minute_open[one_minute_index])
        side = -setup.breakout_direction
        stop = (
            setup.excursion_extreme + config.stop_buffer_atr * setup.source.atr
            if side < 0
            else setup.excursion_extreme - config.stop_buffer_atr * setup.source.atr
        )
        target = setup.source.opposite_level
        geometry_valid = stop > 0.0 and (
            (side > 0 and stop < entry < target)
            or (side < 0 and target < entry < stop)
        )
        if not geometry_valid:
            self.diagnostics["INVALID_GEOMETRY"] += 1
            return None
        per_loss, reward, reward_risk, _, _ = self._cost_geometry(entry, stop, target, side)
        if reward_risk < config.minimum_cost_after_reward_risk:
            self.diagnostics["COST_AFTER_RR_REJECTED"] += 1
            return None
        return SignalCandidate(
            horizon=horizon,
            setup=setup,
            signal_index=index,
            trade_side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            expected_stop_loss_per_unit=per_loss,
            expected_target_reward_per_unit=reward,
            cost_after_reward_risk=reward_risk,
        )

    def update(self, index: int) -> list[SignalCandidate]:
        week = self.week
        config = self.config
        candidates: list[SignalCandidate] = []
        for horizon in config.auction_horizons_5m:
            setup = self.states[horizon]
            if setup is None:
                tests: list[tuple[float, AuctionSource]] = []
                for boundary in ("HIGH", "LOW"):
                    source = self._source(index, horizon, boundary)
                    if source is None:
                        continue
                    excursion = (
                        (week.five_minute_high[index] - source.tested_level) / source.atr
                        if boundary == "HIGH"
                        else (source.tested_level - week.five_minute_low[index]) / source.atr
                    )
                    if config.minimum_excursion_atr <= excursion <= config.maximum_excursion_atr:
                        tests.append((float(excursion), source))
                if tests:
                    # Every materially traversed pool is consumed. If one bar
                    # crosses both sides, keeping the unselected side would
                    # create a fictitious future first test.
                    for _, tested in tests:
                        self.consumed[horizon].add((tested.boundary, tested.source_index))
                    excursion, source = max(tests, key=lambda item: item[0])
                    breakout_direction = 1 if source.boundary == "HIGH" else -1
                    setup = AcceptanceSetup(
                        source=source,
                        test_index=index,
                        breakout_direction=breakout_direction,
                        excursion_extreme=float(
                            week.five_minute_high[index]
                            if breakout_direction > 0
                            else week.five_minute_low[index]
                        ),
                        excursion_atr=excursion,
                    )
                    self.states[horizon] = setup
                    self.diagnostics["BOUNDARY_TEST_STARTED"] += 1
                    self.diagnostics["POOLS_CONSUMED_ON_TEST"] += len(tests)

            if setup is None:
                continue
            setup.excursion_extreme = (
                max(setup.excursion_extreme, float(week.five_minute_high[index]))
                if setup.breakout_direction > 0
                else min(setup.excursion_extreme, float(week.five_minute_low[index]))
            )
            setup.breakout_flow_seen = setup.breakout_flow_seen or self._breakout_impulse(
                index,
                setup.breakout_direction,
            )
            outside = (
                week.five_minute_close[index] > setup.source.tested_level
                if setup.breakout_direction > 0
                else week.five_minute_close[index] < setup.source.tested_level
            )
            setup.outside_count = setup.outside_count + 1 if outside else 0
            inside_depth = (
                (setup.source.tested_level - week.five_minute_close[index]) / setup.source.atr
                if setup.breakout_direction > 0
                else (week.five_minute_close[index] - setup.source.tested_level) / setup.source.atr
            )
            bar_range = week.five_minute_high[index] - week.five_minute_low[index]
            close_location = (
                (week.five_minute_close[index] - week.five_minute_low[index]) / bar_range
                if bar_range > 0.0
                else 0.5
            )
            failure_location_ok = (
                close_location <= config.failure_close_half_fraction
                if setup.breakout_direction > 0
                else close_location >= 1.0 - config.failure_close_half_fraction
            )

            if setup.accepted_index is None:
                if (
                    setup.outside_count >= config.acceptance_consecutive_closes
                    and setup.breakout_flow_seen
                ):
                    setup.accepted_index = index
                    self.diagnostics["BREAKOUT_ACCEPTED"] += 1
                elif index - setup.test_index + 1 >= config.acceptance_deadline_bars:
                    self.states[horizon] = None
                    self.diagnostics["BOUNDARY_TEST_EXPIRED"] += 1
            else:
                if inside_depth >= config.minimum_failure_depth_atr and failure_location_ok:
                    candidate = self._candidate(horizon, setup, index)
                    if candidate is not None:
                        candidates.append(candidate)
                        self.diagnostics["FAILED_ACCEPTANCE_SIGNAL"] += 1
                    self.states[horizon] = None
                elif index - setup.accepted_index + 1 >= config.failure_window_bars:
                    self.states[horizon] = None
                    self.diagnostics["FAILURE_WINDOW_EXPIRED"] += 1
        return candidates

    def choose(self, candidates: Sequence[SignalCandidate]) -> SignalCandidate | None:
        if not candidates:
            return None
        if self.config.rank_policy == "longest_horizon":
            return max(
                candidates,
                key=lambda item: (
                    item.horizon,
                    item.cost_after_reward_risk,
                    item.setup.source.tested_boundary_touches,
                ),
            )
        if self.config.rank_policy == "most_touches":
            return max(
                candidates,
                key=lambda item: (
                    item.setup.source.tested_boundary_touches,
                    item.horizon,
                    item.cost_after_reward_risk,
                ),
            )
        return max(
            candidates,
            key=lambda item: (
                item.cost_after_reward_risk,
                item.horizon,
                item.setup.source.tested_boundary_touches,
            ),
        )


def _effective_stop_fill(
    *,
    side: int,
    stop: float,
    bar_open: float,
    costs: CostModel,
) -> float:
    # Stop-market gaps execute at the worse of the trigger and the bar open,
    # followed by adverse slippage and impact.
    trigger_or_gap = min(stop, bar_open) if side > 0 else max(stop, bar_open)
    return trigger_or_gap * (1.0 - side * (costs.stop_slippage_rate + costs.market_impact_rate))


def execute_candidate(
    week: WeekData,
    candidate: SignalCandidate,
    nav: float,
    config: CandidateV3Config,
    costs: CostModel,
) -> ExecutionResult | None:
    signal_time_ns = int(week.five_minute_time_ns[candidate.signal_index])
    start = int(np.searchsorted(week.one_minute_time_ns, signal_time_ns))
    end = int(
        np.searchsorted(
            week.one_minute_time_ns,
            min(signal_time_ns + config.maximum_holding_minutes * NS_MINUTE, week.evaluation_end_ns),
            side="right",
        ),
    )
    if start >= len(week.one_minute_time_ns) or end <= start:
        return None

    side = candidate.trade_side
    entry_reference = float(week.one_minute_open[start])
    entry_fill = entry_reference * (
        1.0 + side * (costs.entry_slippage_rate + costs.market_impact_rate)
    )
    planned_loss_per_unit = candidate.expected_stop_loss_per_unit
    if planned_loss_per_unit <= 0.0:
        return None
    planned_loss = nav * config.risk_fraction
    quantity = planned_loss / planned_loss_per_unit
    if quantity <= 0.0:
        return None
    effective_notional_multiple = quantity * entry_reference / nav

    nav_path: list[float] = []
    maximum_adverse = 0.0
    maximum_favorable = 0.0
    stop_index: int | None = None
    target_index: int | None = None
    for offset in range(end - start):
        index = start + offset
        stop_hit = (
            week.one_minute_low[index] <= candidate.stop_price
            if side > 0
            else week.one_minute_high[index] >= candidate.stop_price
        )
        target_hit = (
            week.one_minute_high[index] >= candidate.target_price
            if side > 0
            else week.one_minute_low[index] <= candidate.target_price
        )
        if stop_hit and stop_index is None:
            stop_index = offset
        if target_hit and target_index is None:
            target_index = offset

        adverse_mark = (
            float(week.one_minute_low[index]) if side > 0 else float(week.one_minute_high[index])
        )
        favorable_mark = (
            float(week.one_minute_high[index]) if side > 0 else float(week.one_minute_low[index])
        )
        adverse_pnl = (
            side * (adverse_mark - entry_fill)
            - entry_fill * costs.taker_fee_rate
            - entry_reference * costs.funding_allowance_rate
        ) * quantity
        favorable_pnl = (
            side * (favorable_mark - entry_fill)
            - entry_fill * costs.taker_fee_rate
            - entry_reference * costs.funding_allowance_rate
        ) * quantity

        # Unknown intrabar ordering cannot expose the account beyond an already
        # triggered stop. On the trigger bar use the modeled stop execution, not
        # a later raw OHLC extreme which the live position would no longer hold.
        if stop_hit:
            modeled_stop_fill = _effective_stop_fill(
                side=side,
                stop=candidate.stop_price,
                bar_open=float(week.one_minute_open[index]),
                costs=costs,
            )
            adverse_pnl = (
                side * (modeled_stop_fill - entry_fill)
                - entry_fill * costs.taker_fee_rate
                - modeled_stop_fill * costs.taker_fee_rate
                - entry_reference * costs.funding_allowance_rate
            ) * quantity
        maximum_adverse = min(maximum_adverse, adverse_pnl / nav)
        if not stop_hit:
            maximum_favorable = max(maximum_favorable, favorable_pnl / nav)

        # Stop-first resolution on a one-minute bar is conservative when both
        # trigger prices are touched before intrabar ordering is known.
        if stop_hit or target_hit:
            break
        mark = float(week.one_minute_close[index])
        marked_pnl = (
            side * (mark - entry_fill)
            - entry_fill * costs.taker_fee_rate
            - entry_reference * costs.funding_allowance_rate
        ) * quantity
        nav_path.append(nav + marked_pnl)

    stop_offset = stop_index if stop_index is not None else 10**12
    target_offset = target_index if target_index is not None else 10**12
    if stop_offset <= target_offset and stop_index is not None:
        offset = stop_index
        index = start + offset
        exit_fill = _effective_stop_fill(
            side=side,
            stop=candidate.stop_price,
            bar_open=float(week.one_minute_open[index]),
            costs=costs,
        )
        exit_fee = costs.taker_fee_rate
        outcome = "STOP"
    elif target_index is not None:
        offset = target_index
        index = start + offset
        exit_fill = candidate.target_price
        exit_fee = costs.maker_fee_rate
        outcome = "TARGET"
    else:
        offset = end - start - 1
        index = start + offset
        exit_reference = float(week.one_minute_close[index])
        exit_fill = exit_reference * (
            1.0 - side * (costs.entry_slippage_rate + costs.market_impact_rate)
        )
        exit_fee = costs.taker_fee_rate
        outcome = "TIME"

    pnl = (
        side * (exit_fill - entry_fill)
        - entry_fill * costs.taker_fee_rate
        - exit_fill * exit_fee
        - entry_reference * costs.funding_allowance_rate
    ) * quantity
    final_nav = nav + pnl
    nav_path.append(final_nav)
    return ExecutionResult(
        exit_time_ns=int(week.one_minute_time_ns[index]),
        outcome=outcome,
        pnl=float(pnl),
        return_on_nav=float(pnl / nav),
        holding_minutes=int(offset + 1),
        quantity=float(quantity),
        entry_fill=float(entry_fill),
        exit_fill=float(exit_fill),
        planned_loss=float(planned_loss),
        effective_notional_multiple=float(effective_notional_multiple),
        nav_path=tuple(float(value) for value in nav_path),
        maximum_adverse_excursion_fraction=float(maximum_adverse),
        maximum_favorable_excursion_fraction=float(maximum_favorable),
    )


def simulate_week(
    week: WeekData,
    config: CandidateV3Config,
    costs: CostModel,
) -> tuple[list[TradeRecord], list[float], Mapping[str, int]]:
    detector = FailedAcceptanceDetector(week, config, costs)
    nav = 100_000.0
    nav_path = [nav]
    trades: list[TradeRecord] = []
    index = max(100, max(config.auction_horizons_5m) + 1)
    busy_until_ns = -1
    cooldown_until_index = -1
    reset_after_cooldown = False

    while index < len(week.five_minute_time_ns):
        observed_ns = int(week.five_minute_time_ns[index])
        if busy_until_ns >= 0 and observed_ns > busy_until_ns:
            busy_until_ns = -1
            detector.reset_transient_states()
            cooldown_until_index = index + config.cooldown_bars
            reset_after_cooldown = True
        if reset_after_cooldown and index >= cooldown_until_index:
            detector.reset_transient_states()
            reset_after_cooldown = False

        candidates = detector.update(index)
        busy = busy_until_ns >= 0
        cooling_down = index < cooldown_until_index
        if busy or cooling_down:
            if candidates:
                detector.diagnostics[
                    "SIGNAL_REJECTED_GLOBAL_BUSY" if busy else "SIGNAL_REJECTED_COOLDOWN"
                ] += len(candidates)
            index += 1
            continue

        selected = detector.choose(candidates)
        if selected is None:
            index += 1
            continue
        detector.diagnostics["CROSS_HORIZON_REJECTED"] += len(candidates) - 1
        result = execute_candidate(week, selected, nav, config, costs)
        if result is None:
            detector.diagnostics["EXECUTION_SKIPPED"] += 1
            index += 1
            continue

        setup = selected.setup
        source = setup.source
        signal_time_ns = int(week.five_minute_time_ns[selected.signal_index])
        one_minute_index = int(np.searchsorted(week.one_minute_time_ns, signal_time_ns))
        nav_path.extend(result.nav_path)
        nav += result.pnl
        relative_volume = (
            week.five_minute_volume[setup.test_index]
            / week.prior_volume_threshold[setup.test_index]
        )
        relative_trades = (
            week.five_minute_trade_count[setup.test_index]
            / week.prior_trade_threshold[setup.test_index]
        )
        trades.append(
            TradeRecord(
                week=week.name,
                horizon=source.horizon,
                side=selected.trade_side,
                source_time_ns=source.source_time_ns,
                source_level=source.tested_level,
                opposite_level=source.opposite_level,
                signal_time_ns=signal_time_ns,
                entry_time_ns=int(week.one_minute_time_ns[one_minute_index]),
                exit_time_ns=result.exit_time_ns,
                outcome=result.outcome,
                pnl=result.pnl,
                return_on_nav=result.return_on_nav,
                holding_minutes=result.holding_minutes,
                entry_reference=selected.entry_reference,
                entry_fill=result.entry_fill,
                exit_fill=result.exit_fill,
                stop_price=selected.stop_price,
                target_price=selected.target_price,
                cost_after_reward_risk=selected.cost_after_reward_risk,
                planned_loss=result.planned_loss,
                planned_loss_fraction=config.risk_fraction,
                quantity=result.quantity,
                effective_notional_multiple=result.effective_notional_multiple,
                source_atr=source.atr,
                source_age_bars=source.age_bars,
                source_touches=source.tested_boundary_touches,
                source_efficiency=source.path_efficiency,
                source_span_atr=source.span_atr,
                excursion_atr=setup.excursion_atr,
                test_buy_share=float(week.five_minute_buy_share[setup.test_index]),
                relative_test_volume=float(relative_volume),
                relative_test_trades=float(relative_trades),
                maximum_adverse_excursion_fraction=result.maximum_adverse_excursion_fraction,
                maximum_favorable_excursion_fraction=result.maximum_favorable_excursion_fraction,
            ),
        )
        detector.reset_transient_states()
        busy_until_ns = result.exit_time_ns
        index += 1

    return trades, nav_path, dict(detector.diagnostics)


def aggregate_metrics(
    week_results: Sequence[tuple[str, Sequence[TradeRecord], Sequence[float]]],
) -> dict[str, Any]:
    compound_nav = 1.0
    compound_path = [compound_nav]
    positive_weeks = 0
    nonnegative_weeks = 0
    active_weeks = 0
    positive_active_weeks = 0
    all_trades: list[TradeRecord] = []
    weekly: list[dict[str, Any]] = []
    for week_name, trades, path in week_results:
        values = np.asarray(path, dtype=float)
        if len(values) == 0 or values[0] <= 0.0:
            raise ValueError(f"invalid NAV path for {week_name}")
        week_factor = float(values[-1] / values[0])
        start_nav = compound_nav
        for factor in values[1:] / values[:-1]:
            compound_nav *= float(factor)
            compound_path.append(compound_nav)
        is_active = bool(trades)
        positive_weeks += int(week_factor > 1.0)
        nonnegative_weeks += int(week_factor >= 1.0)
        active_weeks += int(is_active)
        positive_active_weeks += int(is_active and week_factor > 1.0)
        all_trades.extend(trades)
        weekly.append(
            {
                "week": week_name,
                "trades": len(trades),
                "nav_factor": week_factor,
                "return": week_factor - 1.0,
                "compounded_start_nav": start_nav,
                "compounded_end_nav": compound_nav,
            },
        )

    positive_pnls = [trade.pnl for trade in all_trades if trade.pnl > 0.0]
    negative_pnls = [trade.pnl for trade in all_trades if trade.pnl < 0.0]
    gross_profit = float(sum(positive_pnls))
    gross_loss = float(sum(negative_pnls))
    path_array = np.asarray(compound_path, dtype=float)
    running_peak = np.maximum.accumulate(path_array)
    maximum_drawdown = float(-(path_array / running_peak - 1.0).min())
    evaluation_days = 7 * len(week_results)
    consecutive_losses = 0
    maximum_consecutive_losses = 0
    for trade in all_trades:
        if trade.pnl < 0.0:
            consecutive_losses += 1
            maximum_consecutive_losses = max(maximum_consecutive_losses, consecutive_losses)
        else:
            consecutive_losses = 0

    return {
        "evaluation_weeks": len(week_results),
        "evaluation_days": evaluation_days,
        "trades": len(all_trades),
        "positive_trades": len(positive_pnls),
        "negative_trades": len(negative_pnls),
        "positive_trade_fraction": (
            len(positive_pnls) / len(all_trades) if all_trades else 0.0
        ),
        "trades_per_day": len(all_trades) / evaluation_days if evaluation_days else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (
            gross_profit / abs(gross_loss)
            if gross_loss < 0.0
            else (math.inf if gross_profit > 0.0 else 0.0)
        ),
        "nav_factor": compound_nav,
        "geometric_daily_growth": (
            compound_nav ** (1.0 / evaluation_days) - 1.0 if evaluation_days else 0.0
        ),
        "maximum_mark_to_market_drawdown": maximum_drawdown,
        "positive_week_fraction": positive_weeks / len(week_results) if week_results else 0.0,
        "nonnegative_week_fraction": (
            nonnegative_weeks / len(week_results) if week_results else 0.0
        ),
        "positive_active_week_fraction": (
            positive_active_weeks / active_weeks if active_weeks else 0.0
        ),
        "active_weeks": active_weeks,
        "maximum_single_winner_share_of_gross_profit": (
            max(positive_pnls, default=0.0) / gross_profit if gross_profit > 0.0 else 0.0
        ),
        "maximum_consecutive_negative_trades": maximum_consecutive_losses,
        "maximum_effective_notional_multiple": max(
            (trade.effective_notional_multiple for trade in all_trades),
            default=0.0,
        ),
        "maximum_planned_loss_to_budget": max(
            (
                trade.planned_loss
                / (trade.planned_loss / trade.planned_loss_fraction)
                / trade.planned_loss_fraction
                for trade in all_trades
                if trade.planned_loss_fraction > 0.0
            ),
            default=0.0,
        ),
        "horizon_counts": dict(Counter(trade.horizon for trade in all_trades)),
        "outcome_counts": dict(Counter(trade.outcome for trade in all_trades)),
        "weekly": weekly,
    }


def _timestamp_unit(values: pd.Series) -> str:
    maximum = int(pd.to_numeric(values, errors="raise").max())
    return "us" if maximum >= 100_000_000_000_000 else "ms"


def read_binance_daily_archive(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}, found {members}")
        raw = archive.read(members[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None)
    if frame.empty:
        raise ValueError(f"empty archive: {path}")
    if str(frame.iloc[0, 0]).strip().lower() in {"open_time", "open time"}:
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.shape[1] < len(BINANCE_COLUMNS):
        raise ValueError(f"unexpected column count in {path}: {frame.shape[1]}")
    frame = frame.iloc[:, : len(BINANCE_COLUMNS)]
    frame.columns = BINANCE_COLUMNS
    for column in BINANCE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    timestamps = pd.to_datetime(
        frame["open_time"].astype("int64"),
        unit=_timestamp_unit(frame["open_time"]),
        utc=True,
    )
    result = frame[
        [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "trade_count",
            "taker_buy_volume",
            "taker_buy_quote_volume",
        ]
    ].astype(float)
    result.index = timestamps
    result.index.name = "open_time_utc"
    return result


def locate_daily_archive(cache_root: Path, symbol: str, day: date) -> Path:
    filename = f"{symbol}-1m-{day.isoformat()}.zip"
    candidates = (
        cache_root / symbol / "daily" / filename,
        cache_root / filename,
        cache_root / "data" / filename,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = list(cache_root.rglob(filename))
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f"could not locate {filename} under {cache_root}")


def load_week_frame(
    *,
    cache_root: Path,
    symbol: str,
    week_start: str,
    config: CandidateV3Config,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    start_day = date.fromisoformat(week_start) - timedelta(days=config.warmup_days)
    end_day = date.fromisoformat(week_start) + timedelta(days=7)
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    day = start_day
    while day <= end_day:
        path = locate_daily_archive(cache_root, symbol, day)
        frames.append(read_binance_daily_archive(path))
        manifest.append(
            {
                "day": day.isoformat(),
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            },
        )
        day += timedelta(days=1)
    frame = pd.concat(frames).sort_index()
    if frame.index.duplicated().any():
        frame = frame[~frame.index.duplicated(keep="first")]
    return frame, manifest


def build_week_data(
    frame: pd.DataFrame,
    week_start: str,
    config: CandidateV3Config,
) -> WeekData:
    evaluation_start = pd.Timestamp(week_start, tz="UTC")
    evaluation_end = evaluation_start + pd.Timedelta(days=7)
    warmup_start = evaluation_start - pd.Timedelta(days=config.warmup_days)
    tail_end = evaluation_end + pd.Timedelta(hours=config.tail_hours)
    one_minute = frame.loc[(frame.index >= warmup_start) & (frame.index < tail_end)].copy()
    if one_minute.empty:
        raise ValueError(f"no one-minute data for {week_start}")
    expected_step = pd.Timedelta(minutes=1)
    gaps = one_minute.index.to_series().diff().dropna()
    if (gaps != expected_step).any():
        bad = gaps[gaps != expected_step].head().to_dict()
        raise ValueError(f"non-contiguous one-minute data for {week_start}: {bad}")
    ohlc_valid = (
        (one_minute["low"] <= one_minute[["open", "close"]].min(axis=1)).all()
        and (one_minute["high"] >= one_minute[["open", "close"]].max(axis=1)).all()
        and (one_minute["high"] >= one_minute["low"]).all()
        and (one_minute["volume"] >= 0.0).all()
        and (one_minute["trade_count"] >= 0.0).all()
        and (one_minute["taker_buy_volume"] >= 0.0).all()
        and (one_minute["taker_buy_volume"] <= one_minute["volume"] + 1e-9).all()
    )
    if not ohlc_valid:
        raise ValueError(f"market-data integrity failure for {week_start}")

    five_minute = one_minute.resample(
        "5min",
        origin="epoch",
        closed="left",
        label="right",
    ).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        trade_count=("trade_count", "sum"),
        taker_buy_volume=("taker_buy_volume", "sum"),
    )
    five_minute.dropna(inplace=True)
    five_minute["buy_share"] = (
        five_minute["taker_buy_volume"] / five_minute["volume"].replace(0.0, np.nan)
    )
    previous_close = five_minute["close"].shift(1)
    true_range = pd.concat(
        [
            five_minute["high"] - five_minute["low"],
            (five_minute["high"] - previous_close).abs(),
            (five_minute["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    prior_atr = true_range.rolling(
        config.atr_lookback_5m,
        min_periods=config.atr_minimum_observations,
    ).median().shift(1)
    rolling_buy_share = five_minute["buy_share"].rolling(
        config.flow_lookback_5m,
        min_periods=config.flow_minimum_observations,
    )
    prior_flow_high = rolling_buy_share.quantile(config.breakout_flow_quantile).shift(1)
    prior_flow_low = rolling_buy_share.quantile(1.0 - config.breakout_flow_quantile).shift(1)
    prior_volume = five_minute["volume"].rolling(
        config.flow_lookback_5m,
        min_periods=config.flow_minimum_observations,
    ).quantile(config.activity_quantile).shift(1)
    prior_trades = five_minute["trade_count"].rolling(
        config.flow_lookback_5m,
        min_periods=config.flow_minimum_observations,
    ).quantile(config.activity_quantile).shift(1)

    return WeekData(
        name=week_start,
        evaluation_start_ns=int(evaluation_start.value),
        evaluation_end_ns=int(evaluation_end.value),
        one_minute_time_ns=one_minute.index.asi8,
        one_minute_open=one_minute["open"].to_numpy(float),
        one_minute_high=one_minute["high"].to_numpy(float),
        one_minute_low=one_minute["low"].to_numpy(float),
        one_minute_close=one_minute["close"].to_numpy(float),
        five_minute_time_ns=five_minute.index.asi8,
        five_minute_open=five_minute["open"].to_numpy(float),
        five_minute_high=five_minute["high"].to_numpy(float),
        five_minute_low=five_minute["low"].to_numpy(float),
        five_minute_close=five_minute["close"].to_numpy(float),
        five_minute_volume=five_minute["volume"].to_numpy(float),
        five_minute_trade_count=five_minute["trade_count"].to_numpy(float),
        five_minute_buy_share=five_minute["buy_share"].to_numpy(float),
        prior_atr=prior_atr.to_numpy(float),
        prior_flow_high=prior_flow_high.to_numpy(float),
        prior_flow_low=prior_flow_low.to_numpy(float),
        prior_volume_threshold=prior_volume.to_numpy(float),
        prior_trade_threshold=prior_trades.to_numpy(float),
    )


def config_from_lock(lock: Mapping[str, Any]) -> tuple[CandidateV3Config, CostModel]:
    implementation = lock["locked_implementation"]
    config_values = dict(implementation["config"])
    config_values["auction_horizons_5m"] = tuple(config_values["auction_horizons_5m"])
    config = CandidateV3Config(**config_values)
    costs = CostModel(**implementation["cost_model"])
    return config, costs


def _json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if math.isnan(number):
            return None
        if math.isinf(number):
            return "Infinity" if number > 0.0 else "-Infinity"
        return number
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_compatible(item) for item in value]
    return str(value)


def evaluate_locked_set(
    *,
    lock_path: Path,
    cache_root: Path,
    set_name: str,
    output: Path,
) -> dict[str, Any]:
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    config, costs = config_from_lock(lock)
    if set_name == "development":
        weeks = lock["research_protocol"]["development_weeks"]
    elif set_name == "holdout":
        weeks = lock["research_protocol"]["prospective_holdout_weeks"]
    else:
        raise ValueError("set_name must be development or holdout")

    output.mkdir(parents=True, exist_ok=True)
    week_results: list[tuple[str, Sequence[TradeRecord], Sequence[float]]] = []
    diagnostics: dict[str, Mapping[str, int]] = {}
    data_manifest: list[dict[str, Any]] = []
    all_trades: list[TradeRecord] = []
    for week_start in weeks:
        frame, files = load_week_frame(
            cache_root=cache_root,
            symbol=lock["market"]["symbol"],
            week_start=week_start,
            config=config,
        )
        week = build_week_data(frame, week_start, config)
        trades, nav_path, week_diagnostics = simulate_week(week, config, costs)
        week_results.append((week_start, trades, nav_path))
        diagnostics[week_start] = week_diagnostics
        all_trades.extend(trades)
        data_manifest.append(
            {
                "week": week_start,
                "first_one_minute_open": pd.Timestamp(week.one_minute_time_ns[0], tz="UTC").isoformat(),
                "last_one_minute_open": pd.Timestamp(week.one_minute_time_ns[-1], tz="UTC").isoformat(),
                "one_minute_rows": len(week.one_minute_time_ns),
                "five_minute_rows": len(week.five_minute_time_ns),
                "files": files,
            },
        )

    metrics = aggregate_metrics(week_results)
    criteria = lock["prospective_pass_criteria"]
    checks = {
        "geometric_daily_growth": metrics["geometric_daily_growth"]
        >= criteria["minimum_after_cost_geometric_daily_growth"],
        "minimum_trades": metrics["trades"] >= criteria["minimum_total_trades"],
        "trades_per_day": metrics["trades_per_day"] >= criteria["minimum_trades_per_day"],
        "positive_trade_fraction": metrics["positive_trade_fraction"]
        >= criteria["minimum_positive_trade_fraction"],
        "profit_factor": metrics["profit_factor"] >= criteria["minimum_profit_factor"],
        "maximum_drawdown": metrics["maximum_mark_to_market_drawdown"]
        <= criteria["maximum_mark_to_market_drawdown"],
        "nonnegative_week_fraction": metrics["nonnegative_week_fraction"]
        >= criteria["minimum_nonnegative_week_fraction"],
        "positive_active_week_fraction": metrics["positive_active_week_fraction"]
        >= criteria["minimum_positive_active_week_fraction"],
        "single_winner_share": metrics["maximum_single_winner_share_of_gross_profit"]
        <= criteria["maximum_single_winner_share_of_gross_profit"],
        "consecutive_losses": metrics["maximum_consecutive_negative_trades"]
        <= criteria["maximum_consecutive_negative_trades"],
    }
    result = {
        "candidate": lock["candidate"],
        "set": set_name,
        "lock_sha256": sha256(lock_bytes).hexdigest(),
        "metrics": metrics,
        "checks": checks,
        "passed": all(checks.values()),
        "diagnostics": diagnostics,
    }
    (output / "metrics.json").write_text(
        json.dumps(_json_compatible(result), indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    pd.DataFrame([asdict(trade) for trade in all_trades]).to_csv(
        output / "trades.csv",
        index=False,
    )
    (output / "data_manifest.json").write_text(
        json.dumps(data_manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=Path(__file__).with_name("locked_rule_v3.json"))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--set", choices=("development", "holdout"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = evaluate_locked_set(
        lock_path=args.lock,
        cache_root=args.cache,
        set_name=args.set,
        output=args.output,
    )
    print(json.dumps(_json_compatible(result), indent=2, sort_keys=True, allow_nan=False))
    return 0 if result["passed"] or args.set == "development" else 2


if __name__ == "__main__":
    raise SystemExit(main())
