"""Positioning-aware causal liquidity auction model for candidate-07.

This module classifies a first contact with previously formed external liquidity
using completed price, aggressor-flow and open-interest observations. It emits
trade plans only; NautilusTrader remains responsible for orders, fills, cash,
fees, funding, positions and NAV.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from math import sqrt
from statistics import fmean
from typing import Any, Mapping

from model import Direction, ScenarioKind, ScenarioState, TradePlan, Transition


class InventoryState(str, Enum):
    BUILD = "BUILD"
    RELEASE = "RELEASE"
    NEUTRAL = "NEUTRAL"


class AuctionBranch(str, Enum):
    LIQUIDATION_REVERSAL = "LIQUIDATION_REVERSAL"
    TRAPPED_POSITION_REVERSAL = "TRAPPED_POSITION_REVERSAL"
    INVENTORY_ACCEPTANCE = "INVENTORY_ACCEPTANCE"
    COVERING_RETEST = "COVERING_RETEST"
    REJECTION_ABLATION = "REJECTION_ABLATION"
    ACCEPTANCE_ABLATION = "ACCEPTANCE_ABLATION"


@dataclass(frozen=True, slots=True)
class PositioningLogicConfig:
    signal_minutes: int = 5
    atr_period: int = 24
    flow_period: int = 36
    oi_period: int = 36
    external_lookback: int = 48
    internal_lookback: int = 12
    target_lookback: int = 288
    target_pivot_radius: int = 2
    min_history: int = 288
    sweep_min_atr: float = 0.05
    sweep_max_atr: float = 1.00
    sweep_wick_fraction: float = 0.20
    reclaim_buffer_atr: float = 0.02
    acceptance_buffer_atr: float = 0.05
    acceptance_close_location: float = 0.65
    aggression_min_imbalance: float = 0.08
    flow_impulse_z: float = 0.25
    oi_impulse_rank: float = 0.50
    reversal_efficiency_max: float = 0.45
    acceptance_efficiency_min: float = 0.12
    confirmation_bars: int = 3
    covering_retest_bars: int = 6
    confirmation_body_atr: float = 0.15
    confirmation_min_imbalance: float = 0.02
    covering_mitigation_atr: float = 0.10
    stop_buffer_atr: float = 0.10
    maximum_stop_atr: float = 1.80
    minimum_rr: float = 1.25
    maximum_target_rr: float = 3.00
    episode_cooldown_bars: int = 1
    use_open_interest: bool = True

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "PositioningLogicConfig":
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown positioning logic config keys: {unknown}")
        config = cls(**dict(values))
        config.validate()
        return config

    def validate(self) -> None:
        for name in (
            "signal_minutes",
            "atr_period",
            "flow_period",
            "oi_period",
            "external_lookback",
            "internal_lookback",
            "target_lookback",
            "target_pivot_radius",
            "min_history",
            "confirmation_bars",
            "covering_retest_bars",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.external_lookback <= self.internal_lookback:
            raise ValueError("external_lookback must exceed internal_lookback")
        required_history = max(
            self.atr_period,
            self.flow_period + 1,
            self.oi_period + 1,
            self.external_lookback,
            self.target_lookback,
        )
        if self.min_history < required_history:
            raise ValueError("min_history must cover all causal lookbacks")
        if self.target_lookback <= self.external_lookback:
            raise ValueError("target_lookback must exceed external_lookback")
        if self.target_lookback <= self.target_pivot_radius * 2:
            raise ValueError("target_lookback is too short for the pivot radius")
        for name in (
            "sweep_wick_fraction",
            "acceptance_close_location",
            "oi_impulse_rank",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.acceptance_close_location < 0.5:
            raise ValueError("acceptance_close_location must be at least 0.5")
        if not 0.0 < self.aggression_min_imbalance < 1.0:
            raise ValueError("aggression_min_imbalance must be in (0, 1)")
        if not 0.0 <= self.confirmation_min_imbalance < 1.0:
            raise ValueError("confirmation_min_imbalance must be in [0, 1)")
        if self.sweep_max_atr <= self.sweep_min_atr:
            raise ValueError("sweep_max_atr must exceed sweep_min_atr")
        if not 0.0 < self.reversal_efficiency_max <= 1.0:
            raise ValueError("reversal_efficiency_max must be in (0, 1]")
        if not 0.0 <= self.acceptance_efficiency_min < 1.0:
            raise ValueError("acceptance_efficiency_min must be in [0, 1)")
        if self.maximum_stop_atr <= 0.0:
            raise ValueError("maximum_stop_atr must be positive")
        if self.minimum_rr <= 0.0 or self.maximum_target_rr < self.minimum_rr:
            raise ValueError("target R parameters are inconsistent")
        if self.episode_cooldown_bars < 0:
            raise ValueError("episode_cooldown_bars must be non-negative")


@dataclass(frozen=True, slots=True)
class PositioningSignalBar:
    ts_event_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    open_interest: float
    open_interest_value: float
    top_trader_account_ratio: float | None = None
    top_trader_position_ratio: float | None = None
    global_long_short_ratio: float | None = None
    taker_long_short_ratio: float | None = None

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
        if self.open_interest <= 0.0 or self.open_interest_value <= 0.0:
            raise ValueError("open interest fields must be positive")

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
class _PositioningEpisode:
    scenario_id: str
    branch: AuctionBranch
    direction: Direction
    created_index: int
    liquidity_level: float
    liquidity_formed_ns: int
    extreme: float
    opposing_internal: float
    opposing_external: float
    directional_targets: tuple[float, ...]
    atr: float
    contact_inventory_state: InventoryState
    contact_oi_change: float
    contact_oi_rank: float
    contact_flow_z: float
    mitigated: bool = False
    state: ScenarioState = ScenarioState.CONTACTED


@dataclass(frozen=True, slots=True)
class PositioningObservation:
    plan: TradePlan | None
    transitions: tuple[Transition, ...]
    diagnostics: Mapping[str, Any]


class PositioningAuctionRouter:
    """First external-liquidity contact routed by inventory formation or release."""

    def __init__(self, config: PositioningLogicConfig):
        config.validate()
        self.config = config
        capacity = config.target_lookback + config.target_pivot_radius * 2 + 16
        self._history: deque[PositioningSignalBar] = deque(maxlen=capacity)
        self._episode: _PositioningEpisode | None = None
        self._episode_counter = 0
        self._cooldown_until = -1
        self._last_ts = -1
        self._consumed_pools: set[tuple[str, int]] = set()

    @property
    def active_scenario_id(self) -> str | None:
        return self._episode.scenario_id if self._episode is not None else None

    @property
    def consumed_pool_count(self) -> int:
        return len(self._consumed_pools)

    def observe(
        self,
        bar: PositioningSignalBar,
        index: int,
        *,
        eligible: bool = True,
    ) -> PositioningObservation:
        if bar.ts_event_ns <= self._last_ts:
            raise ValueError("signal bars must be strictly monotonic")
        self._last_ts = bar.ts_event_ns
        transitions: list[Transition] = []
        diagnostics: dict[str, Any] = {
            "index": index,
            "history": len(self._history),
            "eligible": eligible,
            "imbalance": bar.imbalance,
            "open_interest": bar.open_interest,
        }

        if len(self._history) < self.config.min_history:
            self._history.append(bar)
            diagnostics["reason"] = "WARMUP"
            return PositioningObservation(None, tuple(), diagnostics)

        atr = self._atr()
        upper, lower, upper_ns, lower_ns = self._external_levels()
        internal_high, internal_low = self._internal_levels()
        flow_z = self._flow_z(abs(bar.delta))
        efficiency, slope = self._trend_state()
        oi_change, oi_rank, inventory_state = self._inventory_state(bar)
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
                "oi_change_fraction": oi_change,
                "oi_impulse_rank": oi_rank,
                "inventory_state": inventory_state.value,
                "use_open_interest": self.config.use_open_interest,
                "consumed_pool_count": self.consumed_pool_count,
                "top_trader_account_ratio": bar.top_trader_account_ratio,
                "top_trader_position_ratio": bar.top_trader_position_ratio,
                "global_long_short_ratio": bar.global_long_short_ratio,
                "taker_long_short_ratio": bar.taker_long_short_ratio,
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
            return PositioningObservation(None, tuple(transitions), diagnostics)

        plan: TradePlan | None = None
        if self._episode is not None:
            plan, advanced = self._advance_episode(
                bar=bar,
                index=index,
                atr=atr,
                inventory_state=inventory_state,
                oi_change=oi_change,
                oi_rank=oi_rank,
            )
            transitions.extend(advanced)

        if self._episode is None and plan is None and index >= self._cooldown_until:
            episode, contact = self._detect_contact(
                bar=bar,
                index=index,
                atr=atr,
                flow_z=flow_z,
                efficiency=efficiency,
                inventory_state=inventory_state,
                oi_change=oi_change,
                oi_rank=oi_rank,
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
        return PositioningObservation(plan, tuple(transitions), diagnostics)

    def _atr(self) -> float:
        bars = list(self._history)[-self.config.atr_period :]
        values: list[float] = []
        previous_close: float | None = None
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
        values = [abs(item.delta) for item in list(self._history)[-self.config.flow_period :]]
        mean = fmean(values)
        variance = fmean((value - mean) ** 2 for value in values)
        scale = sqrt(variance)
        if scale <= max(mean * 1e-9, 1e-12):
            if mean <= 1e-12:
                return 1.0 if current_abs_delta > 0.0 else 0.0
            return max(0.0, current_abs_delta / mean - 1.0)
        return (current_abs_delta - mean) / scale

    def _inventory_state(
        self,
        bar: PositioningSignalBar,
    ) -> tuple[float, float, InventoryState]:
        previous = self._history[-1].open_interest
        change = (bar.open_interest - previous) / previous
        bars = list(self._history)[-(self.config.oi_period + 1) :]
        prior_changes = [
            (right.open_interest - left.open_interest) / left.open_interest
            for left, right in zip(bars, bars[1:])
            if left.open_interest > 0.0
        ]
        magnitudes = [abs(value) for value in prior_changes]
        if not magnitudes:
            rank = 0.0
        else:
            rank = sum(value <= abs(change) for value in magnitudes) / len(magnitudes)
        impulse = rank >= self.config.oi_impulse_rank and abs(change) > 0.0
        if not impulse:
            return change, rank, InventoryState.NEUTRAL
        return (
            change,
            rank,
            InventoryState.BUILD if change > 0.0 else InventoryState.RELEASE,
        )

    def _external_levels(self) -> tuple[float, float, int, int]:
        window = list(self._history)[-self.config.external_lookback :]
        upper_bar = max(window, key=lambda item: item.high)
        lower_bar = min(window, key=lambda item: item.low)
        return upper_bar.high, lower_bar.low, upper_bar.ts_event_ns, lower_bar.ts_event_ns

    def _internal_levels(self) -> tuple[float, float]:
        window = list(self._history)[-self.config.internal_lookback :]
        return max(item.high for item in window), min(item.low for item in window)

    def _trend_state(self) -> tuple[float, float]:
        bars = list(self._history)[-self.config.atr_period :]
        closes = [item.close for item in bars]
        path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
        displacement = closes[-1] - closes[0]
        efficiency = abs(displacement) / path if path > 0.0 else 0.0
        slope = displacement / max(1, len(closes) - 1)
        return efficiency, slope

    def _directional_targets(
        self,
        direction: Direction,
        reference: float,
    ) -> tuple[float, ...]:
        window = list(self._history)[-self.config.target_lookback :]
        radius = self.config.target_pivot_radius
        candidates: set[float] = set()
        for index in range(radius, len(window) - radius):
            item = window[index]
            neighbors = window[index - radius : index] + window[index + 1 : index + radius + 1]
            if direction is Direction.LONG:
                if item.high > reference and all(item.high >= other.high for other in neighbors):
                    candidates.add(item.high)
            else:
                if item.low < reference and all(item.low <= other.low for other in neighbors):
                    candidates.add(item.low)
        return tuple(sorted(candidates, reverse=direction is Direction.SHORT))

    def _detect_contact(
        self,
        *,
        bar: PositioningSignalBar,
        index: int,
        atr: float,
        flow_z: float,
        efficiency: float,
        inventory_state: InventoryState,
        oi_change: float,
        oi_rank: float,
        upper: float,
        lower: float,
        upper_ns: int,
        lower_ns: int,
        internal_high: float,
        internal_low: float,
    ) -> tuple[_PositioningEpisode | None, Transition | None]:
        if atr <= 0.0 or bar.range <= 0.0:
            return None, None
        upper_penetration = (bar.high - upper) / atr
        lower_penetration = (lower - bar.low) / atr
        upper_wick = (bar.high - max(bar.open, bar.close)) / bar.range
        lower_wick = (min(bar.open, bar.close) - bar.low) / bar.range
        buy_aggression = (
            bar.imbalance >= self.config.aggression_min_imbalance
            and flow_z >= self.config.flow_impulse_z
        )
        sell_aggression = (
            bar.imbalance <= -self.config.aggression_min_imbalance
            and flow_z >= self.config.flow_impulse_z
        )
        upper_contact = self.config.sweep_min_atr <= upper_penetration <= self.config.sweep_max_atr
        lower_contact = self.config.sweep_min_atr <= lower_penetration <= self.config.sweep_max_atr
        upper_rejection = (
            upper_contact
            and buy_aggression
            and efficiency <= self.config.reversal_efficiency_max
            and upper_wick >= self.config.sweep_wick_fraction
            and bar.close < upper - self.config.reclaim_buffer_atr * atr
        )
        lower_rejection = (
            lower_contact
            and sell_aggression
            and efficiency <= self.config.reversal_efficiency_max
            and lower_wick >= self.config.sweep_wick_fraction
            and bar.close > lower + self.config.reclaim_buffer_atr * atr
        )
        contact_efficiency = bar.body / bar.range if bar.range > 0.0 else 0.0
        upper_acceptance = (
            upper_contact
            and buy_aggression
            and contact_efficiency >= self.config.acceptance_efficiency_min
            and bar.close > upper + self.config.acceptance_buffer_atr * atr
            and bar.close_location >= self.config.acceptance_close_location
        )
        lower_acceptance = (
            lower_contact
            and sell_aggression
            and contact_efficiency >= self.config.acceptance_efficiency_min
            and bar.close < lower - self.config.acceptance_buffer_atr * atr
            and bar.close_location <= 1.0 - self.config.acceptance_close_location
        )
        active = [upper_rejection, lower_rejection, upper_acceptance, lower_acceptance]
        if sum(bool(value) for value in active) != 1:
            return None, None

        if upper_rejection:
            if self.config.use_open_interest:
                branch = {
                    InventoryState.RELEASE: AuctionBranch.LIQUIDATION_REVERSAL,
                    InventoryState.BUILD: AuctionBranch.TRAPPED_POSITION_REVERSAL,
                }.get(inventory_state)
            else:
                branch = AuctionBranch.REJECTION_ABLATION
            if branch is None:
                return None, None
            return self._new_episode(
                branch=branch,
                direction=Direction.SHORT,
                index=index,
                bar=bar,
                level=upper,
                formed_ns=upper_ns,
                extreme=bar.high,
                opposing_internal=internal_low,
                opposing_external=lower,
                inventory_state=inventory_state,
                oi_change=oi_change,
                oi_rank=oi_rank,
                flow_z=flow_z,
                reason="UPPER_POOL_BUY_AUCTION_REJECTED",
                details={
                    "penetration_atr": upper_penetration,
                    "wick_fraction": upper_wick,
                    "trend_efficiency": efficiency,
                },
            )
        if lower_rejection:
            if self.config.use_open_interest:
                branch = {
                    InventoryState.RELEASE: AuctionBranch.LIQUIDATION_REVERSAL,
                    InventoryState.BUILD: AuctionBranch.TRAPPED_POSITION_REVERSAL,
                }.get(inventory_state)
            else:
                branch = AuctionBranch.REJECTION_ABLATION
            if branch is None:
                return None, None
            return self._new_episode(
                branch=branch,
                direction=Direction.LONG,
                index=index,
                bar=bar,
                level=lower,
                formed_ns=lower_ns,
                extreme=bar.low,
                opposing_internal=internal_high,
                opposing_external=upper,
                inventory_state=inventory_state,
                oi_change=oi_change,
                oi_rank=oi_rank,
                flow_z=flow_z,
                reason="LOWER_POOL_SELL_AUCTION_REJECTED",
                details={
                    "penetration_atr": lower_penetration,
                    "wick_fraction": lower_wick,
                    "trend_efficiency": efficiency,
                },
            )
        if upper_acceptance:
            if self.config.use_open_interest:
                branch = {
                    InventoryState.BUILD: AuctionBranch.INVENTORY_ACCEPTANCE,
                    InventoryState.RELEASE: AuctionBranch.COVERING_RETEST,
                }.get(inventory_state)
            else:
                branch = AuctionBranch.ACCEPTANCE_ABLATION
            if branch is None:
                return None, None
            return self._new_episode(
                branch=branch,
                direction=Direction.LONG,
                index=index,
                bar=bar,
                level=upper,
                formed_ns=upper_ns,
                extreme=bar.high,
                opposing_internal=internal_low,
                opposing_external=lower,
                inventory_state=inventory_state,
                oi_change=oi_change,
                oi_rank=oi_rank,
                flow_z=flow_z,
                reason="UPPER_POOL_BUY_AUCTION_ACCEPTED",
                details={
                    "penetration_atr": upper_penetration,
                    "close_location": bar.close_location,
                    "trend_efficiency": efficiency,
                    "contact_efficiency": contact_efficiency,
                },
            )
        if self.config.use_open_interest:
            branch = {
                InventoryState.BUILD: AuctionBranch.INVENTORY_ACCEPTANCE,
                InventoryState.RELEASE: AuctionBranch.COVERING_RETEST,
            }.get(inventory_state)
        else:
            branch = AuctionBranch.ACCEPTANCE_ABLATION
        if branch is None:
            return None, None
        return self._new_episode(
            branch=branch,
            direction=Direction.SHORT,
            index=index,
            bar=bar,
            level=lower,
            formed_ns=lower_ns,
            extreme=bar.low,
            opposing_internal=internal_high,
            opposing_external=upper,
            inventory_state=inventory_state,
            oi_change=oi_change,
            oi_rank=oi_rank,
            flow_z=flow_z,
            reason="LOWER_POOL_SELL_AUCTION_ACCEPTED",
            details={
                "penetration_atr": lower_penetration,
                "close_location": bar.close_location,
                "trend_efficiency": efficiency,
                "contact_efficiency": contact_efficiency,
            },
        )

    def _new_episode(
        self,
        *,
        branch: AuctionBranch,
        direction: Direction,
        index: int,
        bar: PositioningSignalBar,
        level: float,
        formed_ns: int,
        extreme: float,
        opposing_internal: float,
        opposing_external: float,
        inventory_state: InventoryState,
        oi_change: float,
        oi_rank: float,
        flow_z: float,
        reason: str,
        details: Mapping[str, Any],
    ) -> tuple[_PositioningEpisode | None, Transition | None]:
        pool_side = "UPPER" if direction is Direction.SHORT and "REJECTED" in reason else None
        if pool_side is None:
            if direction is Direction.LONG and "ACCEPTED" in reason:
                pool_side = "UPPER"
            elif direction is Direction.LONG:
                pool_side = "LOWER"
            else:
                pool_side = "LOWER"
        pool_key = (pool_side, formed_ns)
        if pool_key in self._consumed_pools:
            return None, None
        self._consumed_pools.add(pool_key)
        self._episode_counter += 1
        scenario_id = f"c07p-{bar.ts_event_ns}-{self._episode_counter:06d}"
        episode = _PositioningEpisode(
            scenario_id=scenario_id,
            branch=branch,
            direction=direction,
            created_index=index,
            liquidity_level=level,
            liquidity_formed_ns=formed_ns,
            extreme=extreme,
            opposing_internal=opposing_internal,
            opposing_external=opposing_external,
            directional_targets=self._directional_targets(direction, bar.close),
            atr=self._atr(),
            contact_inventory_state=inventory_state,
            contact_oi_change=oi_change,
            contact_oi_rank=oi_rank,
            contact_flow_z=flow_z,
        )
        transition = Transition(
            scenario_id=scenario_id,
            event_type="POSITIONING_LIQUIDITY_CONTACT",
            previous_state=ScenarioState.IDLE.value,
            next_state=ScenarioState.CONTACTED.value,
            reason_code=reason,
            event_time_ns=bar.ts_event_ns,
            reference_price=level,
            details={
                **dict(details),
                "branch": branch.value,
                "direction": direction.value,
                "liquidity_formed_ns": formed_ns,
                "pool_side": pool_side,
                "pool_consumed_on_contact": True,
                "inventory_state": inventory_state.value,
                "oi_change_fraction": oi_change,
                "oi_impulse_rank": oi_rank,
                "aggressor_imbalance": bar.imbalance,
                "aggressor_flow_z": flow_z,
                "directional_target_count": len(episode.directional_targets),
            },
        )
        return episode, transition

    def _advance_episode(
        self,
        *,
        bar: PositioningSignalBar,
        index: int,
        atr: float,
        inventory_state: InventoryState,
        oi_change: float,
        oi_rank: float,
    ) -> tuple[TradePlan | None, list[Transition]]:
        episode = self._episode
        if episode is None:
            return None, []
        transitions: list[Transition] = []
        age = index - episode.created_index
        timeout = (
            self.config.covering_retest_bars
            if episode.branch is AuctionBranch.COVERING_RETEST
            else self.config.confirmation_bars
        )
        if age > timeout:
            transitions.append(
                self._transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "POSITIONING_CONFIRMATION_TIMEOUT",
                    bar,
                    episode.liquidity_level,
                    {"age_bars": age, "timeout_bars": timeout},
                )
            )
            self._episode = None
            self._cooldown_until = index + self.config.episode_cooldown_bars
            return None, transitions

        reversal = episode.branch in {
            AuctionBranch.LIQUIDATION_REVERSAL,
            AuctionBranch.TRAPPED_POSITION_REVERSAL,
            AuctionBranch.REJECTION_ABLATION,
        }
        if reversal:
            extreme_accepted = (
                bar.close > episode.extreme
                if episode.direction is Direction.SHORT
                else bar.close < episode.extreme
            )
            if extreme_accepted:
                transitions.append(
                    self._transition(
                        episode,
                        ScenarioState.INVALIDATED,
                        "REJECTED_POOL_EXTREME_ACCEPTED",
                        bar,
                        episode.extreme,
                        {"age_bars": age},
                    )
                )
                self._episode = None
                self._cooldown_until = index + self.config.episode_cooldown_bars
                return None, transitions
        else:
            pool_reclaimed = (
                bar.close < episode.liquidity_level - self.config.reclaim_buffer_atr * episode.atr
                if episode.direction is Direction.LONG
                else bar.close > episode.liquidity_level + self.config.reclaim_buffer_atr * episode.atr
            )
            if pool_reclaimed:
                transitions.append(
                    self._transition(
                        episode,
                        ScenarioState.INVALIDATED,
                        "ACCEPTED_POOL_RECLAIMED",
                        bar,
                        episode.liquidity_level,
                        {"age_bars": age},
                    )
                )
                self._episode = None
                self._cooldown_until = index + self.config.episode_cooldown_bars
                return None, transitions

        body_ok = bar.body >= self.config.confirmation_body_atr * atr
        if episode.direction is Direction.LONG:
            directional_flow = bar.imbalance >= self.config.confirmation_min_imbalance
            directional_body = bar.close > bar.open
            inside_reclaim = bar.close > episode.liquidity_level
        else:
            directional_flow = bar.imbalance <= -self.config.confirmation_min_imbalance
            directional_body = bar.close < bar.open
            inside_reclaim = bar.close < episode.liquidity_level

        confirmed = False
        confirmation_reason = ""
        if episode.branch in {
            AuctionBranch.LIQUIDATION_REVERSAL,
            AuctionBranch.REJECTION_ABLATION,
        }:
            confirmed = body_ok and inside_reclaim and directional_flow and directional_body
            if self.config.use_open_interest:
                confirmed = confirmed and inventory_state is not InventoryState.BUILD
            confirmation_reason = "LIQUIDATION_RELEASE_REVERSED" if self.config.use_open_interest else "REJECTION_ABLATION_CONFIRMED"
        elif episode.branch is AuctionBranch.TRAPPED_POSITION_REVERSAL:
            confirmed = (
                body_ok
                and inside_reclaim
                and directional_flow
                and directional_body
                and inventory_state is InventoryState.RELEASE
            )
            confirmation_reason = "TRAPPED_POSITION_UNWOUND"
        elif episode.branch in {
            AuctionBranch.INVENTORY_ACCEPTANCE,
            AuctionBranch.ACCEPTANCE_ABLATION,
        }:
            hold_buffer = self.config.acceptance_buffer_atr * episode.atr
            held_outside = (
                bar.close > episode.liquidity_level + hold_buffer
                if episode.direction is Direction.LONG
                else bar.close < episode.liquidity_level - hold_buffer
            )
            confirmed = body_ok and held_outside and directional_flow and directional_body
            if self.config.use_open_interest:
                confirmed = confirmed and inventory_state is not InventoryState.RELEASE
            confirmation_reason = "NEW_INVENTORY_ACCEPTANCE_HELD" if self.config.use_open_interest else "ACCEPTANCE_ABLATION_CONFIRMED"
        elif episode.branch is AuctionBranch.COVERING_RETEST:
            mitigation_buffer = self.config.covering_mitigation_atr * episode.atr
            if episode.direction is Direction.LONG:
                touched = bar.low <= episode.liquidity_level + mitigation_buffer
                held = bar.close > episode.liquidity_level + self.config.reclaim_buffer_atr * episode.atr
            else:
                touched = bar.high >= episode.liquidity_level - mitigation_buffer
                held = bar.close < episode.liquidity_level - self.config.reclaim_buffer_atr * episode.atr
            episode.mitigated = episode.mitigated or (touched and held)
            confirmed = (
                episode.mitigated
                and body_ok
                and directional_flow
                and directional_body
                and inventory_state is InventoryState.BUILD
            )
            confirmation_reason = "COVERING_BREAK_RETEST_BUILT_NEW_INVENTORY"

        if not confirmed:
            return None, transitions

        transitions.append(
            self._transition(
                episode,
                ScenarioState.CONFIRMED,
                confirmation_reason,
                bar,
                episode.liquidity_level,
                {
                    "age_bars": age,
                    "inventory_state": inventory_state.value,
                    "oi_change_fraction": oi_change,
                    "oi_impulse_rank": oi_rank,
                    "confirmation_imbalance": bar.imbalance,
                    "mitigated": episode.mitigated,
                },
            )
        )
        plan = self._build_plan(episode, bar, atr, age)
        if plan is None:
            transitions.append(
                self._transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "UNTRADEABLE_POSITIONING_GEOMETRY",
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
                    "POSITIONING_AUCTION_ROUTE_READY",
                    bar,
                    plan.entry_reference,
                    {
                        "branch": episode.branch.value,
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

    def _build_plan(
        self,
        episode: _PositioningEpisode,
        bar: PositioningSignalBar,
        atr: float,
        age: int,
    ) -> TradePlan | None:
        entry = bar.close
        reversal = episode.branch in {
            AuctionBranch.LIQUIDATION_REVERSAL,
            AuctionBranch.TRAPPED_POSITION_REVERSAL,
            AuctionBranch.REJECTION_ABLATION,
        }
        buffer = self.config.stop_buffer_atr * atr
        if reversal:
            if episode.direction is Direction.LONG:
                stop = min(episode.extreme, episode.liquidity_level) - buffer
                risk = entry - stop
            else:
                stop = max(episode.extreme, episode.liquidity_level) + buffer
                risk = stop - entry
            labels_and_levels = [
                ("INTERNAL", episode.opposing_internal),
                ("EXTERNAL", episode.opposing_external),
                *[("DIRECTIONAL_PIVOT", level) for level in episode.directional_targets],
            ]
            kind = ScenarioKind.ABSORPTION_RECLAIM
        else:
            if episode.direction is Direction.LONG:
                stop = episode.liquidity_level - buffer
                risk = entry - stop
            else:
                stop = episode.liquidity_level + buffer
                risk = stop - entry
            labels_and_levels = [
                ("DIRECTIONAL_PIVOT", level) for level in episode.directional_targets
            ]
            kind = ScenarioKind.ACCEPTANCE_CONTINUATION
        risk_atr = risk / atr if atr > 0.0 else 0.0
        if risk <= 0.0 or risk_atr > self.config.maximum_stop_atr:
            return None

        ordered: list[tuple[str, float, float]] = []
        seen: set[float] = set()
        for label, level in labels_and_levels:
            if level in seen:
                continue
            seen.add(level)
            favorable = level > entry if episode.direction is Direction.LONG else level < entry
            if favorable:
                ordered.append((label, level, abs(level - entry) / risk))
        ordered.sort(key=lambda item: abs(item[1] - entry))
        selected = next((item for item in ordered if item[2] >= self.config.minimum_rr), None)
        if selected is None:
            return None
        label, target_level, uncapped_rr = selected
        target_rr = min(uncapped_rr, self.config.maximum_target_rr)
        target = (
            entry + risk * target_rr
            if episode.direction is Direction.LONG
            else entry - risk * target_rr
        )
        return TradePlan(
            scenario_id=episode.scenario_id,
            kind=kind,
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
                "branch": episode.branch.value,
                "risk_atr": risk_atr,
                "selected_target_label": label,
                "selected_target_level": target_level,
                "uncapped_target_rr": uncapped_rr,
                "opposing_internal": episode.opposing_internal,
                "opposing_external": episode.opposing_external,
                "pool_formed_ns": episode.liquidity_formed_ns,
                "contact_inventory_state": episode.contact_inventory_state.value,
                "contact_oi_change_fraction": episode.contact_oi_change,
                "contact_oi_impulse_rank": episode.contact_oi_rank,
                "contact_flow_z": episode.contact_flow_z,
                "use_open_interest": self.config.use_open_interest,
            },
        )

    def _transition(
        self,
        episode: _PositioningEpisode,
        next_state: ScenarioState,
        reason: str,
        bar: PositioningSignalBar,
        reference_price: float,
        details: Mapping[str, Any],
    ) -> Transition:
        previous = episode.state
        episode.state = next_state
        return Transition(
            scenario_id=episode.scenario_id,
            event_type="POSITIONING_SCENARIO_TRANSITION",
            previous_state=previous.value,
            next_state=next_state.value,
            reason_code=reason,
            event_time_ns=bar.ts_event_ns,
            reference_price=reference_price,
            details={"branch": episode.branch.value, **dict(details)},
        )


__all__ = [
    "AuctionBranch",
    "InventoryState",
    "PositioningAuctionRouter",
    "PositioningLogicConfig",
    "PositioningObservation",
    "PositioningSignalBar",
]
