"""Causal market-structure engine for candidate-02.

The engine consumes completed one-minute bars only.  Every swing point carries
both the market time at which the pivot occurred and the later observation time
at which enough right-hand bars existed to confirm it.  It deliberately does
not know anything about orders or portfolio accounting; NautilusTrader owns
those concerns in :mod:`strategy` and :mod:`backtest`.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal, ROUND_FLOOR
from enum import StrEnum
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence


NS_MINUTE = 60_000_000_000
NS_HOUR = 60 * NS_MINUTE


class PoolSide(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class ScenarioState(StrEnum):
    SCANNING = "SCANNING"
    EXCURSION = "EXCURSION"
    RECLAIMED = "RECLAIMED"
    ARMED = "ARMED"
    SIGNALLED = "SIGNALLED"
    IN_TRADE = "IN_TRADE"
    COOLDOWN = "COOLDOWN"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True, slots=True)
class CandidateConfig:
    """One structural hypothesis, not a parameter-search surface."""

    atr_period: int = 60
    volume_period: int = 60
    internal_left: int = 2
    internal_right: int = 2
    external_left: int = 2
    external_right: int = 2
    external_minutes: int = 5
    block_hours: int = 6
    warmup_bars: int = 240
    pool_max_age_minutes: int = 4_320
    pool_merge_atr: float = 0.12
    sweep_min_atr: float = 0.08
    sweep_max_atr: float = 2.75
    reclaim_bars: int = 4
    acceptance_atr: float = 0.70
    confirmation_bars: int = 12
    displacement_atr: float = 0.72
    displacement_body_fraction: float = 0.58
    displacement_close_fraction: float = 0.27
    displacement_volume_ratio: float = 1.05
    fvg_min_atr: float = 0.025
    retest_bars: int = 18
    stop_buffer_atr: float = 0.12
    min_reward_risk: float = 1.45
    max_hold_bars: int = 180
    cooldown_bars: int = 12
    max_pools: int = 160

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "CandidateConfig":
        if not values:
            return cls()
        allowed = {item.name for item in fields(cls)}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown candidate config keys: {unknown}")
        return cls(**dict(values))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __post_init__(self) -> None:
        positive_ints = (
            "atr_period",
            "volume_period",
            "internal_left",
            "internal_right",
            "external_left",
            "external_right",
            "external_minutes",
            "block_hours",
            "warmup_bars",
            "pool_max_age_minutes",
            "reclaim_bars",
            "confirmation_bars",
            "retest_bars",
            "max_hold_bars",
            "cooldown_bars",
            "max_pools",
        )
        for name in positive_ints:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0.0 < self.displacement_body_fraction <= 1.0:
            raise ValueError("displacement_body_fraction must be in (0, 1]")
        if not 0.0 < self.displacement_close_fraction <= 0.5:
            raise ValueError("displacement_close_fraction must be in (0, 0.5]")
        if self.min_reward_risk <= 0.0:
            raise ValueError("min_reward_risk must be positive")


@dataclass(frozen=True, slots=True)
class MarketBar:
    instrument_id: str
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume)
        if self.ts_ns < 0:
            raise ValueError("ts_ns must be non-negative")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("bar values must be finite")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is inconsistent")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is inconsistent")
        if self.volume < 0.0:
            raise ValueError("volume cannot be negative")

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)


@dataclass(slots=True)
class LiquidityPool:
    pool_id: str
    side: PoolSide
    price: float
    event_time_ns: int
    observed_time_ns: int
    source: str
    strength: int = 1
    active: bool = True
    consumed_time_ns: int | None = None
    consumed_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Pivot:
    side: PoolSide
    price: float
    event_time_ns: int
    observed_time_ns: int


@dataclass(frozen=True, slots=True)
class ImbalanceZone:
    low: float
    high: float
    created_time_ns: int

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2.0


@dataclass(slots=True)
class Scenario:
    scenario_id: str
    state: ScenarioState
    pool_id: str
    swept_side: PoolSide
    trade_side: TradeSide
    level: float
    extreme: float
    structure_level: float
    event_time_ns: int
    observed_time_ns: int
    start_bar_index: int
    state_bar_index: int
    zone: ImbalanceZone | None = None
    displacement_atr: float | None = None
    target_pool_id: str | None = None


@dataclass(frozen=True, slots=True)
class TradeSignal:
    scenario_id: str
    instrument_id: str
    side: TradeSide
    observed_time_ns: int
    entry_reference: float
    stop_price: float
    target_price: float
    target_pool_id: str
    score: float
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def reward_risk(self) -> float:
        risk = abs(self.entry_reference - self.stop_price)
        reward = abs(self.target_price - self.entry_reference)
        return reward / risk if risk > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class Transition:
    scenario_id: str
    instrument_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload["reference_price"] is not None:
            payload["reference_price"] = str(payload["reference_price"])
        return payload


@dataclass(frozen=True, slots=True)
class RiskSizing:
    nav: Decimal
    risk_fraction: Decimal
    risk_budget: Decimal
    per_unit_expected_loss: Decimal
    raw_quantity: Decimal
    quantity: Decimal
    planned_loss: Decimal
    entry_notional: Decimal
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            name: (str(value) if isinstance(value, Decimal) else value)
            for name, value in asdict(self).items()
        }


def size_by_planned_loss(
    *,
    nav: Decimal,
    risk_fraction: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    entry_fee_rate: Decimal,
    stop_fee_rate: Decimal,
    entry_slippage_rate: Decimal,
    stop_slippage_rate: Decimal,
    market_impact_rate: Decimal,
    funding_rate_allowance: Decimal,
    quantity_step: Decimal,
    minimum_quantity: Decimal = Decimal("0"),
    minimum_notional: Decimal = Decimal("0"),
) -> RiskSizing:
    """Apply the project's NAV loss-budget equation exactly.

    No maximum notional or leverage multiplier is introduced.  Exchange minimums
    are enforced because an order smaller than them cannot be submitted.
    """

    values = {
        "nav": nav,
        "risk_fraction": risk_fraction,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "entry_fee_rate": entry_fee_rate,
        "stop_fee_rate": stop_fee_rate,
        "entry_slippage_rate": entry_slippage_rate,
        "stop_slippage_rate": stop_slippage_rate,
        "market_impact_rate": market_impact_rate,
        "funding_rate_allowance": funding_rate_allowance,
        "quantity_step": quantity_step,
        "minimum_quantity": minimum_quantity,
        "minimum_notional": minimum_notional,
    }
    for name, value in values.items():
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError(f"{name} must be a finite Decimal")
    if nav <= 0 or entry_price <= 0 or stop_price <= 0:
        raise ValueError("nav and prices must be positive")
    if not Decimal("0") < risk_fraction < Decimal("1"):
        raise ValueError("risk_fraction must be in (0, 1)")
    if quantity_step <= 0:
        raise ValueError("quantity_step must be positive")
    for name in (
        "entry_fee_rate",
        "stop_fee_rate",
        "entry_slippage_rate",
        "stop_slippage_rate",
        "market_impact_rate",
        "funding_rate_allowance",
        "minimum_quantity",
        "minimum_notional",
    ):
        if values[name] < 0:
            raise ValueError(f"{name} cannot be negative")

    risk_budget = nav * risk_fraction
    price_loss = abs(entry_price - stop_price)
    entry_cost = entry_price * (entry_fee_rate + entry_slippage_rate + market_impact_rate)
    stop_cost = stop_price * (stop_fee_rate + stop_slippage_rate + market_impact_rate)
    funding_cost = entry_price * funding_rate_allowance
    per_unit = price_loss + entry_cost + stop_cost + funding_cost
    if per_unit <= 0:
        raise ValueError("per-unit expected loss must be positive")

    raw = risk_budget / per_unit
    steps = (raw / quantity_step).to_integral_value(rounding=ROUND_FLOOR)
    quantity = steps * quantity_step
    reason: str | None = None
    if quantity <= 0:
        reason = "ROUNDED_TO_ZERO"
    elif quantity < minimum_quantity:
        reason = "BELOW_MINIMUM_QUANTITY"
    elif quantity * entry_price < minimum_notional:
        reason = "BELOW_MINIMUM_NOTIONAL"

    if reason is not None:
        quantity = Decimal("0")
    planned_loss = quantity * per_unit
    return RiskSizing(
        nav=nav,
        risk_fraction=risk_fraction,
        risk_budget=risk_budget,
        per_unit_expected_loss=per_unit,
        raw_quantity=raw,
        quantity=quantity,
        planned_loss=planned_loss,
        entry_notional=quantity * entry_price,
        skipped_reason=reason,
    )


class LiquidityCascadeEngine:
    """State machine for one instrument.

    Causal sequence:

    confirmed external liquidity -> finite excursion -> close reclaim -> opposite
    displacement through a previously known internal swing -> directional FVG ->
    rejection on FVG retest -> target the nearest opposing external liquidity.
    """

    def __init__(self, instrument_id: str, config: CandidateConfig | None = None) -> None:
        self.instrument_id = instrument_id
        self.config = config or CandidateConfig()
        history_size = max(
            self.config.warmup_bars + 20,
            self.config.atr_period + 20,
            self.config.volume_period + 20,
            500,
        )
        self.bars: deque[MarketBar] = deque(maxlen=history_size)
        self.external_bars: deque[MarketBar] = deque(maxlen=500)
        self._tr_values: deque[float] = deque(maxlen=self.config.atr_period)
        self._volumes: deque[float] = deque(maxlen=self.config.volume_period)
        self._external_tr: deque[float] = deque(maxlen=max(24, self.config.atr_period // 5))
        self.pools: list[LiquidityPool] = []
        self.internal_highs: deque[Pivot] = deque(maxlen=80)
        self.internal_lows: deque[Pivot] = deque(maxlen=80)
        self.transitions: list[Transition] = []
        self.diagnostics: Counter[str] = Counter()
        self.scenario: Scenario | None = None
        self.bar_index = -1
        self._last_ts_ns = -1
        self._scenario_sequence = 0
        self._pool_sequence = 0
        self._cooldown_until_index = -1
        self._external_accumulator: list[MarketBar] = []
        self._block_accumulator: list[MarketBar] = []
        self._last_external_bucket: int | None = None
        self._last_block_bucket: int | None = None

    @property
    def atr(self) -> float | None:
        return statistics.median(self._tr_values) if len(self._tr_values) >= self.config.atr_period else None

    @property
    def external_atr(self) -> float | None:
        minimum = max(6, self.config.external_left + self.config.external_right + 1)
        return statistics.median(self._external_tr) if len(self._external_tr) >= minimum else self.atr

    @property
    def volume_median(self) -> float | None:
        if len(self._volumes) < self.config.volume_period:
            return None
        return statistics.median(self._volumes)

    def on_bar(self, bar: MarketBar) -> TradeSignal | None:
        if bar.instrument_id != self.instrument_id:
            raise ValueError(f"expected {self.instrument_id}, got {bar.instrument_id}")
        if bar.ts_ns <= self._last_ts_ns:
            raise ValueError("bars must have strictly increasing close timestamps")

        previous = self.bars[-1] if self.bars else None
        true_range = bar.range if previous is None else max(
            bar.range,
            abs(bar.high - previous.close),
            abs(bar.low - previous.close),
        )
        # Ratios for the current bar use only prior observations.
        prior_atr = self.atr
        prior_volume_median = self.volume_median

        self.bar_index += 1
        self._last_ts_ns = bar.ts_ns
        self.bars.append(bar)
        self._aggregate_external(bar)
        self._aggregate_block(bar)
        self._confirm_internal_pivots()

        signal: TradeSignal | None = None
        if prior_atr is not None and prior_atr > 0.0 and prior_volume_median is not None:
            if self.scenario is None:
                if self.bar_index >= self.config.warmup_bars and self.bar_index > self._cooldown_until_index:
                    self._scan_for_excursion(bar, previous, prior_atr)
            else:
                signal = self._advance_scenario(bar, prior_atr, prior_volume_median)

        # Prune only after the current bar had a chance to establish or advance
        # a reclaim scenario.  Otherwise an outside close could erase the pool
        # before the finite-excursion state machine sees it.
        self._prune_pools(bar, prior_atr)
        self._tr_values.append(true_range)
        self._volumes.append(bar.volume)
        return signal

    def notify_entry_filled(self, observed_time_ns: int, fill_price: float) -> None:
        scenario = self.scenario
        if scenario is None or scenario.state is not ScenarioState.SIGNALLED:
            return
        self._transition(
            scenario,
            ScenarioState.IN_TRADE,
            reason="ENTRY_FILLED",
            event_time_ns=observed_time_ns,
            observed_time_ns=observed_time_ns,
            reference_price=fill_price,
        )

    def notify_entry_failed(self, observed_time_ns: int, reason: str) -> None:
        scenario = self.scenario
        if scenario is None or scenario.state not in {ScenarioState.SIGNALLED, ScenarioState.ARMED}:
            return
        self._invalidate(scenario, reason, observed_time_ns, scenario.level)

    def notify_trade_closed(
        self,
        observed_time_ns: int,
        *,
        exit_price: float,
        outcome: str,
    ) -> None:
        scenario = self.scenario
        if scenario is None or scenario.state not in {ScenarioState.SIGNALLED, ScenarioState.IN_TRADE}:
            return
        self._transition(
            scenario,
            ScenarioState.COOLDOWN,
            reason=f"TRADE_CLOSED_{outcome}",
            event_time_ns=observed_time_ns,
            observed_time_ns=observed_time_ns,
            reference_price=exit_price,
        )
        self._cooldown_until_index = self.bar_index + self.config.cooldown_bars
        self.scenario = None

    def force_reset(self, observed_time_ns: int, reason: str = "FORCED_RESET") -> None:
        if self.scenario is None:
            return
        self._invalidate(self.scenario, reason, observed_time_ns, self.scenario.level)

    def state_snapshot(self) -> dict[str, Any]:
        active_pools = Counter(pool.side.value for pool in self.pools if pool.active)
        return {
            "instrument_id": self.instrument_id,
            "bar_index": self.bar_index,
            "last_ts_ns": self._last_ts_ns,
            "atr": self.atr,
            "external_atr": self.external_atr,
            "active_pools": dict(active_pools),
            "scenario": asdict(self.scenario) if self.scenario is not None else None,
            "diagnostics": dict(self.diagnostics),
        }

    def drain_transitions(self) -> list[Transition]:
        result = self.transitions[:]
        self.transitions.clear()
        return result

    def _aggregate_external(self, bar: MarketBar) -> None:
        minutes = self.config.external_minutes
        close_minute = bar.ts_ns // NS_MINUTE
        bucket = (close_minute - 1) // minutes
        if self._last_external_bucket is None:
            self._last_external_bucket = bucket
        if bucket != self._last_external_bucket:
            self._finalize_external()
            self._last_external_bucket = bucket
        self._external_accumulator.append(bar)
        if close_minute % minutes == 0:
            self._finalize_external()
            self._last_external_bucket = bucket + 1

    def _finalize_external(self) -> None:
        if not self._external_accumulator:
            return
        values = self._external_accumulator
        aggregate = MarketBar(
            instrument_id=self.instrument_id,
            ts_ns=values[-1].ts_ns,
            open=values[0].open,
            high=max(item.high for item in values),
            low=min(item.low for item in values),
            close=values[-1].close,
            volume=sum(item.volume for item in values),
        )
        previous = self.external_bars[-1] if self.external_bars else None
        tr = aggregate.range if previous is None else max(
            aggregate.range,
            abs(aggregate.high - previous.close),
            abs(aggregate.low - previous.close),
        )
        self.external_bars.append(aggregate)
        self._external_tr.append(tr)
        self._external_accumulator = []
        self._confirm_external_pivots()

    def _aggregate_block(self, bar: MarketBar) -> None:
        block_ns = self.config.block_hours * NS_HOUR
        bucket = (bar.ts_ns - 1) // block_ns
        if self._last_block_bucket is None:
            self._last_block_bucket = bucket
        if bucket != self._last_block_bucket:
            self._finalize_block(observed_time_ns=bar.ts_ns)
            self._last_block_bucket = bucket
        self._block_accumulator.append(bar)
        if bar.ts_ns % block_ns == 0:
            self._finalize_block(observed_time_ns=bar.ts_ns)
            self._last_block_bucket = bucket + 1

    def _finalize_block(self, observed_time_ns: int) -> None:
        if not self._block_accumulator:
            return
        values = self._block_accumulator
        high_bar = max(values, key=lambda item: item.high)
        low_bar = min(values, key=lambda item: item.low)
        atr = self.external_atr or self.atr
        self._add_pool(
            side=PoolSide.HIGH,
            price=high_bar.high,
            event_time_ns=high_bar.ts_ns,
            observed_time_ns=observed_time_ns,
            source=f"BLOCK_{self.config.block_hours}H",
            atr=atr,
        )
        self._add_pool(
            side=PoolSide.LOW,
            price=low_bar.low,
            event_time_ns=low_bar.ts_ns,
            observed_time_ns=observed_time_ns,
            source=f"BLOCK_{self.config.block_hours}H",
            atr=atr,
        )
        self._block_accumulator = []

    def _confirm_internal_pivots(self) -> None:
        window = self.config.internal_left + self.config.internal_right + 1
        if len(self.bars) < window:
            return
        values = list(self.bars)
        center_index = len(values) - 1 - self.config.internal_right
        center = values[center_index]
        left = values[center_index - self.config.internal_left : center_index]
        right = values[center_index + 1 : center_index + 1 + self.config.internal_right]
        if all(center.high > item.high for item in (*left, *right)):
            self.internal_highs.append(
                Pivot(PoolSide.HIGH, center.high, center.ts_ns, values[-1].ts_ns),
            )
        if all(center.low < item.low for item in (*left, *right)):
            self.internal_lows.append(
                Pivot(PoolSide.LOW, center.low, center.ts_ns, values[-1].ts_ns),
            )

    def _confirm_external_pivots(self) -> None:
        window = self.config.external_left + self.config.external_right + 1
        if len(self.external_bars) < window:
            return
        values = list(self.external_bars)
        center_index = len(values) - 1 - self.config.external_right
        center = values[center_index]
        left = values[center_index - self.config.external_left : center_index]
        right = values[center_index + 1 : center_index + 1 + self.config.external_right]
        observed = values[-1].ts_ns
        atr = self.external_atr
        if all(center.high > item.high for item in (*left, *right)):
            self._add_pool(
                PoolSide.HIGH,
                center.high,
                center.ts_ns,
                observed,
                "SWING_5M",
                atr,
            )
        if all(center.low < item.low for item in (*left, *right)):
            self._add_pool(
                PoolSide.LOW,
                center.low,
                center.ts_ns,
                observed,
                "SWING_5M",
                atr,
            )

    def _add_pool(
        self,
        side: PoolSide,
        price: float,
        event_time_ns: int,
        observed_time_ns: int,
        source: str,
        atr: float | None,
    ) -> None:
        if not math.isfinite(price) or price <= 0.0:
            return
        merge_distance = (atr or 0.0) * self.config.pool_merge_atr
        candidates = [
            pool
            for pool in self.pools
            if pool.active
            and pool.side is side
            and abs(pool.price - price) <= merge_distance
        ]
        if candidates and merge_distance > 0.0:
            pool = min(candidates, key=lambda item: abs(item.price - price))
            pool.strength += 1
            if side is PoolSide.HIGH:
                pool.price = max(pool.price, price)
            else:
                pool.price = min(pool.price, price)
            pool.observed_time_ns = max(pool.observed_time_ns, observed_time_ns)
            pool.source = f"{pool.source}+{source}"
            self.diagnostics["POOL_MERGED"] += 1
            return

        self._pool_sequence += 1
        self.pools.append(
            LiquidityPool(
                pool_id=f"{self.instrument_id}-pool-{self._pool_sequence:06d}",
                side=side,
                price=price,
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                source=source,
            ),
        )
        self.diagnostics[f"POOL_CREATED_{side.value}"] += 1
        if len(self.pools) > self.config.max_pools:
            inactive = [pool for pool in self.pools if not pool.active]
            if inactive:
                remove_ids = {pool.pool_id for pool in inactive[: len(self.pools) - self.config.max_pools]}
                self.pools = [pool for pool in self.pools if pool.pool_id not in remove_ids]
            if len(self.pools) > self.config.max_pools:
                self.pools = self.pools[-self.config.max_pools :]

    def _prune_pools(self, bar: MarketBar, atr: float | None) -> None:
        max_age_ns = self.config.pool_max_age_minutes * NS_MINUTE
        protected = self.scenario.pool_id if self.scenario is not None else None
        for pool in self.pools:
            if not pool.active or pool.pool_id == protected:
                continue
            if bar.ts_ns - pool.observed_time_ns > max_age_ns:
                self._consume_pool(pool, bar.ts_ns, "EXPIRED")
                continue
            if atr is None or atr <= 0.0:
                continue
            acceptance = self.config.acceptance_atr * atr
            # A decisive close beyond a pool without a reclaim setup means that
            # external liquidity has already been consumed and accepted.
            if pool.side is PoolSide.HIGH and bar.close > pool.price + acceptance:
                self._consume_pool(pool, bar.ts_ns, "ACCEPTED_ABOVE")
            elif pool.side is PoolSide.LOW and bar.close < pool.price - acceptance:
                self._consume_pool(pool, bar.ts_ns, "ACCEPTED_BELOW")

    def _scan_for_excursion(
        self,
        bar: MarketBar,
        previous: MarketBar | None,
        atr: float,
    ) -> None:
        if previous is None:
            return
        candidates: list[tuple[float, LiquidityPool, float, float]] = []
        for pool in self.pools:
            if not pool.active or pool.observed_time_ns > bar.ts_ns:
                continue
            if pool.side is PoolSide.HIGH:
                extension = bar.high - pool.price
                approached_from_inside = previous.close <= pool.price
                structure = self.internal_lows[-1].price if self.internal_lows else None
            else:
                extension = pool.price - bar.low
                approached_from_inside = previous.close >= pool.price
                structure = self.internal_highs[-1].price if self.internal_highs else None
            if structure is None or not approached_from_inside:
                continue
            normalized = extension / atr
            if not self.config.sweep_min_atr <= normalized <= self.config.sweep_max_atr:
                continue
            recency_minutes = max(1.0, (bar.ts_ns - pool.observed_time_ns) / NS_MINUTE)
            source_bonus = 0.40 if "BLOCK" in pool.source else 0.0
            score = (1.0 + math.log1p(pool.strength) + source_bonus) / math.log1p(recency_minutes)
            candidates.append((score, pool, structure, normalized))

        if not candidates:
            return
        _, pool, structure, normalized = max(candidates, key=lambda item: item[0])
        self._scenario_sequence += 1
        trade_side = TradeSide.SELL if pool.side is PoolSide.HIGH else TradeSide.BUY
        extreme = bar.high if pool.side is PoolSide.HIGH else bar.low
        scenario = Scenario(
            scenario_id=f"{self.instrument_id}-lcr-{self._scenario_sequence:06d}",
            state=ScenarioState.EXCURSION,
            pool_id=pool.pool_id,
            swept_side=pool.side,
            trade_side=trade_side,
            level=pool.price,
            extreme=extreme,
            structure_level=structure,
            event_time_ns=pool.event_time_ns,
            observed_time_ns=bar.ts_ns,
            start_bar_index=self.bar_index,
            state_bar_index=self.bar_index,
        )
        self.scenario = scenario
        self.transitions.append(
            Transition(
                scenario_id=scenario.scenario_id,
                instrument_id=self.instrument_id,
                event_type="LIQUIDITY_EXCURSION",
                event_time_ns=pool.event_time_ns,
                observed_time_ns=bar.ts_ns,
                previous_state=ScenarioState.SCANNING.value,
                next_state=ScenarioState.EXCURSION.value,
                reason_code="EXTERNAL_POOL_SWEPT",
                reference_price=pool.price,
                details={
                    "pool_id": pool.pool_id,
                    "pool_source": pool.source,
                    "pool_strength": pool.strength,
                    "normalized_extension": normalized,
                    "structure_level_known_before_sweep": structure,
                },
            ),
        )
        self.diagnostics["SCENARIO_STARTED"] += 1

    def _advance_scenario(
        self,
        bar: MarketBar,
        atr: float,
        volume_median: float,
    ) -> TradeSignal | None:
        scenario = self.scenario
        assert scenario is not None
        if scenario.swept_side is PoolSide.HIGH:
            breached_extreme = bar.high > scenario.extreme + self.config.stop_buffer_atr * atr
        else:
            breached_extreme = bar.low < scenario.extreme - self.config.stop_buffer_atr * atr

        age = self.bar_index - scenario.state_bar_index
        if scenario.state is ScenarioState.EXCURSION:
            # The excursion extreme is not frozen until price re-enters the
            # prior auction.  After reclaim it becomes the causal invalidation
            # level and may no longer drift with future bars.
            if scenario.swept_side is PoolSide.HIGH:
                scenario.extreme = max(scenario.extreme, bar.high)
            else:
                scenario.extreme = min(scenario.extreme, bar.low)
            reclaimed = (
                bar.close < scenario.level
                if scenario.swept_side is PoolSide.HIGH
                else bar.close > scenario.level
            )
            if reclaimed:
                self._transition(
                    scenario,
                    ScenarioState.RECLAIMED,
                    reason="CLOSE_REENTERED_PRIOR_AUCTION",
                    event_time_ns=bar.ts_ns,
                    observed_time_ns=bar.ts_ns,
                    reference_price=bar.close,
                    details={"bars_after_excursion": self.bar_index - scenario.start_bar_index},
                )
                return None
            if age >= self.config.reclaim_bars:
                self._invalidate(scenario, "NO_TIMELY_RECLAIM", bar.ts_ns, bar.close)
                return None
            acceptance = self.config.acceptance_atr * atr
            accepted = (
                bar.close > scenario.level + acceptance
                if scenario.swept_side is PoolSide.HIGH
                else bar.close < scenario.level - acceptance
            )
            if accepted:
                self._invalidate(scenario, "AUCTION_ACCEPTED_BEYOND_POOL", bar.ts_ns, bar.close)
            return None

        if scenario.state is ScenarioState.RECLAIMED:
            if breached_extreme:
                self._invalidate(scenario, "SWEEP_EXTREME_BREACHED", bar.ts_ns, bar.close)
                return None
            if age >= self.config.confirmation_bars:
                self._invalidate(scenario, "NO_DISPLACEMENT_CONFIRMATION", bar.ts_ns, bar.close)
                return None
            displacement = self._displacement_confirmation(scenario, bar, atr, volume_median)
            if displacement is None:
                return None
            zone, normalized_body, body_fraction, close_location, volume_ratio = displacement
            scenario.zone = zone
            scenario.displacement_atr = normalized_body
            self._consume_pool_by_id(scenario.pool_id, bar.ts_ns, "SWEPT_AND_RECLAIMED")
            self._transition(
                scenario,
                ScenarioState.ARMED,
                reason="MSS_DISPLACEMENT_WITH_FVG",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                reference_price=scenario.structure_level,
                details={
                    "fvg_low": zone.low,
                    "fvg_high": zone.high,
                    "displacement_body_atr": normalized_body,
                    "body_fraction": body_fraction,
                    "close_location": close_location,
                    "volume_ratio": volume_ratio,
                },
            )
            return None

        if scenario.state is ScenarioState.ARMED:
            if breached_extreme:
                self._invalidate(scenario, "SWEEP_EXTREME_BREACHED", bar.ts_ns, bar.close)
                return None
            if age >= self.config.retest_bars:
                self._invalidate(scenario, "FVG_RETEST_EXPIRED", bar.ts_ns, bar.close)
                return None
            zone = scenario.zone
            assert zone is not None
            if scenario.trade_side is TradeSide.SELL:
                invalidated_zone = bar.close > zone.high
                touched = bar.high >= zone.low and bar.low <= zone.high
                rejected = bar.close < zone.midpoint and bar.close < bar.open
            else:
                invalidated_zone = bar.close < zone.low
                touched = bar.low <= zone.high and bar.high >= zone.low
                rejected = bar.close > zone.midpoint and bar.close > bar.open
            if invalidated_zone:
                self._invalidate(scenario, "FVG_CLOSED_THROUGH", bar.ts_ns, bar.close)
                return None
            if not (touched and rejected):
                return None
            signal = self._build_signal(scenario, bar, atr)
            if signal is None:
                self._invalidate(scenario, "TARGET_GEOMETRY_REJECTED", bar.ts_ns, bar.close)
                return None
            scenario.target_pool_id = signal.target_pool_id
            self._transition(
                scenario,
                ScenarioState.SIGNALLED,
                reason="FVG_RETEST_REJECTION",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                reference_price=bar.close,
                details={
                    "entry_reference": signal.entry_reference,
                    "stop_price": signal.stop_price,
                    "target_price": signal.target_price,
                    "target_pool_id": signal.target_pool_id,
                    "reward_risk": signal.reward_risk,
                    "score": signal.score,
                },
            )
            return signal

        return None

    def _displacement_confirmation(
        self,
        scenario: Scenario,
        bar: MarketBar,
        atr: float,
        volume_median: float,
    ) -> tuple[ImbalanceZone, float, float, float, float] | None:
        if len(self.bars) < 3 or bar.range <= 0.0 or atr <= 0.0:
            return None
        normalized_body = bar.body / atr
        body_fraction = bar.body / bar.range
        close_location = (bar.close - bar.low) / bar.range
        volume_ratio = bar.volume / volume_median if volume_median > 0.0 else 0.0
        common = (
            normalized_body >= self.config.displacement_atr
            and body_fraction >= self.config.displacement_body_fraction
            and volume_ratio >= self.config.displacement_volume_ratio
        )
        if not common:
            return None

        two_back = list(self.bars)[-3]
        if scenario.trade_side is TradeSide.SELL:
            directional = (
                bar.close < bar.open
                and bar.close < scenario.structure_level
                and close_location <= self.config.displacement_close_fraction
            )
            gap_low = bar.high
            gap_high = two_back.low
        else:
            directional = (
                bar.close > bar.open
                and bar.close > scenario.structure_level
                and close_location >= 1.0 - self.config.displacement_close_fraction
            )
            gap_low = two_back.high
            gap_high = bar.low
        if not directional:
            return None
        if gap_high <= gap_low or (gap_high - gap_low) < self.config.fvg_min_atr * atr:
            return None
        return (
            ImbalanceZone(gap_low, gap_high, bar.ts_ns),
            normalized_body,
            body_fraction,
            close_location,
            volume_ratio,
        )

    def _build_signal(self, scenario: Scenario, bar: MarketBar, atr: float) -> TradeSignal | None:
        entry = bar.close
        if scenario.trade_side is TradeSide.SELL:
            stop = scenario.extreme + self.config.stop_buffer_atr * atr
            targets = sorted(
                (
                    pool
                    for pool in self.pools
                    if pool.active
                    and pool.side is PoolSide.LOW
                    and pool.price < entry
                    and pool.observed_time_ns <= bar.ts_ns
                ),
                key=lambda pool: entry - pool.price,
            )
        else:
            stop = scenario.extreme - self.config.stop_buffer_atr * atr
            targets = sorted(
                (
                    pool
                    for pool in self.pools
                    if pool.active
                    and pool.side is PoolSide.HIGH
                    and pool.price > entry
                    and pool.observed_time_ns <= bar.ts_ns
                ),
                key=lambda pool: pool.price - entry,
            )
        if not targets:
            self.diagnostics["NO_OPPOSING_POOL"] += 1
            return None
        target = targets[0]
        risk = abs(entry - stop)
        reward = abs(target.price - entry)
        if risk <= 0.0 or reward / risk < self.config.min_reward_risk:
            self.diagnostics["RR_REJECTED"] += 1
            return None
        pool = self._pool_by_id(scenario.pool_id)
        source_strength = pool.strength if pool is not None else 1
        displacement = scenario.displacement_atr or 1.0
        score = source_strength * displacement * math.log1p(reward / risk)
        return TradeSignal(
            scenario_id=scenario.scenario_id,
            instrument_id=self.instrument_id,
            side=scenario.trade_side,
            observed_time_ns=bar.ts_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target.price,
            target_pool_id=target.pool_id,
            score=score,
            details={
                "swept_pool_id": scenario.pool_id,
                "swept_side": scenario.swept_side.value,
                "structure_level": scenario.structure_level,
                "fvg_low": scenario.zone.low if scenario.zone else None,
                "fvg_high": scenario.zone.high if scenario.zone else None,
            },
        )

    def _transition(
        self,
        scenario: Scenario,
        next_state: ScenarioState,
        *,
        reason: str,
        event_time_ns: int,
        observed_time_ns: int,
        reference_price: float | None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        previous = scenario.state
        scenario.state = next_state
        scenario.state_bar_index = self.bar_index
        scenario.observed_time_ns = observed_time_ns
        self.transitions.append(
            Transition(
                scenario_id=scenario.scenario_id,
                instrument_id=self.instrument_id,
                event_type="SCENARIO_STATE_TRANSITION",
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                previous_state=previous.value,
                next_state=next_state.value,
                reason_code=reason,
                reference_price=reference_price,
                details=dict(details or {}),
            ),
        )
        self.diagnostics[f"TRANSITION_{next_state.value}"] += 1

    def _invalidate(
        self,
        scenario: Scenario,
        reason: str,
        observed_time_ns: int,
        reference_price: float,
    ) -> None:
        self._consume_pool_by_id(scenario.pool_id, observed_time_ns, reason)
        self._transition(
            scenario,
            ScenarioState.INVALIDATED,
            reason=reason,
            event_time_ns=observed_time_ns,
            observed_time_ns=observed_time_ns,
            reference_price=reference_price,
        )
        self._cooldown_until_index = self.bar_index + self.config.cooldown_bars
        self.diagnostics[f"INVALIDATED_{reason}"] += 1
        self.scenario = None

    def _consume_pool_by_id(self, pool_id: str, ts_ns: int, reason: str) -> None:
        pool = self._pool_by_id(pool_id)
        if pool is not None:
            self._consume_pool(pool, ts_ns, reason)

    def _consume_pool(self, pool: LiquidityPool, ts_ns: int, reason: str) -> None:
        if not pool.active:
            return
        pool.active = False
        pool.consumed_time_ns = ts_ns
        pool.consumed_reason = reason
        self.diagnostics[f"POOL_CONSUMED_{reason}"] += 1

    def _pool_by_id(self, pool_id: str) -> LiquidityPool | None:
        return next((pool for pool in self.pools if pool.pool_id == pool_id), None)


def geometric_daily_growth(nav_factors: Iterable[float], total_days: float) -> float:
    factors = list(nav_factors)
    if total_days <= 0.0:
        raise ValueError("total_days must be positive")
    if not factors or any(factor <= 0.0 or not math.isfinite(factor) for factor in factors):
        raise ValueError("nav factors must be finite and positive")
    return math.prod(factors) ** (1.0 / total_days) - 1.0


def max_drawdown(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("NAV values must be finite and positive")
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst
