"""Causal completed-London-range auction state machine.

One completed London range can resolve through four mutually exclusive paths:

* low-side raid rejected in London discount -> long to the opposite boundary;
* high-side raid rejected by upper-range context or forceful reclaim -> short
  into the completed range;
* weak high-side rejection fails and price accepts above the raid extreme ->
  long one completed-range projection;
* a deep-discount London low is accepted below its raid extreme -> short one
  completed-range projection.

The module emits causal trade plans only. NautilusTrader remains the only
matching, fill, fee, margin, position and account-NAV authority.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from enum import Enum
import math
from typing import Any, Deque

from smc_ict_4.contracts import ResearchEvent

NS_MINUTE = 60_000_000_000
NS_DAY = 86_400_000_000_000


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class BoundarySide(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class ScenarioKind(str, Enum):
    LONDON_HIGH_REJECTION = "LONDON_HIGH_REJECTION"
    LONDON_LOW_REJECTION = "LONDON_LOW_REJECTION"
    LONDON_HIGH_ACCEPTANCE = "LONDON_HIGH_ACCEPTANCE"
    LONDON_LOW_ACCEPTANCE = "LONDON_LOW_ACCEPTANCE"


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
    high_reclaim_displacement_atr: float = 1.0
    acceptance_displacement_atr: float = 1.0
    acceptance_close_location: float = 0.65
    low_acceptance_deep_discount: float = 0.25
    stop_buffer_atr: float = 0.80
    rejection_target_fraction: float = 0.60
    acceptance_range_projection: float = 1.0
    max_stop_atr: float = 5.0
    min_net_r: float = 0.65
    risk_fraction: float = 0.03
    effective_maker_rate: float = 0.0004
    effective_taker_rate: float = 0.0008
    tick_slippage_units: float = 2.0
    price_increment: float = 0.1

    def __post_init__(self) -> None:
        for name in ("bar_minutes", "atr_period", "reclaim_max_bars", "confirmation_bars"):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be within (0, 0.03]")
        if not 0.5 <= self.rejection_target_fraction <= 0.618:
            raise ValueError("rejection target must remain equilibrium-to-discount")
        if not 0 < self.low_acceptance_deep_discount < 0.5:
            raise ValueError("deep-discount boundary must be below equilibrium")
        if not 0.5 < self.acceptance_close_location < 1:
            raise ValueError("acceptance close location must be upper-range")
        if self.stop_buffer_atr <= 0 or self.max_stop_atr <= self.stop_buffer_atr:
            raise ValueError("invalid stop-distance bounds")
        if self.price_increment <= 0:
            raise ValueError("price_increment must be positive")
        if self.min_net_r < 0:
            raise ValueError("min_net_r cannot be negative")
        if not (0 <= self.london_start_minute < self.london_end_minute < self.ny_end_minute <= 1440):
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
    """Exact current-NAV loss-budget sizing; margin is only feasibility."""

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
            return SizeDecision(False, quantity, budget, expected, required_margin, "INSUFFICIENT_MARGIN")
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

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def close_location(self) -> float:
        if self.range <= 0:
            return 0.5
        return (self.close - self.low) / self.range


@dataclass(frozen=True, slots=True)
class LondonRange:
    day_bucket: int
    high: float
    low: float
    close: float
    observed_ts_ns: int

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def close_location(self) -> float:
        return (self.close - self.low) / self.width


@dataclass(slots=True)
class BoundaryEpisode:
    scenario_id: str
    side: BoundarySide
    source: LondonRange
    sweep_index: int
    sweep_ts_ns: int
    extreme: float
    phase: str = "WAIT_RECLAIM"
    reclaim_index: int | None = None
    reclaim_ts_ns: int | None = None
    reclaim_bar: FiveBar | None = None
    atr_at_reclaim: float | None = None
    confirm_index: int | None = None


class CausalLiquidityAuctionEngine:
    """Completed-range rejection/acceptance state machine."""

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
        self._london_close: float | None = None
        self._london: LondonRange | None = None
        self._ranges: list[LondonRange] = []
        self._episodes: dict[BoundarySide, BoundaryEpisode] = {}
        self._done_sides: set[BoundarySide] = set()
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
                reference_price=(None if reference_price is None else f"{reference_price:.12f}".rstrip("0").rstrip(".")),
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
        for side in tuple(self._episodes):
            self._terminate(side, bar.ts_ns, "DAY_ROLLOVER")
        self._day_bucket = day_bucket
        self._london_high = -math.inf
        self._london_low = math.inf
        self._london_close = None
        self._london = None
        self._episodes.clear()
        self._done_sides.clear()

    def _freeze_london(self, bar: FiveBar) -> None:
        if self._day_bucket is None or not math.isfinite(self._london_high) or not math.isfinite(self._london_low):
            return
        if self._london_high <= self._london_low or self._london_close is None:
            self.skips["INVALID_LONDON_RANGE"] += 1
            return
        self._london = LondonRange(
            day_bucket=self._day_bucket,
            high=self._london_high,
            low=self._london_low,
            close=self._london_close,
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
                "close": self._london.close,
                "width": self._london.width,
                "close_location": self._london.close_location,
                "weekday": self._weekday(self._day_bucket),
            },
        )

    def _start_episode(self, side: BoundarySide, bar: FiveBar, atr: float) -> None:
        assert self._london is not None
        self._scenario_counter += 1
        scenario_id = f"{self.instrument_id}-LONDON-{side.value}-{self._scenario_counter:06d}"
        extreme = bar.high if side is BoundarySide.HIGH else bar.low
        episode = BoundaryEpisode(
            scenario_id=scenario_id,
            side=side,
            source=self._london,
            sweep_index=self._five_index,
            sweep_ts_ns=bar.ts_ns,
            extreme=extreme,
        )
        self._episodes[side] = episode
        self.scenario_counts[f"LONDON_{side.value}_AUCTION"] += 1
        boundary = self._london.high if side is BoundarySide.HIGH else self._london.low
        self._emit(
            scenario_id=scenario_id,
            event_type="LONDON_BOUNDARY_RAID_DETECTED",
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="WAIT_RECLAIM",
            reason_code=f"NEW_YORK_TRADED_BEYOND_COMPLETED_LONDON_{side.value}",
            reference_price=boundary,
            details={
                "side": side.value,
                "london_high": self._london.high,
                "london_low": self._london.low,
                "london_close_location": self._london.close_location,
                "raid_extreme": extreme,
                "penetration_atr": abs(extreme - boundary) / atr,
            },
        )

    def _mark_reclaim(self, episode: BoundaryEpisode, bar: FiveBar, atr: float) -> None:
        episode.phase = "WAIT_CONFIRM"
        episode.reclaim_index = self._five_index
        episode.reclaim_ts_ns = bar.ts_ns
        episode.reclaim_bar = bar
        episode.atr_at_reclaim = atr
        episode.confirm_index = self._five_index + self.config.confirmation_bars
        boundary = episode.source.high if episode.side is BoundarySide.HIGH else episode.source.low
        self._emit(
            scenario_id=episode.scenario_id,
            event_type="BOUNDARY_RECLAIM_OBSERVED",
            event_time_ns=episode.sweep_ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="WAIT_CONFIRM",
            reason_code="COMPLETED_BAR_CLOSED_BACK_INSIDE_LONDON_RANGE",
            reference_price=boundary,
            details={
                "side": episode.side.value,
                "raid_extreme": episode.extreme,
                "bars_from_sweep": self._five_index - episode.sweep_index,
                "atr_at_reclaim": atr,
                "reclaim_body_atr": bar.body / atr,
                "reclaim_direction": "UP" if bar.close > bar.open else "DOWN",
            },
        )

    def _wait_acceptance(self, episode: BoundaryEpisode, bar: FiveBar, reason: str) -> None:
        episode.phase = "WAIT_ACCEPT"
        boundary = episode.source.high if episode.side is BoundarySide.HIGH else episode.source.low
        self._emit(
            scenario_id=episode.scenario_id,
            event_type="REJECTION_NOT_CONFIRMED",
            event_time_ns=episode.sweep_ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="WAIT_ACCEPT",
            reason_code=reason,
            reference_price=boundary,
            details={
                "side": episode.side.value,
                "raid_extreme": episode.extreme,
                "london_close_location": episode.source.close_location,
            },
        )

    def _terminate(self, side: BoundarySide, ts_ns: int, reason: str) -> None:
        episode = self._episodes.pop(side, None)
        if episode is None:
            return
        self.skips[reason] += 1
        boundary = episode.source.high if side is BoundarySide.HIGH else episode.source.low
        self._emit(
            scenario_id=episode.scenario_id,
            event_type="SCENARIO_INVALIDATED",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            next_state="TERMINAL",
            reason_code=reason,
            reference_price=boundary,
            details={"phase": episode.phase, "raid_extreme": episode.extreme},
        )
        self._done_sides.add(side)

    def _round_price(self, value: float, rounding: str) -> float:
        increment = Decimal(str(self.config.price_increment))
        mode = ROUND_CEILING if rounding == "CEIL" else ROUND_DOWN
        units = (Decimal(str(value)) / increment).to_integral_value(rounding=mode)
        return float(units * increment)

    def _build_plan(
        self,
        *,
        episode: BoundaryEpisode,
        bar: FiveBar,
        atr: float,
        scenario: ScenarioKind,
        direction: Direction,
        stop_raw: float,
        target_raw: float,
        reason: str,
    ) -> TradePlan | None:
        # A structural objective is invalid once the completed decision bar has
        # already traded through it.  Entering afterward would reuse consumed
        # liquidity and manufacture reward from a target which is no longer live.
        if direction is Direction.LONG and bar.high >= target_raw:
            self.skips["STRUCTURAL_TARGET_REACHED_BEFORE_DECISION"] += 1
            return None
        if direction is Direction.SHORT and bar.low <= target_raw:
            self.skips["STRUCTURAL_TARGET_REACHED_BEFORE_DECISION"] += 1
            return None
        if direction is Direction.LONG:
            entry = self._round_price(bar.close, "CEIL")
            stop = self._round_price(stop_raw, "FLOOR")
            target = self._round_price(target_raw, "FLOOR")
            structural_loss = entry - stop
            structural_profit = target - entry
        else:
            entry = self._round_price(bar.close, "FLOOR")
            stop = self._round_price(stop_raw, "CEIL")
            target = self._round_price(target_raw, "CEIL")
            structural_loss = stop - entry
            structural_profit = entry - target
        if structural_loss <= 0 or structural_loss > self.config.max_stop_atr * atr:
            self.skips["INVALID_STRUCTURAL_STOP"] += 1
            return None
        if structural_profit <= 0:
            self.skips["INVALID_STRUCTURAL_TARGET"] += 1
            return None
        entry_cost = entry * self.config.effective_taker_rate
        stop_cost = stop * self.config.effective_taker_rate
        target_cost = target * self.config.effective_maker_rate
        slippage = self.config.tick_slippage_units * self.config.price_increment
        loss_per_unit = structural_loss + entry_cost + stop_cost + slippage
        expected_profit = structural_profit - entry_cost - target_cost - slippage
        if loss_per_unit <= 0 or expected_profit <= 0:
            self.skips["NON_POSITIVE_COSTED_EXPECTANCY"] += 1
            return None
        net_r = expected_profit / loss_per_unit
        if net_r < self.config.min_net_r:
            self.skips["INSUFFICIENT_COSTED_STRUCTURAL_R"] += 1
            return None
        return TradePlan(
            scenario_id=episode.scenario_id,
            scenario=scenario,
            direction=direction,
            observed_ts_ns=bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            loss_per_unit=loss_per_unit,
            expected_profit_per_unit=expected_profit,
            net_r=net_r,
            details={
                "source": "COMPLETED_LONDON_RANGE",
                "side": episode.side.value,
                "london_high": episode.source.high,
                "london_low": episode.source.low,
                "london_close": episode.source.close,
                "london_close_location": episode.source.close_location,
                "london_width": episode.source.width,
                "raid_extreme": episode.extreme,
                "sweep_ts_ns": episode.sweep_ts_ns,
                "reclaim_ts_ns": episode.reclaim_ts_ns,
                "decision_ts_ns": bar.ts_ns,
                "decision_atr": atr,
                "entry_cost_per_unit": entry_cost,
                "stop_cost_per_unit": stop_cost,
                "target_cost_per_unit": target_cost,
                "slippage_allowance_per_unit": slippage,
                "decision_reason": reason,
                "entry_semantics": "MARKET_AFTER_COMPLETED_CAUSAL_CONFIRMATION",
            },
        )

    def _emit_plan(self, episode: BoundaryEpisode, bar: FiveBar, plan: TradePlan, allow_entry: bool) -> TradePlan | None:
        side = episode.side
        self._episodes.pop(side, None)
        self._done_sides.add(side)
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
                details={"scenario": plan.scenario.value, "net_r": plan.net_r},
            )
            return None
        self._emit(
            scenario_id=episode.scenario_id,
            event_type="TRADE_PLAN_EMITTED",
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="PLAN_EMITTED",
            reason_code="COSTED_CAUSAL_AUCTION_PLAN_VALID",
            reference_price=plan.expected_entry,
            details={
                "scenario": plan.scenario.value,
                "direction": plan.direction.value,
                "entry": plan.expected_entry,
                "stop": plan.stop_price,
                "target": plan.target_price,
                "net_r": plan.net_r,
            },
        )
        return plan

    def _advance_episode(self, side: BoundarySide, bar: FiveBar, atr: float, allow_entry: bool) -> TradePlan | None:
        episode = self._episodes.get(side)
        if episode is None:
            return None
        source = episode.source
        minute = self._minute_of_day(bar.ts_ns)
        if minute > self.config.ny_end_minute:
            self._terminate(side, bar.ts_ns, "NEW_YORK_WINDOW_EXPIRED")
            return None

        if episode.phase == "WAIT_RECLAIM":
            if side is BoundarySide.HIGH:
                episode.extreme = max(episode.extreme, bar.high)
                reclaimed = bar.close < source.high
            else:
                episode.extreme = min(episode.extreme, bar.low)
                reclaimed = bar.close > source.low
            if reclaimed:
                self._mark_reclaim(episode, bar, atr)
                return None
            if self._five_index - episode.sweep_index >= self.config.reclaim_max_bars:
                self._wait_acceptance(episode, bar, "BOUNDARY_NOT_RECLAIMED_IN_TIME")
            return None

        if episode.phase == "WAIT_CONFIRM":
            assert episode.confirm_index is not None
            if self._five_index < episode.confirm_index:
                return None
            assert episode.reclaim_bar is not None and episode.atr_at_reclaim is not None
            reclaim = episode.reclaim_bar
            reclaim_atr = episode.atr_at_reclaim
            if side is BoundarySide.HIGH:
                upper_context = source.close_location >= 0.5
                forceful_reclaim = reclaim.body / reclaim_atr >= self.config.high_reclaim_displacement_atr
                invalidation = episode.extreme + self.config.stop_buffer_atr * reclaim_atr
                if (upper_context or forceful_reclaim) and bar.high < invalidation:
                    plan = self._build_plan(
                        episode=episode,
                        bar=bar,
                        atr=reclaim_atr,
                        scenario=ScenarioKind.LONDON_HIGH_REJECTION,
                        direction=Direction.SHORT,
                        stop_raw=invalidation,
                        target_raw=source.high - self.config.rejection_target_fraction * source.width,
                        reason="UPPER_RANGE_CONTEXT_OR_FORCEFUL_RECLAIM",
                    )
                    if plan is not None:
                        return self._emit_plan(episode, bar, plan, allow_entry)
                self._wait_acceptance(episode, bar, "WEAK_HIGH_RECLAIM_REQUIRES_ACCEPTANCE_DECISION")
                return None

            discount_context = source.close_location < 0.5
            bullish_reclaim = reclaim.close > reclaim.open
            held_inside = bar.close > source.low
            invalidation = episode.extreme - self.config.stop_buffer_atr * reclaim_atr
            if discount_context and bullish_reclaim and held_inside and bar.low > invalidation:
                plan = self._build_plan(
                    episode=episode,
                    bar=bar,
                    atr=reclaim_atr,
                    scenario=ScenarioKind.LONDON_LOW_REJECTION,
                    direction=Direction.LONG,
                    stop_raw=invalidation,
                    target_raw=source.high,
                    reason="DISCOUNT_LOW_RAID_RECLAIM_HELD_TO_OPPOSITE_BOUNDARY",
                )
                if plan is not None:
                    return self._emit_plan(episode, bar, plan, allow_entry)
            self._wait_acceptance(episode, bar, "LOW_RECLAIM_NOT_HELD_OR_NOT_IN_DISCOUNT")
            return None

        if side is BoundarySide.HIGH:
            if (
                bar.close > episode.extreme
                and bar.body / atr >= self.config.acceptance_displacement_atr
                and bar.close_location >= self.config.acceptance_close_location
            ):
                plan = self._build_plan(
                    episode=episode,
                    bar=bar,
                    atr=atr,
                    scenario=ScenarioKind.LONDON_HIGH_ACCEPTANCE,
                    direction=Direction.LONG,
                    stop_raw=source.high - self.config.stop_buffer_atr * atr,
                    target_raw=source.high + self.config.acceptance_range_projection * source.width,
                    reason="DISPLACEMENT_CLOSE_ABOVE_RAID_EXTREME",
                )
                if plan is not None:
                    return self._emit_plan(episode, bar, plan, allow_entry)
            return None

        if source.close_location > self.config.low_acceptance_deep_discount:
            self._terminate(side, bar.ts_ns, "LOW_ACCEPTANCE_REQUIRES_DEEP_DISCOUNT_LONDON_CLOSE")
            return None
        if (
            bar.close < episode.extreme
            and bar.body / atr >= self.config.acceptance_displacement_atr
            and bar.close_location <= 1.0 - self.config.acceptance_close_location
        ):
            plan = self._build_plan(
                episode=episode,
                bar=bar,
                atr=atr,
                scenario=ScenarioKind.LONDON_LOW_ACCEPTANCE,
                direction=Direction.SHORT,
                stop_raw=source.low + self.config.stop_buffer_atr * atr,
                target_raw=source.low - self.config.acceptance_range_projection * source.width,
                reason="DEEP_DISCOUNT_DISPLACEMENT_CLOSE_BELOW_RAID_EXTREME",
            )
            if plan is not None:
                return self._emit_plan(episode, bar, plan, allow_entry)
        return None

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
            self._london_close = bar.close
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
        ):
            return None

        plan: TradePlan | None = None
        for side in (BoundarySide.LOW, BoundarySide.HIGH):
            if side in self._done_sides:
                continue
            episode = self._episodes.get(side)
            if episode is None:
                crossed = (
                    bar.high >= self._london.high + self.config.price_increment
                    if side is BoundarySide.HIGH
                    else bar.low <= self._london.low - self.config.price_increment
                )
                if crossed:
                    self._start_episode(side, bar, atr)
                    episode = self._episodes[side]
                    reclaimed = bar.close < self._london.high if side is BoundarySide.HIGH else bar.close > self._london.low
                    if reclaimed:
                        self._mark_reclaim(episode, bar, atr)
                continue
            candidate = self._advance_episode(side, bar, atr, allow_entry)
            if candidate is not None and plan is None:
                plan = candidate
        return plan

    def on_bar(self, bar: BarObs, *, allow_entry: bool = True) -> TradePlan | None:
        self._minute_parts.append(bar)
        boundary = self.config.bar_minutes * NS_MINUTE
        if bar.ts_ns % boundary != 0:
            return None
        if len(self._minute_parts) != self.config.bar_minutes:
            self.skips["INCOMPLETE_AGGREGATION_BUCKET"] += 1
            self._minute_parts.clear()
            return None
        five = self._aggregate_five()
        self._minute_parts.clear()
        return self._on_five(five, allow_entry)

    def mark_plan_rejected(self, plan: TradePlan, ts_ns: int, reason: str, details: dict[str, Any] | None = None) -> None:
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
            reason_code="NAUTILUS_MARKET_BRACKET_SUBMITTED",
            reference_price=plan.expected_entry,
            details=details,
        )

    def mark_trade_terminal(self, plan: TradePlan, ts_ns: int, reason: str, details: dict[str, Any] | None = None) -> None:
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
    "BarObs", "BoundarySide", "CausalLiquidityAuctionEngine", "Direction", "FiveBar",
    "LogicConfig", "RiskSizer", "ScenarioKind", "SizeDecision", "TradePlan",
]
