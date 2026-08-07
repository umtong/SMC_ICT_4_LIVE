"""Causal New-York raid of completed London buy-side liquidity.

Candidate 12 deliberately models one economic scenario only:

    completed London range -> New-York raid above London high -> completed
    close back inside -> one full confirmation bar -> protected sell limit at
    the raided boundary -> invalidation beyond the observed raid extreme ->
    structural objective inside the completed London dealing range.

The state machine only emits trade plans. NautilusTrader remains the sole
matching, fill, fee, margin, position, and account-NAV authority.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Deque

from smc_ict_4.contracts import ResearchEvent

NS_MINUTE = 60_000_000_000
NS_DAY = 86_400_000_000_000


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ScenarioKind(str, Enum):
    NY_LONDON_HIGH_RAID = "NY_LONDON_HIGH_RAID"


@dataclass(frozen=True, slots=True)
class BarObs:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume, self.taker_buy_volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("bar contains a non-finite value")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is inconsistent")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is inconsistent")
        if self.volume < 0 or self.taker_buy_volume < 0:
            raise ValueError("bar volume cannot be negative")
        if self.taker_buy_volume > self.volume + 1e-9:
            raise ValueError("taker-buy volume exceeds total volume")


@dataclass(frozen=True, slots=True)
class LogicConfig:
    bar_minutes: int = 5
    atr_period: int = 36
    london_start_minute: int = 360
    london_end_minute: int = 720
    ny_end_minute: int = 1080
    reclaim_max_bars: int = 3
    confirmation_bars: int = 1
    entry_expiry_minutes: int = 15
    sweep_min_ticks: int = 1
    stop_buffer_atr: float = 0.80
    target_range_fraction: float = 0.60
    max_stop_atr: float = 5.0
    min_net_r: float = 0.50
    risk_fraction: float = 0.03
    effective_maker_rate: float = 0.0004
    effective_taker_rate: float = 0.0008
    tick_slippage_units: float = 2.0
    price_increment: float = 0.1

    def __post_init__(self) -> None:
        for name in (
            "bar_minutes", "atr_period", "reclaim_max_bars", "confirmation_bars",
            "entry_expiry_minutes", "sweep_min_ticks",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be within (0, 0.03]")
        if not 0.5 <= self.target_range_fraction <= 0.618:
            raise ValueError("target_range_fraction must stay in the equilibrium-to-discount band")
        if self.stop_buffer_atr <= 0 or self.max_stop_atr <= self.stop_buffer_atr:
            raise ValueError("invalid stop-distance bounds")
        if self.price_increment <= 0:
            raise ValueError("price_increment must be positive")
        if not 0 <= self.min_net_r:
            raise ValueError("min_net_r cannot be negative")
        if not (
            0 <= self.london_start_minute < self.london_end_minute < self.ny_end_minute <= 1440
        ):
            raise ValueError("invalid session boundaries")


@dataclass(frozen=True, slots=True)
class TradePlan:
    scenario_id: str
    scenario: ScenarioKind
    direction: Direction
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    loss_per_unit: float
    expected_profit_per_unit: float
    net_r: float
    expire_ts_ns: int
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SizeDecision:
    feasible: bool
    quantity: Decimal
    planned_loss_budget: Decimal
    expected_total_loss: Decimal
    required_margin: Decimal
    reason: str


class RiskSizer:
    """Exact current-NAV loss-budget sizing, with margin only as feasibility."""

    def __init__(self, risk_fraction: float) -> None:
        fraction = Decimal(str(risk_fraction))
        if fraction <= 0 or fraction > Decimal("0.03"):
            raise ValueError("risk fraction must be within (0, 0.03]")
        self.risk_fraction = fraction

    @staticmethod
    def _floor_increment(value: Decimal, increment: Decimal) -> Decimal:
        if increment <= 0:
            raise ValueError("quantity increment must be positive")
        return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment

    def size(
        self,
        *,
        nav: Decimal,
        loss_per_unit: Decimal,
        entry_price: Decimal,
        quantity_increment: Decimal,
        min_quantity: Decimal,
        min_notional: Decimal,
        margin_init: Decimal,
        free_balance: Decimal,
    ) -> SizeDecision:
        zero = Decimal("0")
        budget = nav * self.risk_fraction
        if nav <= zero or loss_per_unit <= zero or entry_price <= zero:
            return SizeDecision(False, zero, budget, zero, zero, "INVALID_RISK_INPUT")
        quantity = self._floor_increment(budget / loss_per_unit, quantity_increment)
        expected = quantity * loss_per_unit
        if quantity < min_quantity:
            return SizeDecision(False, quantity, budget, expected, zero, "BELOW_MIN_QUANTITY")
        notional = quantity * entry_price
        if notional < min_notional:
            return SizeDecision(False, quantity, budget, expected, zero, "BELOW_MIN_NOTIONAL")
        if expected > budget:
            return SizeDecision(False, quantity, budget, expected, zero, "RISK_BUDGET_EXCEEDED")
        required_margin = notional * margin_init
        if required_margin > free_balance:
            return SizeDecision(
                False, quantity, budget, expected, required_margin, "INSUFFICIENT_MARGIN"
            )
        return SizeDecision(True, quantity, budget, expected, required_margin, "OK")


@dataclass(frozen=True, slots=True)
class FiveBar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float


@dataclass(frozen=True, slots=True)
class LondonRange:
    day_bucket: int
    high: float
    low: float
    observed_ts_ns: int

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(slots=True)
class RaidEpisode:
    scenario_id: str
    source: LondonRange
    sweep_index: int
    sweep_ts_ns: int
    extreme: float
    phase: str = "WAIT_RECLAIM"
    reclaim_index: int | None = None
    reclaim_ts_ns: int | None = None
    atr_at_reclaim: float | None = None
    confirm_index: int | None = None


class CausalLiquidityAuctionEngine:
    """One-plan-per-weekday London-high raid/reclaim state machine."""

    def __init__(self, config: LogicConfig, instrument_id: str) -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        self.scenario_counts: Counter[str] = Counter()
        self.pool_counts: Counter[str] = Counter()
        self._states: dict[str, str] = {}
        self._minute_parts: list[BarObs] = []
        self._five_index = -1
        self._true_ranges: Deque[float] = deque(maxlen=config.atr_period)
        self._previous_five_close: float | None = None
        self._day_bucket: int | None = None
        self._london_high = -math.inf
        self._london_low = math.inf
        self._london: LondonRange | None = None
        self._ranges: list[LondonRange] = []
        self._episode: RaidEpisode | None = None
        self._day_done = False
        self._scenario_counter = 0

    @property
    def pools(self) -> tuple[LondonRange, ...]:
        return tuple(self._ranges)

    @staticmethod
    def _minute_of_day(ts_ns: int) -> int:
        return int(((ts_ns - 1) % NS_DAY) // NS_MINUTE) + 1

    @staticmethod
    def _weekday(day_bucket: int) -> int:
        return datetime.fromtimestamp(day_bucket * 86_400, tz=timezone.utc).weekday()

    def _emit(
        self,
        *,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        previous_state = self._states.get(scenario_id, "NONE")
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type=event_type,
                event_time_ns=int(event_time_ns),
                observed_time_ns=int(observed_time_ns),
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=(
                    None
                    if reference_price is None
                    else f"{reference_price:.12f}".rstrip("0").rstrip(".")
                ),
                details=details or {},
            )
        )
        self._states[scenario_id] = next_state

    def _aggregate_five(self) -> FiveBar:
        parts = self._minute_parts
        return FiveBar(
            ts_ns=parts[-1].ts_ns,
            open=parts[0].open,
            high=max(item.high for item in parts),
            low=min(item.low for item in parts),
            close=parts[-1].close,
            volume=sum(item.volume for item in parts),
            taker_buy_volume=sum(item.taker_buy_volume for item in parts),
        )

    def _atr(self) -> float | None:
        if len(self._true_ranges) < self.config.atr_period:
            return None
        return sum(self._true_ranges) / len(self._true_ranges)

    def _roll_day(self, bar: FiveBar) -> None:
        day_bucket = (bar.ts_ns - 1) // NS_DAY
        if self._day_bucket == day_bucket:
            return
        if self._episode is not None:
            self._terminate_episode(bar.ts_ns, "DAY_ROLLOVER")
        self._day_bucket = day_bucket
        self._london_high = -math.inf
        self._london_low = math.inf
        self._london = None
        self._episode = None
        self._day_done = False

    def _freeze_london(self, bar: FiveBar) -> None:
        if self._day_bucket is None or not math.isfinite(self._london_high) or not math.isfinite(self._london_low):
            return
        if self._london_high <= self._london_low:
            self.skips["INVALID_LONDON_RANGE"] += 1
            return
        self._london = LondonRange(
            day_bucket=self._day_bucket,
            high=self._london_high,
            low=self._london_low,
            observed_ts_ns=bar.ts_ns,
        )
        self._ranges.append(self._london)
        self.pool_counts["LONDON_RANGE"] += 1
        range_id = f"{self.instrument_id}-LONDON-{self._day_bucket}"
        self._emit(
            scenario_id=range_id,
            event_type="LONDON_RANGE_FROZEN",
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="RANGE_FROZEN",
            reason_code="COMPLETED_0600_1200_UTC_RANGE",
            details={
                "high": self._london.high,
                "low": self._london.low,
                "width": self._london.width,
                "weekday": self._weekday(self._day_bucket),
            },
        )

    def _start_raid(self, bar: FiveBar, atr: float) -> None:
        assert self._london is not None
        self._scenario_counter += 1
        scenario_id = f"{self.instrument_id}-NY-LH-RAID-{self._scenario_counter:06d}"
        self._episode = RaidEpisode(
            scenario_id=scenario_id,
            source=self._london,
            sweep_index=self._five_index,
            sweep_ts_ns=bar.ts_ns,
            extreme=bar.high,
        )
        self.scenario_counts[ScenarioKind.NY_LONDON_HIGH_RAID.value] += 1
        self._emit(
            scenario_id=scenario_id,
            event_type="LONDON_HIGH_RAID_DETECTED",
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="WAIT_RECLAIM",
            reason_code="NEW_YORK_TRADED_ABOVE_COMPLETED_LONDON_HIGH",
            reference_price=self._london.high,
            details={
                "london_high": self._london.high,
                "london_low": self._london.low,
                "raid_high": bar.high,
                "penetration_atr": (bar.high - self._london.high) / atr,
            },
        )

    def _terminate_episode(self, ts_ns: int, reason: str) -> None:
        episode = self._episode
        if episode is None:
            return
        self.skips[reason] += 1
        self._emit(
            scenario_id=episode.scenario_id,
            event_type="SCENARIO_INVALIDATED",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            next_state="TERMINAL",
            reason_code=reason,
            reference_price=episode.source.high,
            details={"phase": episode.phase, "raid_extreme": episode.extreme},
        )
        self._episode = None
        self._day_done = True

    def _costed_plan(self, episode: RaidEpisode, bar: FiveBar) -> TradePlan | None:
        atr = episode.atr_at_reclaim
        if atr is None or atr <= 0:
            self.skips["ATR_UNAVAILABLE_AT_RECLAIM"] += 1
            return None
        source = episode.source
        increment = Decimal(str(self.config.price_increment))

        def ceil_tick(value: float) -> float:
            units = (Decimal(str(value)) / increment).to_integral_value(rounding=ROUND_CEILING)
            return float(units * increment)

        entry = ceil_tick(source.high)
        # Round stop away from the position and target toward entry so sizing
        # never benefits from price-precision rounding.
        stop = ceil_tick(episode.extreme + self.config.stop_buffer_atr * atr)
        target = ceil_tick(source.high - self.config.target_range_fraction * source.width)
        stop_distance = stop - entry
        if stop_distance <= 0 or stop_distance > self.config.max_stop_atr * atr:
            self.skips["INVALID_STRUCTURAL_STOP"] += 1
            return None
        if not source.low < target < entry:
            self.skips["INVALID_STRUCTURAL_TARGET"] += 1
            return None
        # A protected sell limit can be marketable; reserve taker entry cost.
        entry_cost = entry * self.config.effective_taker_rate
        stop_cost = stop * self.config.effective_taker_rate
        target_cost = target * self.config.effective_maker_rate
        slippage = self.config.tick_slippage_units * self.config.price_increment
        loss_per_unit = stop_distance + entry_cost + stop_cost + slippage
        expected_profit = entry - target - entry_cost - target_cost - slippage
        if loss_per_unit <= 0 or expected_profit <= 0:
            self.skips["NON_POSITIVE_COSTED_EXPECTANCY"] += 1
            return None
        net_r = expected_profit / loss_per_unit
        if net_r < self.config.min_net_r:
            self.skips["INSUFFICIENT_COSTED_STRUCTURAL_R"] += 1
            return None
        return TradePlan(
            scenario_id=episode.scenario_id,
            scenario=ScenarioKind.NY_LONDON_HIGH_RAID,
            direction=Direction.SHORT,
            observed_ts_ns=bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            loss_per_unit=loss_per_unit,
            expected_profit_per_unit=expected_profit,
            net_r=net_r,
            expire_ts_ns=bar.ts_ns + self.config.entry_expiry_minutes * NS_MINUTE,
            details={
                "source": "COMPLETED_LONDON_RANGE",
                "london_high": source.high,
                "london_low": source.low,
                "london_width": source.width,
                "raid_extreme": episode.extreme,
                "sweep_ts_ns": episode.sweep_ts_ns,
                "reclaim_ts_ns": episode.reclaim_ts_ns,
                "confirmation_ts_ns": bar.ts_ns,
                "atr_at_reclaim": atr,
                "stop_buffer_atr": self.config.stop_buffer_atr,
                "target_range_fraction": self.config.target_range_fraction,
                "entry_cost_per_unit": entry_cost,
                "stop_cost_per_unit": stop_cost,
                "target_cost_per_unit": target_cost,
                "slippage_allowance_per_unit": slippage,
                "entry_semantics": "SELL_LIMIT_GTD_MARKETABLE_PROTECTED",
            },
        )

    def _advance_episode(self, bar: FiveBar, atr: float, allow_entry: bool) -> TradePlan | None:
        episode = self._episode
        if episode is None:
            return None
        source = episode.source
        minute = self._minute_of_day(bar.ts_ns)
        if minute > self.config.ny_end_minute:
            self._terminate_episode(bar.ts_ns, "NEW_YORK_WINDOW_EXPIRED")
            return None

        if episode.phase == "WAIT_RECLAIM":
            episode.extreme = max(episode.extreme, bar.high)
            age = self._five_index - episode.sweep_index
            if bar.close < source.high:
                episode.phase = "WAIT_CONFIRM"
                episode.reclaim_index = self._five_index
                episode.reclaim_ts_ns = bar.ts_ns
                episode.atr_at_reclaim = atr
                episode.confirm_index = self._five_index + self.config.confirmation_bars
                self._emit(
                    scenario_id=episode.scenario_id,
                    event_type="RAID_RECLAIM_CONFIRMED",
                    event_time_ns=episode.sweep_ts_ns,
                    observed_time_ns=bar.ts_ns,
                    next_state="WAIT_CONFIRM",
                    reason_code="COMPLETED_BAR_CLOSED_BACK_INSIDE_LONDON_RANGE",
                    reference_price=source.high,
                    details={
                        "raid_extreme": episode.extreme,
                        "bars_from_sweep": age,
                        "atr_at_reclaim": atr,
                    },
                )
                return None
            if age >= self.config.reclaim_max_bars:
                self._terminate_episode(bar.ts_ns, "RAID_NOT_RECLAIMED_IN_TIME")
            return None

        assert episode.confirm_index is not None
        if self._five_index < episode.confirm_index:
            return None
        prospective_stop = episode.extreme + self.config.stop_buffer_atr * float(episode.atr_at_reclaim)
        if bar.high >= prospective_stop:
            self._terminate_episode(bar.ts_ns, "CONFIRMATION_TRADED_THROUGH_INVALIDATION")
            return None
        plan = self._costed_plan(episode, bar)
        if plan is None:
            self._terminate_episode(bar.ts_ns, "COSTED_PLAN_REJECTED")
            return None
        if not allow_entry:
            self.skips["OUTSIDE_EVALUATION_WINDOW"] += 1
            self._emit(
                scenario_id=episode.scenario_id,
                event_type="TRADE_PLAN_REJECTED",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                next_state="TERMINAL",
                reason_code="OUTSIDE_EVALUATION_WINDOW",
                reference_price=plan.expected_entry,
                details={"net_r": plan.net_r},
            )
            self._episode = None
            self._day_done = True
            return None
        self._emit(
            scenario_id=episode.scenario_id,
            event_type="TRADE_PLAN_EMITTED",
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="PLAN_EMITTED",
            reason_code="RAID_RECLAIM_CONFIRMATION_AND_COSTED_RANGE_OBJECTIVE_VALID",
            reference_price=plan.expected_entry,
            details={
                "direction": plan.direction.value,
                "entry": plan.expected_entry,
                "stop": plan.stop_price,
                "target": plan.target_price,
                "expire_ts_ns": plan.expire_ts_ns,
                "net_r": plan.net_r,
            },
        )
        self._episode = None
        self._day_done = True
        return plan

    def _on_five(self, bar: FiveBar, allow_entry: bool) -> TradePlan | None:
        self._five_index += 1
        if self._previous_five_close is None:
            true_range = bar.high - bar.low
        else:
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - self._previous_five_close),
                abs(bar.low - self._previous_five_close),
            )
        self._true_ranges.append(true_range)
        self._previous_five_close = bar.close
        atr = self._atr()
        self._roll_day(bar)
        minute = self._minute_of_day(bar.ts_ns)

        if self.config.london_start_minute < minute <= self.config.london_end_minute:
            self._london_high = max(self._london_high, bar.high)
            self._london_low = min(self._london_low, bar.low)
            if minute == self.config.london_end_minute:
                self._freeze_london(bar)
            return None

        if (
            atr is None
            or self._london is None
            or self._day_bucket is None
            or self._weekday(self._day_bucket) >= 5
            or minute <= self.config.london_end_minute
            or minute > self.config.ny_end_minute
            or self._day_done
        ):
            return None

        if self._episode is None:
            threshold = self._london.high + self.config.sweep_min_ticks * self.config.price_increment
            if bar.high >= threshold:
                self._start_raid(bar, atr)
                return self._advance_episode(bar, atr, allow_entry)
            return None
        return self._advance_episode(bar, atr, allow_entry)

    def on_bar(self, bar: BarObs, *, allow_entry: bool = True) -> TradePlan | None:
        self._minute_parts.append(bar)
        boundary = self.config.bar_minutes * NS_MINUTE
        if bar.ts_ns % boundary != 0:
            return None
        expected_parts = self.config.bar_minutes
        if len(self._minute_parts) != expected_parts:
            self.skips["INCOMPLETE_AGGREGATION_BUCKET"] += 1
            self._minute_parts.clear()
            return None
        five = self._aggregate_five()
        self._minute_parts.clear()
        return self._on_five(five, allow_entry)

    def mark_plan_rejected(
        self,
        plan: TradePlan,
        ts_ns: int,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.skips[reason] += 1
        self._emit(
            scenario_id=plan.scenario_id,
            event_type="TRADE_PLAN_REJECTED",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            next_state="TERMINAL",
            reason_code=reason,
            reference_price=plan.expected_entry,
            details=details or {},
        )

    def mark_plan_submitted(self, plan: TradePlan, ts_ns: int, details: dict[str, Any]) -> None:
        self._emit(
            scenario_id=plan.scenario_id,
            event_type="TRADE_PLAN_SUBMITTED",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            next_state="SUBMITTED",
            reason_code="NAUTILUS_LIMIT_BRACKET_SUBMITTED",
            reference_price=plan.expected_entry,
            details=details,
        )

    def mark_trade_terminal(
        self,
        plan: TradePlan,
        ts_ns: int,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._emit(
            scenario_id=plan.scenario_id,
            event_type="TRADE_TERMINAL",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            next_state="TERMINAL",
            reason_code=reason,
            reference_price=plan.expected_entry,
            details=details or {},
        )


__all__ = [
    "BarObs", "CausalLiquidityAuctionEngine", "Direction", "FiveBar", "LogicConfig",
    "RiskSizer", "ScenarioKind", "SizeDecision", "TradePlan",
]
