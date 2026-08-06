"""Causal market-state model for candidate-07.

This module detects scenario transitions only. It does not simulate orders, cash,
positions, fills, fees, or PnL; those responsibilities remain in NautilusTrader.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from math import sqrt
from statistics import fmean
from typing import Any, Iterable, Mapping


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ScenarioKind(str, Enum):
    ABSORPTION_RECLAIM = "ABSORPTION_RECLAIM"
    ACCEPTANCE_CONTINUATION = "ACCEPTANCE_CONTINUATION"


class ScenarioState(str, Enum):
    IDLE = "IDLE"
    CONTACTED = "CONTACTED"
    CONFIRMED = "CONFIRMED"
    ENTRY_READY = "ENTRY_READY"
    INVALIDATED = "INVALIDATED"
    TERMINAL = "TERMINAL"


@dataclass(frozen=True, slots=True)
class LogicConfig:
    signal_minutes: int = 5
    atr_period: int = 24
    volume_period: int = 36
    external_lookback: int = 48
    internal_lookback: int = 12
    trend_period: int = 24
    min_history: int = 52
    sweep_min_atr: float = 0.04
    sweep_max_atr: float = 0.90
    sweep_wick_fraction: float = 0.30
    sweep_volume_z: float = 0.20
    reclaim_buffer_atr: float = 0.015
    break_min_atr: float = 0.08
    displacement_body_atr: float = 0.34
    displacement_close_location: float = 0.68
    break_volume_z: float = 0.15
    confirmation_bars: int = 3
    reverse_confirm_body_atr: float = 0.18
    continuation_hold_atr: float = 0.06
    stop_buffer_atr: float = 0.10
    minimum_stop_atr: float = 0.28
    maximum_stop_atr: float = 1.80
    minimum_rr: float = 1.25
    continuation_target_rr: float = 2.20
    maximum_target_rr: float = 5.00
    continuation_efficiency_min: float = 0.18
    episode_cooldown_bars: int = 4

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "LogicConfig":
        known = {field_name for field_name in cls.__dataclass_fields__}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown logic config keys: {unknown}")
        config = cls(**dict(values))
        config.validate()
        return config

    def validate(self) -> None:
        positive_ints = (
            "signal_minutes",
            "atr_period",
            "volume_period",
            "external_lookback",
            "internal_lookback",
            "trend_period",
            "min_history",
            "confirmation_bars",
        )
        for name in positive_ints:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.external_lookback <= self.internal_lookback:
            raise ValueError("external_lookback must exceed internal_lookback")
        if self.min_history < max(self.atr_period, self.volume_period, self.external_lookback):
            raise ValueError("min_history must cover all causal lookbacks")
        if not 0.0 <= self.sweep_wick_fraction <= 1.0:
            raise ValueError("sweep_wick_fraction must be in [0, 1]")
        if not 0.5 <= self.displacement_close_location <= 1.0:
            raise ValueError("displacement_close_location must be in [0.5, 1]")
        if self.sweep_max_atr <= self.sweep_min_atr:
            raise ValueError("sweep_max_atr must exceed sweep_min_atr")
        if self.maximum_stop_atr <= self.minimum_stop_atr:
            raise ValueError("maximum_stop_atr must exceed minimum_stop_atr")
        if self.minimum_rr <= 0 or self.continuation_target_rr < self.minimum_rr:
            raise ValueError("target R parameters are inconsistent")
        if self.maximum_target_rr < self.continuation_target_rr:
            raise ValueError("maximum_target_rr must cover continuation_target_rr")


@dataclass(frozen=True, slots=True)
class SignalBar:
    ts_event_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.ts_event_ns < 0:
            raise ValueError("ts_event_ns must be non-negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.low <= 0.0 or self.volume < 0.0:
            raise ValueError("price must be positive and volume non-negative")

    @property
    def range(self) -> float:
        return max(0.0, self.high - self.low)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def close_location(self) -> float:
        if self.range <= 0.0:
            return 0.5
        return (self.close - self.low) / self.range


@dataclass(frozen=True, slots=True)
class Transition:
    scenario_id: str
    event_type: str
    previous_state: str
    next_state: str
    reason_code: str
    event_time_ns: int
    reference_price: float | None
    details: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TradePlan:
    scenario_id: str
    kind: ScenarioKind
    direction: Direction
    observed_time_ns: int
    entry_reference: float
    stop_price: float
    target_price: float
    liquidity_level: float
    expected_rr: float
    details: Mapping[str, Any]


@dataclass(slots=True)
class _Episode:
    scenario_id: str
    kind: ScenarioKind
    direction: Direction
    created_index: int
    created_time_ns: int
    liquidity_level: float
    extreme: float
    opposing_external: float
    opposing_internal: float
    atr: float
    trigger_price: float
    state: ScenarioState = ScenarioState.CONTACTED


@dataclass(frozen=True, slots=True)
class Observation:
    plan: TradePlan | None
    transitions: tuple[Transition, ...]
    diagnostics: Mapping[str, Any]


class CausalLiquidityRouter:
    """Past-only liquidity contact -> branch -> confirmation state machine."""

    def __init__(self, config: LogicConfig):
        config.validate()
        self.config = config
        capacity = max(config.min_history, config.external_lookback, config.volume_period) + 8
        self._history: deque[SignalBar] = deque(maxlen=capacity)
        self._episode: _Episode | None = None
        self._episode_counter = 0
        self._cooldown_until = -1
        self._last_ts = -1

    @property
    def active_scenario_id(self) -> str | None:
        return self._episode.scenario_id if self._episode is not None else None

    @property
    def history_size(self) -> int:
        return len(self._history)

    def reset_episode(self) -> None:
        self._episode = None

    def observe(self, bar: SignalBar, index: int, *, eligible: bool = True) -> Observation:
        if bar.ts_event_ns <= self._last_ts:
            raise ValueError("signal bars must be strictly monotonic")
        self._last_ts = bar.ts_event_ns

        transitions: list[Transition] = []
        diagnostics: dict[str, Any] = {
            "index": index,
            "history": len(self._history),
            "eligible": eligible,
        }

        if len(self._history) < self.config.min_history:
            self._history.append(bar)
            diagnostics["reason"] = "WARMUP"
            return Observation(None, tuple(), diagnostics)

        atr = self._atr()
        upper, lower, upper_ts, lower_ts = self._external_levels()
        internal_high, internal_low = self._internal_levels()
        volume_z = self._volume_z(bar.volume)
        efficiency, slope = self._trend_state()
        diagnostics.update(
            {
                "atr": atr,
                "upper_liquidity": upper,
                "lower_liquidity": lower,
                "upper_formed_ns": upper_ts,
                "lower_formed_ns": lower_ts,
                "internal_high": internal_high,
                "internal_low": internal_low,
                "volume_z": volume_z,
                "trend_efficiency": efficiency,
                "trend_slope": slope,
            },
        )

        if not eligible:
            if self._episode is not None:
                transitions.append(
                    self._transition(
                        self._episode,
                        ScenarioState.INVALIDATED,
                        "ELIGIBILITY_LOST",
                        bar,
                        self._episode.liquidity_level,
                        diagnostics,
                    ),
                )
                self._episode = None
            self._history.append(bar)
            diagnostics["reason"] = "INELIGIBLE"
            return Observation(None, tuple(transitions), diagnostics)

        plan: TradePlan | None = None
        if self._episode is not None:
            plan, episode_transitions = self._advance_episode(bar, index, atr)
            transitions.extend(episode_transitions)

        if self._episode is None and plan is None and index >= self._cooldown_until:
            episode, contact_transition = self._detect_contact(
                bar=bar,
                index=index,
                atr=atr,
                volume_z=volume_z,
                efficiency=efficiency,
                slope=slope,
                upper=upper,
                lower=lower,
                internal_high=internal_high,
                internal_low=internal_low,
            )
            if episode is not None and contact_transition is not None:
                self._episode = episode
                transitions.append(contact_transition)

        self._history.append(bar)
        diagnostics["active_scenario_id"] = self.active_scenario_id
        return Observation(plan, tuple(transitions), diagnostics)

    def _atr(self) -> float:
        bars = list(self._history)[-self.config.atr_period :]
        previous_close: float | None = None
        ranges: list[float] = []
        for bar in bars:
            if previous_close is None:
                true_range = bar.range
            else:
                true_range = max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            ranges.append(true_range)
            previous_close = bar.close
        return max(fmean(ranges), bars[-1].close * 1e-6)

    def _volume_z(self, current_volume: float) -> float:
        values = [bar.volume for bar in list(self._history)[-self.config.volume_period :]]
        mean = fmean(values)
        variance = fmean((value - mean) ** 2 for value in values)
        scale = sqrt(variance)
        if scale <= max(mean * 1e-9, 1e-12):
            return 0.0
        return (current_volume - mean) / scale

    def _external_levels(self) -> tuple[float, float, int, int]:
        window = list(self._history)[-self.config.external_lookback :]
        upper_bar = max(window, key=lambda item: item.high)
        lower_bar = min(window, key=lambda item: item.low)
        return upper_bar.high, lower_bar.low, upper_bar.ts_event_ns, lower_bar.ts_event_ns

    def _internal_levels(self) -> tuple[float, float]:
        window = list(self._history)[-self.config.internal_lookback :]
        return max(bar.high for bar in window), min(bar.low for bar in window)

    def _trend_state(self) -> tuple[float, float]:
        bars = list(self._history)[-self.config.trend_period :]
        closes = [bar.close for bar in bars]
        path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
        displacement = closes[-1] - closes[0]
        efficiency = abs(displacement) / path if path > 0.0 else 0.0
        slope = displacement / max(1, len(closes) - 1)
        return efficiency, slope

    def _detect_contact(
        self,
        *,
        bar: SignalBar,
        index: int,
        atr: float,
        volume_z: float,
        efficiency: float,
        slope: float,
        upper: float,
        lower: float,
        internal_high: float,
        internal_low: float,
    ) -> tuple[_Episode | None, Transition | None]:
        if atr <= 0.0 or bar.range <= 0.0:
            return None, None

        upper_penetration = (bar.high - upper) / atr
        lower_penetration = (lower - bar.low) / atr
        upper_wick = (bar.high - max(bar.open, bar.close)) / bar.range
        lower_wick = (min(bar.open, bar.close) - bar.low) / bar.range
        body_atr = bar.body / atr
        close_location = bar.close_location

        high_sweep = (
            self.config.sweep_min_atr <= upper_penetration <= self.config.sweep_max_atr
            and bar.close < upper - self.config.reclaim_buffer_atr * atr
            and upper_wick >= self.config.sweep_wick_fraction
            and volume_z >= self.config.sweep_volume_z
        )
        low_sweep = (
            self.config.sweep_min_atr <= lower_penetration <= self.config.sweep_max_atr
            and bar.close > lower + self.config.reclaim_buffer_atr * atr
            and lower_wick >= self.config.sweep_wick_fraction
            and volume_z >= self.config.sweep_volume_z
        )
        high_break = (
            bar.close >= upper + self.config.break_min_atr * atr
            and body_atr >= self.config.displacement_body_atr
            and close_location >= self.config.displacement_close_location
            and volume_z >= self.config.break_volume_z
            and efficiency >= self.config.continuation_efficiency_min
            and slope > 0.0
        )
        low_break = (
            bar.close <= lower - self.config.break_min_atr * atr
            and body_atr >= self.config.displacement_body_atr
            and close_location <= 1.0 - self.config.displacement_close_location
            and volume_z >= self.config.break_volume_z
            and efficiency >= self.config.continuation_efficiency_min
            and slope < 0.0
        )

        if high_sweep:
            return self._new_episode(
                kind=ScenarioKind.ABSORPTION_RECLAIM,
                direction=Direction.SHORT,
                index=index,
                bar=bar,
                level=upper,
                extreme=bar.high,
                opposing_external=lower,
                opposing_internal=internal_low,
                trigger_price=internal_low,
                atr=atr,
                reason="UPPER_POOL_SWEEP_RECLAIM",
                details={"penetration_atr": upper_penetration, "wick_fraction": upper_wick, "volume_z": volume_z},
            )
        if low_sweep:
            return self._new_episode(
                kind=ScenarioKind.ABSORPTION_RECLAIM,
                direction=Direction.LONG,
                index=index,
                bar=bar,
                level=lower,
                extreme=bar.low,
                opposing_external=upper,
                opposing_internal=internal_high,
                trigger_price=internal_high,
                atr=atr,
                reason="LOWER_POOL_SWEEP_RECLAIM",
                details={"penetration_atr": lower_penetration, "wick_fraction": lower_wick, "volume_z": volume_z},
            )
        if high_break:
            return self._new_episode(
                kind=ScenarioKind.ACCEPTANCE_CONTINUATION,
                direction=Direction.LONG,
                index=index,
                bar=bar,
                level=upper,
                extreme=bar.high,
                opposing_external=lower,
                opposing_internal=internal_low,
                trigger_price=upper,
                atr=atr,
                reason="UPPER_POOL_ACCEPTED_DISPLACEMENT",
                details={
                    "close_extension_atr": (bar.close - upper) / atr,
                    "body_atr": body_atr,
                    "volume_z": volume_z,
                    "trend_efficiency": efficiency,
                },
            )
        if low_break:
            return self._new_episode(
                kind=ScenarioKind.ACCEPTANCE_CONTINUATION,
                direction=Direction.SHORT,
                index=index,
                bar=bar,
                level=lower,
                extreme=bar.low,
                opposing_external=upper,
                opposing_internal=internal_high,
                trigger_price=lower,
                atr=atr,
                reason="LOWER_POOL_ACCEPTED_DISPLACEMENT",
                details={
                    "close_extension_atr": (lower - bar.close) / atr,
                    "body_atr": body_atr,
                    "volume_z": volume_z,
                    "trend_efficiency": efficiency,
                },
            )
        return None, None

    def _new_episode(
        self,
        *,
        kind: ScenarioKind,
        direction: Direction,
        index: int,
        bar: SignalBar,
        level: float,
        extreme: float,
        opposing_external: float,
        opposing_internal: float,
        trigger_price: float,
        atr: float,
        reason: str,
        details: Mapping[str, Any],
    ) -> tuple[_Episode, Transition]:
        self._episode_counter += 1
        scenario_id = f"c07-{bar.ts_event_ns}-{self._episode_counter:06d}"
        episode = _Episode(
            scenario_id=scenario_id,
            kind=kind,
            direction=direction,
            created_index=index,
            created_time_ns=bar.ts_event_ns,
            liquidity_level=level,
            extreme=extreme,
            opposing_external=opposing_external,
            opposing_internal=opposing_internal,
            atr=atr,
            trigger_price=trigger_price,
        )
        transition = Transition(
            scenario_id=scenario_id,
            event_type="LIQUIDITY_CONTACT",
            previous_state=ScenarioState.IDLE.value,
            next_state=ScenarioState.CONTACTED.value,
            reason_code=reason,
            event_time_ns=bar.ts_event_ns,
            reference_price=level,
            details=dict(details),
        )
        return episode, transition

    def _advance_episode(self, bar: SignalBar, index: int, atr: float) -> tuple[TradePlan | None, list[Transition]]:
        episode = self._episode
        if episode is None:
            return None, []
        transitions: list[Transition] = []
        age = index - episode.created_index
        if age > self.config.confirmation_bars:
            transitions.append(self._transition(episode, ScenarioState.INVALIDATED, "CONFIRMATION_TIMEOUT", bar, episode.liquidity_level, {"age_bars": age}))
            self._episode = None
            self._cooldown_until = index + self.config.episode_cooldown_bars
            return None, transitions

        if episode.kind is ScenarioKind.ABSORPTION_RECLAIM:
            confirmed = self._reversal_confirmed(episode, bar, atr)
            reason = "OPPOSITE_DISPLACEMENT_MSS"
        else:
            confirmed = self._continuation_confirmed(episode, bar, atr)
            reason = "OUTSIDE_ACCEPTANCE_HELD"

        if not confirmed:
            invalid_reason = self._early_invalidation(episode, bar, atr)
            if invalid_reason is not None:
                transitions.append(self._transition(episode, ScenarioState.INVALIDATED, invalid_reason, bar, episode.liquidity_level, {"age_bars": age}))
                self._episode = None
                self._cooldown_until = index + self.config.episode_cooldown_bars
            return None, transitions

        transitions.append(self._transition(episode, ScenarioState.CONFIRMED, reason, bar, episode.liquidity_level, {"age_bars": age, "atr": atr}))
        plan = self._build_plan(episode, bar, atr)
        if plan is None:
            transitions.append(self._transition(episode, ScenarioState.INVALIDATED, "UNTRADEABLE_GEOMETRY", bar, episode.liquidity_level, {"age_bars": age}))
        else:
            transitions.append(
                self._transition(
                    episode,
                    ScenarioState.ENTRY_READY,
                    "CAUSAL_ROUTE_READY",
                    bar,
                    plan.entry_reference,
                    {
                        "kind": plan.kind.value,
                        "direction": plan.direction.value,
                        "stop": plan.stop_price,
                        "target": plan.target_price,
                        "expected_rr": plan.expected_rr,
                    },
                )
            )
        self._episode = None
        self._cooldown_until = index + self.config.episode_cooldown_bars
        return plan, transitions

    def _reversal_confirmed(self, episode: _Episode, bar: SignalBar, atr: float) -> bool:
        body_ok = bar.body >= self.config.reverse_confirm_body_atr * atr
        if episode.direction is Direction.SHORT:
            trigger = min(episode.liquidity_level, (episode.extreme + episode.liquidity_level) / 2.0)
            return body_ok and bar.close < trigger and bar.close < bar.open
        trigger = max(episode.liquidity_level, (episode.extreme + episode.liquidity_level) / 2.0)
        return body_ok and bar.close > trigger and bar.close > bar.open

    def _continuation_confirmed(self, episode: _Episode, bar: SignalBar, atr: float) -> bool:
        tolerance = self.config.continuation_hold_atr * atr
        if episode.direction is Direction.LONG:
            return bar.low >= episode.liquidity_level - tolerance and bar.close > episode.liquidity_level
        return bar.high <= episode.liquidity_level + tolerance and bar.close < episode.liquidity_level

    def _early_invalidation(self, episode: _Episode, bar: SignalBar, atr: float) -> str | None:
        tolerance = self.config.continuation_hold_atr * atr
        if episode.kind is ScenarioKind.ACCEPTANCE_CONTINUATION:
            if episode.direction is Direction.LONG and bar.close < episode.liquidity_level - tolerance:
                return "BREAKOUT_RECLAIMED"
            if episode.direction is Direction.SHORT and bar.close > episode.liquidity_level + tolerance:
                return "BREAKOUT_RECLAIMED"
        else:
            if episode.direction is Direction.SHORT and bar.close > episode.extreme + tolerance:
                return "SWEEP_EXTREME_ACCEPTED"
            if episode.direction is Direction.LONG and bar.close < episode.extreme - tolerance:
                return "SWEEP_EXTREME_ACCEPTED"
        return None

    def _build_plan(self, episode: _Episode, bar: SignalBar, atr: float) -> TradePlan | None:
        entry = bar.close
        buffer = self.config.stop_buffer_atr * atr
        if episode.direction is Direction.LONG:
            raw_stop = min(episode.extreme, episode.liquidity_level) - buffer
            minimum_stop = entry - self.config.minimum_stop_atr * atr
            stop = min(raw_stop, minimum_stop)
            risk = entry - stop
        else:
            raw_stop = max(episode.extreme, episode.liquidity_level) + buffer
            minimum_stop = entry + self.config.minimum_stop_atr * atr
            stop = max(raw_stop, minimum_stop)
            risk = stop - entry
        if risk <= 0.0 or risk > self.config.maximum_stop_atr * atr:
            return None

        if episode.kind is ScenarioKind.ACCEPTANCE_CONTINUATION:
            target_rr = self.config.continuation_target_rr
        else:
            candidates: list[float] = []
            for level in (episode.opposing_internal, episode.opposing_external):
                if episode.direction is Direction.LONG and level > entry:
                    candidates.append(level)
                elif episode.direction is Direction.SHORT and level < entry:
                    candidates.append(level)
            if not candidates:
                return None
            target_level = min(candidates) if episode.direction is Direction.LONG else max(candidates)
            target_rr = abs(target_level - entry) / risk
            if target_rr < self.config.minimum_rr:
                return None
            target_rr = min(target_rr, self.config.maximum_target_rr)

        if target_rr < self.config.minimum_rr:
            return None
        target = entry + risk * target_rr if episode.direction is Direction.LONG else entry - risk * target_rr
        return TradePlan(
            scenario_id=episode.scenario_id,
            kind=episode.kind,
            direction=episode.direction,
            observed_time_ns=bar.ts_event_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            liquidity_level=episode.liquidity_level,
            expected_rr=target_rr,
            details={
                "atr": atr,
                "route_age_bars": 0,
                "opposing_internal": episode.opposing_internal,
                "opposing_external": episode.opposing_external,
            },
        )

    def _transition(
        self,
        episode: _Episode,
        next_state: ScenarioState,
        reason: str,
        bar: SignalBar,
        reference_price: float,
        details: Mapping[str, Any],
    ) -> Transition:
        previous = episode.state
        episode.state = next_state
        return Transition(
            scenario_id=episode.scenario_id,
            event_type="SCENARIO_TRANSITION",
            previous_state=previous.value,
            next_state=next_state.value,
            reason_code=reason,
            event_time_ns=bar.ts_event_ns,
            reference_price=reference_price,
            details=dict(details),
        )


def aggregate_signal_bars(bars: Iterable[SignalBar], group_size: int) -> list[SignalBar]:
    """Aggregate consecutive bars for deterministic unit fixtures and diagnostics."""
    if group_size <= 0:
        raise ValueError("group_size must be positive")
    grouped: list[SignalBar] = []
    bucket: list[SignalBar] = []
    for bar in bars:
        bucket.append(bar)
        if len(bucket) == group_size:
            grouped.append(
                SignalBar(
                    ts_event_ns=bucket[-1].ts_event_ns,
                    open=bucket[0].open,
                    high=max(item.high for item in bucket),
                    low=min(item.low for item in bucket),
                    close=bucket[-1].close,
                    volume=sum(item.volume for item in bucket),
                )
            )
            bucket.clear()
    return grouped
