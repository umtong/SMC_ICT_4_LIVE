"""Five-minute OI dislocation with one-minute index-price execution.

A completed five-minute signal identifies a tail trade/index deviation with
aligned aggressor flow and a contemporaneous OI impulse. No order is emitted at
that point. Subsequent completed one-minute bars must show contraction of the
same deviation and opposite aggressor flow while fair value remains ahead.
NautilusTrader owns orders, fills, cash, fees, positions, funding and NAV.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from math import sqrt
from statistics import fmean, median
from typing import Any, Mapping

from model import Direction, ScenarioKind, TradePlan, Transition
from model_positioning import InventoryState


class MTFValuationState(str, Enum):
    IDLE = "IDLE"
    DISLOCATION = "DISLOCATION"
    CONFIRMED = "CONFIRMED"
    ENTRY_READY = "ENTRY_READY"
    INVALIDATED = "INVALIDATED"


class MTFDislocationKind(str, Enum):
    INVENTORY_BUILD = "INVENTORY_BUILD"
    INVENTORY_RELEASE = "INVENTORY_RELEASE"
    OI_ABLATION = "OI_ABLATION"


@dataclass(frozen=True, slots=True)
class MTFValuationLogicConfig:
    signal_minutes: int = 5
    signal_flow_period: int = 36
    oi_period: int = 36
    basis_period: int = 288
    min_signal_history: int = 288
    minute_atr_period: int = 60
    minute_flow_period: int = 60
    min_minute_history: int = 60
    basis_tail_rank: float = 0.90
    basis_normal_rank: float = 0.55
    minimum_abs_basis_bps: float = 0.50
    signal_aggression_min_imbalance: float = 0.05
    signal_flow_impulse_z: float = 0.10
    oi_impulse_rank: float = 0.50
    contraction_fraction: float = 0.25
    confirmation_minutes: int = 10
    confirmation_body_atr: float = 0.15
    confirmation_min_imbalance: float = 0.03
    confirmation_flow_z: float = 0.0
    stop_buffer_atr: float = 0.05
    minimum_rr: float = 1.25
    maximum_target_rr: float = 3.0
    rearm_minutes: int = 3
    use_open_interest: bool = True

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "MTFValuationLogicConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown MTF valuation logic config keys: {unknown}")
        config = cls(**dict(values))
        config.validate()
        return config

    def validate(self) -> None:
        for name in (
            "signal_minutes",
            "signal_flow_period",
            "oi_period",
            "basis_period",
            "min_signal_history",
            "minute_atr_period",
            "minute_flow_period",
            "min_minute_history",
            "confirmation_minutes",
            "rearm_minutes",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.min_signal_history < max(
            self.signal_flow_period,
            self.oi_period + 1,
            self.basis_period,
        ):
            raise ValueError("min_signal_history must cover signal lookbacks")
        if self.min_minute_history < max(
            self.minute_atr_period,
            self.minute_flow_period,
        ):
            raise ValueError("min_minute_history must cover minute lookbacks")
        for name in (
            "basis_tail_rank",
            "basis_normal_rank",
            "oi_impulse_rank",
            "contraction_fraction",
        ):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.basis_tail_rank <= self.basis_normal_rank:
            raise ValueError("tail rank must exceed normal rank")
        if self.minimum_abs_basis_bps < 0.0:
            raise ValueError("minimum_abs_basis_bps must be non-negative")
        for name in (
            "signal_aggression_min_imbalance",
            "confirmation_min_imbalance",
        ):
            if not 0.0 <= getattr(self, name) < 1.0:
                raise ValueError(f"{name} must be in [0, 1)")
        if self.stop_buffer_atr < 0.0:
            raise ValueError("stop_buffer_atr must be non-negative")
        if self.minimum_rr <= 0.0 or self.maximum_target_rr < self.minimum_rr:
            raise ValueError("target R parameters are inconsistent")


@dataclass(frozen=True, slots=True)
class ValuationMinuteBar:
    ts_event_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    index_open: float
    index_high: float
    index_low: float
    index_close: float

    def __post_init__(self) -> None:
        if self.ts_event_ns < 0:
            raise ValueError("ts_event_ns must be non-negative")
        if self.low <= 0.0 or self.index_low <= 0.0 or self.volume < 0.0:
            raise ValueError("prices must be positive and volume non-negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("trade OHLC is inconsistent")
        if (
            self.index_high < max(self.index_open, self.index_close)
            or self.index_low > min(self.index_open, self.index_close)
        ):
            raise ValueError("index OHLC is inconsistent")
        tolerance = max(1e-9, self.volume * 1e-9)
        if self.taker_buy_volume < -tolerance or self.taker_buy_volume > self.volume + tolerance:
            raise ValueError("taker_buy_volume must lie inside total volume")

    @property
    def range(self) -> float:
        return max(0.0, self.high - self.low)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def taker_sell_volume(self) -> float:
        return max(0.0, self.volume - self.taker_buy_volume)

    @property
    def delta(self) -> float:
        return self.taker_buy_volume - self.taker_sell_volume

    @property
    def imbalance(self) -> float:
        return self.delta / self.volume if self.volume > 0.0 else 0.0

    @property
    def basis(self) -> float:
        return (self.close - self.index_close) / self.index_close


@dataclass(frozen=True, slots=True)
class ValuationSignalBar:
    ts_event_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    index_close: float
    open_interest: float
    open_interest_value: float
    top_trader_account_ratio: float | None = None
    top_trader_position_ratio: float | None = None
    global_long_short_ratio: float | None = None
    taker_long_short_ratio: float | None = None

    def __post_init__(self) -> None:
        if self.ts_event_ns < 0:
            raise ValueError("ts_event_ns must be non-negative")
        if self.low <= 0.0 or self.index_close <= 0.0 or self.volume < 0.0:
            raise ValueError("prices must be positive and volume non-negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("signal OHLC is inconsistent")
        if self.open_interest <= 0.0 or self.open_interest_value <= 0.0:
            raise ValueError("open-interest fields must be positive")
        tolerance = max(1e-9, self.volume * 1e-9)
        if self.taker_buy_volume < -tolerance or self.taker_buy_volume > self.volume + tolerance:
            raise ValueError("taker_buy_volume must lie inside total volume")

    @property
    def taker_sell_volume(self) -> float:
        return max(0.0, self.volume - self.taker_buy_volume)

    @property
    def delta(self) -> float:
        return self.taker_buy_volume - self.taker_sell_volume

    @property
    def imbalance(self) -> float:
        return self.delta / self.volume if self.volume > 0.0 else 0.0

    @property
    def basis(self) -> float:
        return (self.close - self.index_close) / self.index_close


@dataclass(slots=True)
class _MTFEpisode:
    scenario_id: str
    created_signal_index: int
    created_minute_index: int
    created_ns: int
    sign: int
    direction: Direction
    kind: MTFDislocationKind
    initial_basis: float
    extreme_basis: float
    initial_trade_price: float
    initial_index_price: float
    median_basis: float
    initial_oi_change: float
    initial_oi_rank: float
    initial_signal_flow_z: float
    state: MTFValuationState = MTFValuationState.DISLOCATION


@dataclass(frozen=True, slots=True)
class MTFValuationObservation:
    plan: TradePlan | None
    transitions: tuple[Transition, ...]
    diagnostics: Mapping[str, Any]


class MTFValuationDislocationRouter:
    """Five-minute dislocation selection and one-minute causal execution."""

    NS_PER_MINUTE = 60_000_000_000

    def __init__(self, config: MTFValuationLogicConfig):
        config.validate()
        self.config = config
        signal_capacity = max(
            config.signal_flow_period,
            config.oi_period + 1,
            config.basis_period,
        ) + 8
        minute_capacity = max(
            config.minute_atr_period,
            config.minute_flow_period,
        ) + 8
        self._signal_history: deque[ValuationSignalBar] = deque(maxlen=signal_capacity)
        self._minute_history: deque[ValuationMinuteBar] = deque(maxlen=minute_capacity)
        self._episode: _MTFEpisode | None = None
        self._episode_counter = 0
        self._episode_count = 0
        self._last_signal_ts = -1
        self._last_minute_ts = -1
        self._needs_normalization = True
        self._rearm_after_minute = -1

    @property
    def active_scenario_id(self) -> str | None:
        return self._episode.scenario_id if self._episode else None

    @property
    def consumed_pool_count(self) -> int:
        return self._episode_count

    def observe_signal(
        self,
        bar: ValuationSignalBar,
        signal_index: int,
        minute_index: int,
        *,
        eligible: bool = True,
    ) -> MTFValuationObservation:
        if bar.ts_event_ns <= self._last_signal_ts:
            raise ValueError("signal bars must be strictly monotonic")
        self._last_signal_ts = bar.ts_event_ns
        diagnostics: dict[str, Any] = {
            "clock": "SIGNAL",
            "signal_index": signal_index,
            "minute_index": minute_index,
            "history": len(self._signal_history),
            "eligible": eligible,
        }
        transitions: list[Transition] = []
        if len(self._signal_history) < self.config.min_signal_history:
            self._signal_history.append(bar)
            self._needs_normalization = True
            diagnostics["reason"] = "SIGNAL_WARMUP"
            return MTFValuationObservation(None, tuple(), diagnostics)

        basis_rank, median_basis = self._basis_stats(bar.basis)
        flow_z = self._signal_flow_z(abs(bar.delta))
        oi_change, oi_rank, inventory_state = self._inventory_state(bar)
        diagnostics.update(
            {
                "basis": bar.basis,
                "basis_bps": bar.basis * 10_000.0,
                "absolute_basis_rank": basis_rank,
                "median_basis": median_basis,
                "median_basis_bps": median_basis * 10_000.0,
                "signal_imbalance": bar.imbalance,
                "signal_flow_z": flow_z,
                "oi_change_fraction": oi_change,
                "oi_impulse_rank": oi_rank,
                "inventory_state": inventory_state.value,
                "needs_normalization": self._needs_normalization,
                "use_open_interest": self.config.use_open_interest,
            }
        )

        if not eligible:
            self._needs_normalization = True
            if self._episode is not None:
                transitions.append(
                    self._terminal(
                        self._episode,
                        MTFValuationState.INVALIDATED,
                        "SIGNAL_ELIGIBILITY_LOST",
                        bar.ts_event_ns,
                        bar.close,
                        {},
                    )
                )
                self._finish(minute_index)
            self._signal_history.append(bar)
            diagnostics["reason"] = "SIGNAL_INELIGIBLE"
            return MTFValuationObservation(None, tuple(transitions), diagnostics)

        if self._episode is None and basis_rank <= self.config.basis_normal_rank:
            self._needs_normalization = False

        if (
            self._episode is None
            and minute_index >= self._rearm_after_minute
            and not self._needs_normalization
        ):
            episode, transition = self._detect_signal(
                bar=bar,
                signal_index=signal_index,
                minute_index=minute_index,
                basis_rank=basis_rank,
                median_basis=median_basis,
                flow_z=flow_z,
                oi_change=oi_change,
                oi_rank=oi_rank,
                inventory_state=inventory_state,
            )
            if episode is not None and transition is not None:
                self._episode = episode
                self._episode_count += 1
                transitions.append(transition)

        self._signal_history.append(bar)
        diagnostics["active_scenario_id"] = self.active_scenario_id
        return MTFValuationObservation(None, tuple(transitions), diagnostics)

    def observe_minute(
        self,
        bar: ValuationMinuteBar,
        minute_index: int,
        *,
        eligible: bool = True,
    ) -> MTFValuationObservation:
        if bar.ts_event_ns <= self._last_minute_ts:
            raise ValueError("minute bars must be strictly monotonic")
        self._last_minute_ts = bar.ts_event_ns
        diagnostics: dict[str, Any] = {
            "clock": "EXECUTION",
            "minute_index": minute_index,
            "history": len(self._minute_history),
            "eligible": eligible,
            "basis": bar.basis,
            "basis_bps": bar.basis * 10_000.0,
            "imbalance": bar.imbalance,
        }
        transitions: list[Transition] = []
        if len(self._minute_history) < self.config.min_minute_history:
            self._minute_history.append(bar)
            diagnostics["reason"] = "MINUTE_WARMUP"
            return MTFValuationObservation(None, tuple(), diagnostics)

        atr = self._minute_atr()
        flow_z = self._minute_flow_z(abs(bar.delta))
        diagnostics.update({"minute_atr": atr, "minute_flow_z": flow_z})

        if not eligible:
            self._needs_normalization = True
            if self._episode is not None:
                transitions.append(
                    self._terminal(
                        self._episode,
                        MTFValuationState.INVALIDATED,
                        "EXECUTION_ELIGIBILITY_LOST",
                        bar.ts_event_ns,
                        bar.close,
                        {},
                    )
                )
                self._finish(minute_index)
            self._minute_history.append(bar)
            diagnostics["reason"] = "EXECUTION_INELIGIBLE"
            return MTFValuationObservation(None, tuple(transitions), diagnostics)

        plan: TradePlan | None = None
        if self._episode is not None:
            plan, advanced, geometry = self._advance_minute(
                bar=bar,
                minute_index=minute_index,
                atr=atr,
                flow_z=flow_z,
            )
            transitions.extend(advanced)
            diagnostics.update(geometry)

        self._minute_history.append(bar)
        diagnostics["active_scenario_id"] = self.active_scenario_id
        return MTFValuationObservation(plan, tuple(transitions), diagnostics)

    def invalidate_data_gap(
        self,
        *,
        minute_index: int,
        event_time_ns: int,
        reference_price: float,
        reason_code: str,
    ) -> tuple[Transition, ...]:
        if minute_index < 0 or event_time_ns < 0 or reference_price <= 0.0:
            raise ValueError("gap invalidation arguments are inconsistent")
        self._needs_normalization = True
        self._rearm_after_minute = max(
            self._rearm_after_minute,
            minute_index + self.config.rearm_minutes,
        )
        if self._episode is None:
            return tuple()
        episode = self._episode
        transition = Transition(
            scenario_id=episode.scenario_id,
            event_type="MTF_VALUATION_TRANSITION",
            previous_state=episode.state.value,
            next_state=MTFValuationState.INVALIDATED.value,
            reason_code=reason_code,
            event_time_ns=event_time_ns,
            reference_price=reference_price,
            details={
                "dislocation_kind": episode.kind.value,
                "data_gap": True,
                "synthetic_index_used": False,
                "synthetic_positioning_used": False,
                "forward_fill_used": False,
                "interpolation_used": False,
            },
        )
        self._episode = None
        return (transition,)

    def _basis_stats(self, current_basis: float) -> tuple[float, float]:
        values = [
            item.basis
            for item in list(self._signal_history)[-self.config.basis_period :]
        ]
        absolute = [abs(value) for value in values]
        magnitude = abs(current_basis)
        less = sum(value < magnitude for value in absolute)
        equal = sum(value == magnitude for value in absolute)
        rank = (less + 0.5 * equal) / len(absolute)
        return rank, float(median(values))

    def _signal_flow_z(self, current_abs_delta: float) -> float:
        values = [
            abs(item.delta)
            for item in list(self._signal_history)[-self.config.signal_flow_period :]
        ]
        return self._z(current_abs_delta, values)

    def _minute_flow_z(self, current_abs_delta: float) -> float:
        values = [
            abs(item.delta)
            for item in list(self._minute_history)[-self.config.minute_flow_period :]
        ]
        return self._z(current_abs_delta, values)

    @staticmethod
    def _z(current: float, values: list[float]) -> float:
        mean = fmean(values)
        variance = fmean((value - mean) ** 2 for value in values)
        scale = sqrt(variance)
        if scale <= max(mean * 1e-9, 1e-12):
            if mean <= 1e-12:
                return 1.0 if current > 0.0 else 0.0
            return max(0.0, current / mean - 1.0)
        return (current - mean) / scale

    def _inventory_state(
        self,
        bar: ValuationSignalBar,
    ) -> tuple[float, float, InventoryState]:
        expected_ns = self.config.signal_minutes * self.NS_PER_MINUTE
        previous_bar = self._signal_history[-1]
        if bar.ts_event_ns - previous_bar.ts_event_ns != expected_ns:
            return 0.0, 0.0, InventoryState.NEUTRAL
        previous = previous_bar.open_interest
        change = (bar.open_interest - previous) / previous
        bars = list(self._signal_history)[-(self.config.oi_period + 1) :]
        prior_changes = [
            (right.open_interest - left.open_interest) / left.open_interest
            for left, right in zip(bars, bars[1:])
            if (
                left.open_interest > 0.0
                and right.ts_event_ns - left.ts_event_ns == expected_ns
            )
        ]
        magnitudes = [abs(value) for value in prior_changes]
        rank = (
            sum(value <= abs(change) for value in magnitudes) / len(magnitudes)
            if magnitudes
            else 0.0
        )
        impulse = rank >= self.config.oi_impulse_rank and abs(change) > 0.0
        if not impulse:
            return change, rank, InventoryState.NEUTRAL
        return (
            change,
            rank,
            InventoryState.BUILD if change > 0.0 else InventoryState.RELEASE,
        )

    def _minute_atr(self) -> float:
        bars = list(self._minute_history)[-self.config.minute_atr_period :]
        values: list[float] = []
        previous_close: float | None = None
        for item in bars:
            if previous_close is None:
                true_range = item.range
            else:
                true_range = max(
                    item.high - item.low,
                    abs(item.high - previous_close),
                    abs(item.low - previous_close),
                )
            values.append(true_range)
            previous_close = item.close
        return max(fmean(values), bars[-1].close * 1e-6)

    def _detect_signal(
        self,
        *,
        bar: ValuationSignalBar,
        signal_index: int,
        minute_index: int,
        basis_rank: float,
        median_basis: float,
        flow_z: float,
        oi_change: float,
        oi_rank: float,
        inventory_state: InventoryState,
    ) -> tuple[_MTFEpisode | None, Transition | None]:
        sign = 1 if bar.basis > 0.0 else -1 if bar.basis < 0.0 else 0
        aligned = (
            bar.imbalance >= self.config.signal_aggression_min_imbalance
            if sign > 0
            else bar.imbalance <= -self.config.signal_aggression_min_imbalance
        )
        minimum = self.config.minimum_abs_basis_bps / 10_000.0
        qualified = (
            sign != 0
            and abs(bar.basis) >= minimum
            and basis_rank >= self.config.basis_tail_rank
            and aligned
            and flow_z >= self.config.signal_flow_impulse_z
            and (
                not self.config.use_open_interest
                or inventory_state is not InventoryState.NEUTRAL
            )
        )
        if not qualified:
            return None, None

        kind = (
            MTFDislocationKind.OI_ABLATION
            if not self.config.use_open_interest
            else MTFDislocationKind.INVENTORY_BUILD
            if inventory_state is InventoryState.BUILD
            else MTFDislocationKind.INVENTORY_RELEASE
        )
        self._episode_counter += 1
        scenario_id = f"c07m-{bar.ts_event_ns}-{self._episode_counter:06d}"
        episode = _MTFEpisode(
            scenario_id=scenario_id,
            created_signal_index=signal_index,
            created_minute_index=minute_index,
            created_ns=bar.ts_event_ns,
            sign=sign,
            direction=Direction.SHORT if sign > 0 else Direction.LONG,
            kind=kind,
            initial_basis=bar.basis,
            extreme_basis=bar.basis,
            initial_trade_price=bar.close,
            initial_index_price=bar.index_close,
            median_basis=median_basis,
            initial_oi_change=oi_change,
            initial_oi_rank=oi_rank,
            initial_signal_flow_z=flow_z,
        )
        transition = Transition(
            scenario_id=scenario_id,
            event_type="MTF_VALUATION_TRANSITION",
            previous_state=MTFValuationState.IDLE.value,
            next_state=MTFValuationState.DISLOCATION.value,
            reason_code="FIVE_MINUTE_INDEX_BASIS_TAIL",
            event_time_ns=bar.ts_event_ns,
            reference_price=bar.index_close,
            details={
                "direction": episode.direction.value,
                "dislocation_kind": kind.value,
                "basis": bar.basis,
                "basis_bps": bar.basis * 10_000.0,
                "absolute_basis_rank": basis_rank,
                "median_basis": median_basis,
                "median_basis_bps": median_basis * 10_000.0,
                "trade_price": bar.close,
                "index_price": bar.index_close,
                "signal_imbalance": bar.imbalance,
                "signal_flow_z": flow_z,
                "inventory_state": inventory_state.value,
                "oi_change_fraction": oi_change,
                "oi_impulse_rank": oi_rank,
            },
        )
        return episode, transition

    def _advance_minute(
        self,
        *,
        bar: ValuationMinuteBar,
        minute_index: int,
        atr: float,
        flow_z: float,
    ) -> tuple[TradePlan | None, list[Transition], dict[str, Any]]:
        episode = self._episode
        if episode is None:
            return None, [], {}
        age = minute_index - episode.created_minute_index
        geometry: dict[str, Any] = {
            "execution_age_minutes": age,
            "episode_initial_basis_bps": episode.initial_basis * 10_000.0,
            "current_minute_basis_bps": bar.basis * 10_000.0,
            "episode_median_basis_bps": episode.median_basis * 10_000.0,
        }
        if age <= 0:
            return None, [], geometry
        if age > self.config.confirmation_minutes:
            transition = self._terminal(
                episode,
                MTFValuationState.INVALIDATED,
                "ONE_MINUTE_CONTRACTION_TIMEOUT",
                bar.ts_event_ns,
                bar.close,
                geometry,
            )
            self._finish(minute_index)
            return None, [transition], geometry

        target_reached = (
            bar.basis <= episode.median_basis
            if episode.sign > 0
            else bar.basis >= episode.median_basis
        )
        if target_reached:
            transition = self._terminal(
                episode,
                MTFValuationState.INVALIDATED,
                "FAIR_VALUE_REACHED_BEFORE_ENTRY",
                bar.ts_event_ns,
                bar.index_close * (1.0 + episode.median_basis),
                geometry,
            )
            self._finish(minute_index)
            return None, [transition], geometry

        same_sign = bar.basis * episode.sign > 0.0
        if same_sign and abs(bar.basis) > abs(episode.extreme_basis):
            episode.extreme_basis = bar.basis
        contraction = (
            1.0 - abs(bar.basis) / abs(episode.extreme_basis)
            if same_sign
            else 1.0 + abs(bar.basis) / abs(episode.extreme_basis)
        )
        geometry["contraction_fraction"] = contraction
        body_ok = bar.body >= self.config.confirmation_body_atr * atr
        if episode.sign > 0:
            counterflow = (
                bar.imbalance <= -self.config.confirmation_min_imbalance
                and bar.close < bar.open
            )
            price_reversed = bar.close < episode.initial_trade_price
        else:
            counterflow = (
                bar.imbalance >= self.config.confirmation_min_imbalance
                and bar.close > bar.open
            )
            price_reversed = bar.close > episode.initial_trade_price
        confirmed = (
            contraction >= self.config.contraction_fraction
            and body_ok
            and counterflow
            and flow_z >= self.config.confirmation_flow_z
            and price_reversed
        )
        if not confirmed:
            return None, [], geometry

        fair_value = bar.index_close * (1.0 + episode.median_basis)
        buffer = self.config.stop_buffer_atr * atr
        if episode.direction is Direction.SHORT:
            stop = bar.high + buffer
            risk = stop - bar.close
            reward = bar.close - fair_value
        else:
            stop = bar.low - buffer
            risk = bar.close - stop
            reward = fair_value - bar.close
        uncapped_rr = reward / risk if risk > 0.0 else -1.0
        geometry.update(
            {
                "geometry_reason": (
                    "NONPOSITIVE_RISK"
                    if risk <= 0.0
                    else "FAIR_VALUE_ALREADY_PASSED"
                    if reward <= 0.0
                    else "REMAINING_RR_BELOW_MINIMUM"
                    if uncapped_rr < self.config.minimum_rr
                    else "ACCEPTED"
                ),
                "entry": bar.close,
                "confirmation_high": bar.high,
                "confirmation_low": bar.low,
                "stop": stop,
                "risk": risk,
                "reward_to_fair_value": reward,
                "fair_value_target": fair_value,
                "uncapped_target_rr": uncapped_rr,
                "minute_atr": atr,
                "minute_flow_z": flow_z,
            }
        )
        if (
            risk <= 0.0
            or reward <= 0.0
            or uncapped_rr < self.config.minimum_rr
        ):
            return None, [], geometry

        previous = episode.state
        episode.state = MTFValuationState.CONFIRMED
        transitions = [
            Transition(
                scenario_id=episode.scenario_id,
                event_type="MTF_VALUATION_TRANSITION",
                previous_state=previous.value,
                next_state=MTFValuationState.CONFIRMED.value,
                reason_code="ONE_MINUTE_BASIS_CONTRACTION_COUNTERFLOW",
                event_time_ns=bar.ts_event_ns,
                reference_price=bar.index_close,
                details={
                    "direction": episode.direction.value,
                    "dislocation_kind": episode.kind.value,
                    **geometry,
                },
            )
        ]
        target_rr = min(uncapped_rr, self.config.maximum_target_rr)
        target = (
            bar.close - risk * target_rr
            if episode.direction is Direction.SHORT
            else bar.close + risk * target_rr
        )
        plan = TradePlan(
            scenario_id=episode.scenario_id,
            kind=ScenarioKind.ABSORPTION_RECLAIM,
            direction=episode.direction,
            observed_time_ns=bar.ts_event_ns,
            entry_reference=bar.close,
            stop_price=stop,
            target_price=target,
            liquidity_level=bar.index_close,
            expected_rr=target_rr,
            details={
                "atr": atr,
                "route_age_bars": age,
                "execution_clock": "ONE_MINUTE",
                "signal_clock": "FIVE_MINUTE",
                "dislocation_kind": episode.kind.value,
                "initial_basis": episode.initial_basis,
                "extreme_basis": episode.extreme_basis,
                "current_basis": bar.basis,
                "contraction_fraction": contraction,
                "initial_trade_price": episode.initial_trade_price,
                "initial_index_price": episode.initial_index_price,
                "confirmation_index_price": bar.index_close,
                "median_basis": episode.median_basis,
                "fair_value_target": fair_value,
                "uncapped_target_rr": uncapped_rr,
                "initial_oi_change_fraction": episode.initial_oi_change,
                "initial_oi_impulse_rank": episode.initial_oi_rank,
                "initial_signal_flow_z": episode.initial_signal_flow_z,
                "use_open_interest": self.config.use_open_interest,
            },
        )
        previous = episode.state
        episode.state = MTFValuationState.ENTRY_READY
        transitions.append(
            Transition(
                scenario_id=episode.scenario_id,
                event_type="MTF_VALUATION_TRANSITION",
                previous_state=previous.value,
                next_state=MTFValuationState.ENTRY_READY.value,
                reason_code="ONE_MINUTE_VALUATION_ROUTE_READY",
                event_time_ns=bar.ts_event_ns,
                reference_price=plan.entry_reference,
                details={
                    "direction": plan.direction.value,
                    "dislocation_kind": episode.kind.value,
                    "stop": plan.stop_price,
                    "target": plan.target_price,
                    "expected_rr": plan.expected_rr,
                    **geometry,
                },
            )
        )
        self._finish(minute_index)
        return plan, transitions, geometry

    def _terminal(
        self,
        episode: _MTFEpisode,
        next_state: MTFValuationState,
        reason: str,
        event_time_ns: int,
        reference_price: float,
        details: Mapping[str, Any],
    ) -> Transition:
        previous = episode.state
        episode.state = next_state
        return Transition(
            scenario_id=episode.scenario_id,
            event_type="MTF_VALUATION_TRANSITION",
            previous_state=previous.value,
            next_state=next_state.value,
            reason_code=reason,
            event_time_ns=event_time_ns,
            reference_price=reference_price,
            details={
                "dislocation_kind": episode.kind.value,
                **dict(details),
            },
        )

    def _finish(self, minute_index: int) -> None:
        self._episode = None
        self._needs_normalization = True
        self._rearm_after_minute = max(
            self._rearm_after_minute,
            minute_index + self.config.rearm_minutes,
        )


__all__ = [
    "MTFDislocationKind",
    "MTFValuationDislocationRouter",
    "MTFValuationLogicConfig",
    "MTFValuationObservation",
    "MTFValuationState",
    "ValuationMinuteBar",
    "ValuationSignalBar",
]
