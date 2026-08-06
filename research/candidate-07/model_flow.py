"""Aggressor-flow liquidity-state model for candidate-07.

The model identifies a liquidity pool by the timestamp of the bar which formed
the rolling external high or low. A pool is consumed on its first causal sweep;
the same historical pool can never be traded again. This prevents repeated
backtests of already-removed liquidity without any time-based cooldown.

No order, fill, cash, fee, portfolio, or PnL simulation exists in this module.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import sqrt
from statistics import fmean
from typing import Any, Mapping

from model import Direction, ScenarioKind, ScenarioState, TradePlan, Transition


@dataclass(frozen=True, slots=True)
class FlowLogicConfig:
    signal_minutes: int = 5
    atr_period: int = 24
    flow_period: int = 36
    external_lookback: int = 48
    internal_lookback: int = 12
    min_history: int = 52
    sweep_min_atr: float = 0.05
    sweep_max_atr: float = 1.00
    sweep_wick_fraction: float = 0.25
    reclaim_buffer_atr: float = 0.02
    absorption_min_imbalance: float = 0.10
    absorption_flow_z: float = 0.50
    reversal_efficiency_max: float = 0.35
    confirmation_bars: int = 3
    confirmation_body_atr: float = 0.20
    confirmation_min_imbalance: float = 0.02
    stop_buffer_atr: float = 0.10
    minimum_stop_atr: float = 1.00
    maximum_stop_atr: float = 1.60
    minimum_rr: float = 1.50
    maximum_target_rr: float = 3.00
    episode_cooldown_bars: int = 1

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "FlowLogicConfig":
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown flow logic config keys: {unknown}")
        config = cls(**dict(values))
        config.validate()
        return config

    def validate(self) -> None:
        for name in (
            "signal_minutes",
            "atr_period",
            "flow_period",
            "external_lookback",
            "internal_lookback",
            "min_history",
            "confirmation_bars",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.external_lookback <= self.internal_lookback:
            raise ValueError("external_lookback must exceed internal_lookback")
        if self.min_history < max(
            self.atr_period,
            self.flow_period,
            self.external_lookback,
        ):
            raise ValueError("min_history must cover all causal lookbacks")
        if not 0.0 <= self.sweep_wick_fraction <= 1.0:
            raise ValueError("sweep_wick_fraction must be in [0, 1]")
        if not 0.0 < self.absorption_min_imbalance < 1.0:
            raise ValueError("absorption_min_imbalance must be in (0, 1)")
        if not 0.0 <= self.confirmation_min_imbalance < 1.0:
            raise ValueError("confirmation_min_imbalance must be in [0, 1)")
        if not 0.0 < self.reversal_efficiency_max < 1.0:
            raise ValueError("reversal_efficiency_max must be in (0, 1)")
        if self.sweep_max_atr <= self.sweep_min_atr:
            raise ValueError("sweep_max_atr must exceed sweep_min_atr")
        if self.maximum_stop_atr <= self.minimum_stop_atr:
            raise ValueError("maximum_stop_atr must exceed minimum_stop_atr")
        if self.minimum_rr <= 0.0 or self.maximum_target_rr < self.minimum_rr:
            raise ValueError("target R parameters are inconsistent")
        if self.episode_cooldown_bars < 0:
            raise ValueError("episode_cooldown_bars must be non-negative")


@dataclass(frozen=True, slots=True)
class FlowSignalBar:
    ts_event_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float

    def __post_init__(self) -> None:
        if self.ts_event_ns < 0:
            raise ValueError("ts_event_ns must be non-negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.low <= 0.0 or self.volume < 0.0:
            raise ValueError("price must be positive and volume non-negative")
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


@dataclass(slots=True)
class _FlowEpisode:
    scenario_id: str
    direction: Direction
    created_index: int
    liquidity_level: float
    liquidity_formed_ns: int
    extreme: float
    opposing_internal: float
    opposing_external: float
    atr: float
    state: ScenarioState = ScenarioState.CONTACTED


@dataclass(frozen=True, slots=True)
class FlowObservation:
    plan: TradePlan | None
    transitions: tuple[Transition, ...]
    diagnostics: Mapping[str, Any]


class CausalAggressorFlowRouter:
    """Past-only liquidity sweep -> failed aggression -> opposite flow state."""

    def __init__(self, config: FlowLogicConfig):
        config.validate()
        self.config = config
        capacity = max(
            config.min_history,
            config.external_lookback,
            config.flow_period,
        ) + 8
        self._history: deque[FlowSignalBar] = deque(maxlen=capacity)
        self._episode: _FlowEpisode | None = None
        self._episode_counter = 0
        self._cooldown_until = -1
        self._last_ts = -1
        self._consumed_pools: set[tuple[Direction, int]] = set()

    @property
    def active_scenario_id(self) -> str | None:
        return self._episode.scenario_id if self._episode is not None else None

    @property
    def consumed_pool_count(self) -> int:
        return len(self._consumed_pools)

    def observe(
        self,
        bar: FlowSignalBar,
        index: int,
        *,
        eligible: bool = True,
    ) -> FlowObservation:
        if bar.ts_event_ns <= self._last_ts:
            raise ValueError("signal bars must be strictly monotonic")
        self._last_ts = bar.ts_event_ns
        transitions: list[Transition] = []
        diagnostics: dict[str, Any] = {
            "index": index,
            "history": len(self._history),
            "eligible": eligible,
            "imbalance": bar.imbalance,
        }

        if len(self._history) < self.config.min_history:
            self._history.append(bar)
            diagnostics["reason"] = "WARMUP"
            return FlowObservation(None, tuple(), diagnostics)

        atr = self._atr()
        upper, lower, upper_ns, lower_ns = self._external_levels()
        internal_high, internal_low = self._internal_levels()
        flow_z = self._flow_z(abs(bar.delta))
        efficiency, slope = self._trend_state()
        diagnostics.update(
            {
                "atr": atr,
                "upper_liquidity": upper,
                "lower_liquidity": lower,
                "upper_formed_ns": upper_ns,
                "lower_formed_ns": lower_ns,
                "internal_high": internal_high,
                "internal_low": internal_low,
                "aggressor_flow_z": flow_z,
                "trend_efficiency": efficiency,
                "trend_slope": slope,
                "consumed_pool_count": self.consumed_pool_count,
            }
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
                        {},
                    )
                )
                self._episode = None
            self._history.append(bar)
            diagnostics["reason"] = "INELIGIBLE"
            return FlowObservation(None, tuple(transitions), diagnostics)

        plan: TradePlan | None = None
        if self._episode is not None:
            plan, advanced = self._advance_episode(bar, index, atr)
            transitions.extend(advanced)

        if self._episode is None and plan is None and index >= self._cooldown_until:
            episode, contact = self._detect_contact(
                bar=bar,
                index=index,
                atr=atr,
                flow_z=flow_z,
                efficiency=efficiency,
                upper=upper,
                lower=lower,
                upper_ns=upper_ns,
                lower_ns=lower_ns,
                internal_high=internal_high,
                internal_low=internal_low,
            )
            if episode is not None and contact is not None:
                self._episode = episode
                transitions.append(contact)

        self._history.append(bar)
        diagnostics["active_scenario_id"] = self.active_scenario_id
        return FlowObservation(plan, tuple(transitions), diagnostics)

    def _atr(self) -> float:
        bars = list(self._history)[-self.config.atr_period :]
        previous_close: float | None = None
        values: list[float] = []
        for bar in bars:
            if previous_close is None:
                true_range = bar.range
            else:
                true_range = max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            values.append(true_range)
            previous_close = bar.close
        return max(fmean(values), bars[-1].close * 1e-6)

    def _flow_z(self, current_abs_delta: float) -> float:
        values = [
            abs(bar.delta)
            for bar in list(self._history)[-self.config.flow_period :]
        ]
        mean = fmean(values)
        variance = fmean((value - mean) ** 2 for value in values)
        scale = sqrt(variance)
        if scale <= max(mean * 1e-9, 1e-12):
            return 0.0
        return (current_abs_delta - mean) / scale

    def _external_levels(self) -> tuple[float, float, int, int]:
        window = list(self._history)[-self.config.external_lookback :]
        upper_bar = max(window, key=lambda item: item.high)
        lower_bar = min(window, key=lambda item: item.low)
        return (
            upper_bar.high,
            lower_bar.low,
            upper_bar.ts_event_ns,
            lower_bar.ts_event_ns,
        )

    def _internal_levels(self) -> tuple[float, float]:
        window = list(self._history)[-self.config.internal_lookback :]
        return max(bar.high for bar in window), min(bar.low for bar in window)

    def _trend_state(self) -> tuple[float, float]:
        bars = list(self._history)[-self.config.atr_period :]
        closes = [bar.close for bar in bars]
        path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
        displacement = closes[-1] - closes[0]
        efficiency = abs(displacement) / path if path > 0.0 else 0.0
        slope = displacement / max(1, len(closes) - 1)
        return efficiency, slope

    def _detect_contact(
        self,
        *,
        bar: FlowSignalBar,
        index: int,
        atr: float,
        flow_z: float,
        efficiency: float,
        upper: float,
        lower: float,
        upper_ns: int,
        lower_ns: int,
        internal_high: float,
        internal_low: float,
    ) -> tuple[_FlowEpisode | None, Transition | None]:
        if atr <= 0.0 or bar.range <= 0.0:
            return None, None
        if efficiency > self.config.reversal_efficiency_max:
            return None, None

        upper_penetration = (bar.high - upper) / atr
        lower_penetration = (lower - bar.low) / atr
        upper_wick = (bar.high - max(bar.open, bar.close)) / bar.range
        lower_wick = (min(bar.open, bar.close) - bar.low) / bar.range

        high_sweep = (
            self.config.sweep_min_atr <= upper_penetration <= self.config.sweep_max_atr
            and bar.close < upper - self.config.reclaim_buffer_atr * atr
            and upper_wick >= self.config.sweep_wick_fraction
            and bar.imbalance >= self.config.absorption_min_imbalance
            and flow_z >= self.config.absorption_flow_z
        )
        low_sweep = (
            self.config.sweep_min_atr <= lower_penetration <= self.config.sweep_max_atr
            and bar.close > lower + self.config.reclaim_buffer_atr * atr
            and lower_wick >= self.config.sweep_wick_fraction
            and bar.imbalance <= -self.config.absorption_min_imbalance
            and flow_z >= self.config.absorption_flow_z
        )
        if high_sweep and low_sweep:
            return None, None

        if high_sweep:
            return self._new_episode(
                direction=Direction.SHORT,
                index=index,
                bar=bar,
                level=upper,
                formed_ns=upper_ns,
                extreme=bar.high,
                opposing_internal=internal_low,
                opposing_external=lower,
                reason="UPPER_POOL_BUY_AGGRESSION_ABSORBED",
                details={
                    "penetration_atr": upper_penetration,
                    "wick_fraction": upper_wick,
                    "aggressor_imbalance": bar.imbalance,
                    "aggressor_flow_z": flow_z,
                    "trend_efficiency": efficiency,
                },
            )
        if low_sweep:
            return self._new_episode(
                direction=Direction.LONG,
                index=index,
                bar=bar,
                level=lower,
                formed_ns=lower_ns,
                extreme=bar.low,
                opposing_internal=internal_high,
                opposing_external=upper,
                reason="LOWER_POOL_SELL_AGGRESSION_ABSORBED",
                details={
                    "penetration_atr": lower_penetration,
                    "wick_fraction": lower_wick,
                    "aggressor_imbalance": bar.imbalance,
                    "aggressor_flow_z": flow_z,
                    "trend_efficiency": efficiency,
                },
            )
        return None, None

    def _new_episode(
        self,
        *,
        direction: Direction,
        index: int,
        bar: FlowSignalBar,
        level: float,
        formed_ns: int,
        extreme: float,
        opposing_internal: float,
        opposing_external: float,
        reason: str,
        details: Mapping[str, Any],
    ) -> tuple[_FlowEpisode | None, Transition | None]:
        pool_key = (direction, formed_ns)
        if pool_key in self._consumed_pools:
            return None, None
        self._consumed_pools.add(pool_key)
        self._episode_counter += 1
        scenario_id = f"c07f-{bar.ts_event_ns}-{self._episode_counter:06d}"
        episode = _FlowEpisode(
            scenario_id=scenario_id,
            direction=direction,
            created_index=index,
            liquidity_level=level,
            liquidity_formed_ns=formed_ns,
            extreme=extreme,
            opposing_internal=opposing_internal,
            opposing_external=opposing_external,
            atr=self._atr(),
        )
        transition = Transition(
            scenario_id=scenario_id,
            event_type="LIQUIDITY_CONTACT",
            previous_state=ScenarioState.IDLE.value,
            next_state=ScenarioState.CONTACTED.value,
            reason_code=reason,
            event_time_ns=bar.ts_event_ns,
            reference_price=level,
            details={
                **dict(details),
                "liquidity_formed_ns": formed_ns,
                "pool_consumed_on_contact": True,
            },
        )
        return episode, transition

    def _advance_episode(
        self,
        bar: FlowSignalBar,
        index: int,
        atr: float,
    ) -> tuple[TradePlan | None, list[Transition]]:
        episode = self._episode
        if episode is None:
            return None, []
        transitions: list[Transition] = []
        age = index - episode.created_index
        if age > self.config.confirmation_bars:
            transitions.append(
                self._transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "FLOW_CONFIRMATION_TIMEOUT",
                    bar,
                    episode.liquidity_level,
                    {"age_bars": age},
                )
            )
            self._episode = None
            self._cooldown_until = index + self.config.episode_cooldown_bars
            return None, transitions

        body_ok = bar.body >= self.config.confirmation_body_atr * atr
        if episode.direction is Direction.SHORT:
            midpoint = (episode.extreme + episode.liquidity_level) / 2.0
            confirmed = (
                body_ok
                and bar.close < midpoint
                and bar.close < bar.open
                and bar.imbalance <= -self.config.confirmation_min_imbalance
            )
            extreme_accepted = bar.close > episode.extreme
        else:
            midpoint = (episode.extreme + episode.liquidity_level) / 2.0
            confirmed = (
                body_ok
                and bar.close > midpoint
                and bar.close > bar.open
                and bar.imbalance >= self.config.confirmation_min_imbalance
            )
            extreme_accepted = bar.close < episode.extreme

        if not confirmed:
            if extreme_accepted:
                transitions.append(
                    self._transition(
                        episode,
                        ScenarioState.INVALIDATED,
                        "ABSORPTION_EXTREME_ACCEPTED",
                        bar,
                        episode.extreme,
                        {
                            "age_bars": age,
                            "confirmation_imbalance": bar.imbalance,
                        },
                    )
                )
                self._episode = None
                self._cooldown_until = index + self.config.episode_cooldown_bars
            return None, transitions

        transitions.append(
            self._transition(
                episode,
                ScenarioState.CONFIRMED,
                "OPPOSITE_AGGRESSOR_DISPLACEMENT",
                bar,
                episode.liquidity_level,
                {
                    "age_bars": age,
                    "atr": atr,
                    "confirmation_imbalance": bar.imbalance,
                },
            )
        )
        plan = self._build_plan(episode, bar, atr, age)
        if plan is None:
            transitions.append(
                self._transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "UNTRADEABLE_FLOW_GEOMETRY",
                    bar,
                    episode.liquidity_level,
                    {"age_bars": age},
                )
            )
        else:
            transitions.append(
                self._transition(
                    episode,
                    ScenarioState.ENTRY_READY,
                    "CAUSAL_FLOW_ROUTE_READY",
                    bar,
                    plan.entry_reference,
                    {
                        "kind": plan.kind.value,
                        "direction": plan.direction.value,
                        "stop": plan.stop_price,
                        "target": plan.target_price,
                        "expected_rr": plan.expected_rr,
                        "pool_formed_ns": episode.liquidity_formed_ns,
                    },
                )
            )
        self._episode = None
        self._cooldown_until = index + self.config.episode_cooldown_bars
        return plan, transitions

    def _build_plan(
        self,
        episode: _FlowEpisode,
        bar: FlowSignalBar,
        atr: float,
        age: int,
    ) -> TradePlan | None:
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
        risk_atr = risk / atr if atr > 0.0 else 0.0
        if (
            risk <= 0.0
            or risk_atr < self.config.minimum_stop_atr
            or risk_atr > self.config.maximum_stop_atr
        ):
            return None

        candidates: list[float] = []
        for level in (episode.opposing_internal, episode.opposing_external):
            if episode.direction is Direction.LONG and level > entry:
                candidates.append(level)
            elif episode.direction is Direction.SHORT and level < entry:
                candidates.append(level)
        if not candidates:
            return None
        target_level = (
            min(candidates)
            if episode.direction is Direction.LONG
            else max(candidates)
        )
        target_rr = abs(target_level - entry) / risk
        if target_rr < self.config.minimum_rr:
            return None
        target_rr = min(target_rr, self.config.maximum_target_rr)
        target = (
            entry + risk * target_rr
            if episode.direction is Direction.LONG
            else entry - risk * target_rr
        )
        return TradePlan(
            scenario_id=episode.scenario_id,
            kind=ScenarioKind.ABSORPTION_RECLAIM,
            direction=episode.direction,
            observed_time_ns=bar.ts_event_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            liquidity_level=episode.liquidity_level,
            expected_rr=target_rr,
            details={
                "atr": atr,
                "route_age_bars": age,
                "opposing_internal": episode.opposing_internal,
                "opposing_external": episode.opposing_external,
                "pool_formed_ns": episode.liquidity_formed_ns,
                "confirmation_imbalance": bar.imbalance,
            },
        )

    def _transition(
        self,
        episode: _FlowEpisode,
        next_state: ScenarioState,
        reason: str,
        bar: FlowSignalBar,
        reference_price: float,
        details: Mapping[str, Any],
    ) -> Transition:
        previous = episode.state
        episode.state = next_state
        return Transition(
            scenario_id=episode.scenario_id,
            event_type="FLOW_SCENARIO_TRANSITION",
            previous_state=previous.value,
            next_state=next_state.value,
            reason_code=reason,
            event_time_ns=bar.ts_event_ns,
            reference_price=reference_price,
            details=dict(details),
        )


__all__ = [
    "CausalAggressorFlowRouter",
    "FlowLogicConfig",
    "FlowObservation",
    "FlowSignalBar",
]
