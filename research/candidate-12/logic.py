"""Causal completed-session auction router for Candidate 12 I7.

The engine distinguishes economically different interactions with a completed
Asia or London dealing range instead of forcing one candle pattern onto every
crossing:

* premium-side buy-side raid -> failed auction rejection;
* sell-side raid -> bullish rejection only after an explicit reclaim and MSS;
* sustained buy-side acceptance -> bullish FVG mitigation and continuation;
* Asia high acceptance which fails back inside -> one fresh FVG re-acceptance.

Every decision uses completed five-minute observations only.  The module emits
trade plans; NautilusTrader remains the sole matching, fill, fee, margin,
position, contingent-order, and account-NAV authority.
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


class SessionLabel(str, Enum):
    ASIA = "ASIA"
    LONDON = "LONDON"


class EntryOrder(str, Enum):
    MARKET = "MARKET"
    LIMIT_GTD = "LIMIT_GTD_MARKETABLE_PROTECTED"


class ScenarioKind(str, Enum):
    ASIA_HIGH_REJECTION = "ASIA_HIGH_REJECTION"
    LONDON_HIGH_REJECTION = "LONDON_HIGH_REJECTION"
    ASIA_LOW_REJECTION = "ASIA_LOW_REJECTION"
    LONDON_LOW_REJECTION = "LONDON_LOW_REJECTION"
    ASIA_HIGH_ACCEPTANCE = "ASIA_HIGH_ACCEPTANCE"
    LONDON_HIGH_ACCEPTANCE = "LONDON_HIGH_ACCEPTANCE"
    ASIA_HIGH_REACCEPTANCE = "ASIA_HIGH_REACCEPTANCE"


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
    asia_start_minute: int = 0
    asia_end_minute: int = 360
    london_end_minute: int = 720
    new_york_end_minute: int = 1080

    reclaim_max_bars: int = 3
    confirmation_bars: int = 1
    rejection_reclaim_body_atr: float = 0.80
    asia_high_confirmation_body_atr: float = 0.50
    asia_high_confirmation_max_close_location: float = 0.35
    asia_high_min_pre_raid_location: float = 0.25
    low_confirmation_body_atr: float = 0.50
    low_confirmation_min_close_location: float = 0.65

    acceptance_closes: int = 2
    acceptance_displacement_body_atr: float = 0.80
    reacceptance_displacement_body_atr: float = 0.60
    acceptance_displacement_min_close_location: float = 0.65
    fvg_boundary_tolerance_atr: float = 0.15
    acceptance_retest_expiry_bars: int = 36
    active_retest_body_atr: float = 0.35
    active_retest_min_close_location: float = 0.75
    passive_retest_body_atr: float = 0.80
    passive_retest_max_close_location: float = 0.25
    fvg_stop_buffer_atr: float = 0.20
    limit_entry_expiry_bars: int = 1

    rejection_stop_buffer_atr: float = 0.80
    rejection_target_fraction: float = 0.60
    acceptance_market_projection: float = 1.00
    acceptance_limit_projection: float = 0.50
    reacceptance_projection: float = 1.00
    max_stop_atr: float = 5.0
    min_net_r: float = 0.65

    risk_fraction: float = 0.03
    effective_maker_rate: float = 0.0004
    effective_taker_rate: float = 0.0008
    tick_slippage_units: float = 2.0
    price_increment: float = 0.1

    def __post_init__(self) -> None:
        positive_ints = (
            "bar_minutes", "atr_period", "reclaim_max_bars", "confirmation_bars",
            "acceptance_closes", "acceptance_retest_expiry_bars", "limit_entry_expiry_bars",
        )
        for name in positive_ints:
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be within (0, 0.03]")
        for name in (
            "rejection_reclaim_body_atr", "asia_high_confirmation_body_atr",
            "low_confirmation_body_atr", "acceptance_displacement_body_atr",
            "reacceptance_displacement_body_atr", "active_retest_body_atr",
            "passive_retest_body_atr", "rejection_stop_buffer_atr",
            "fvg_stop_buffer_atr", "max_stop_atr",
        ):
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0 < self.asia_high_confirmation_max_close_location < 0.5:
            raise ValueError("Asia high confirmation must close in the lower half")
        if not 0.5 < self.low_confirmation_min_close_location < 1:
            raise ValueError("low confirmation must close in the upper half")
        if not 0 < self.asia_high_min_pre_raid_location < 0.5:
            raise ValueError("Asia pre-raid location must represent deep discount")
        if not 0.5 < self.acceptance_displacement_min_close_location < 1:
            raise ValueError("acceptance displacement must close in the upper half")
        if not 0.5 < self.active_retest_min_close_location < 1:
            raise ValueError("active retest must close in the upper half")
        if not 0 < self.passive_retest_max_close_location < 0.5:
            raise ValueError("passive retest must close in the lower half")
        if not 0 <= self.fvg_boundary_tolerance_atr <= 0.5:
            raise ValueError("invalid FVG boundary tolerance")
        if not 0.5 <= self.rejection_target_fraction <= 0.618:
            raise ValueError("rejection target must remain equilibrium-to-discount/premium")
        if self.acceptance_market_projection <= 0 or self.acceptance_limit_projection <= 0:
            raise ValueError("acceptance projections must be positive")
        if self.reacceptance_projection <= 0:
            raise ValueError("reacceptance projection must be positive")
        if self.max_stop_atr <= max(self.rejection_stop_buffer_atr, self.fvg_stop_buffer_atr):
            raise ValueError("max stop must exceed route buffers")
        if self.min_net_r < 0:
            raise ValueError("min_net_r cannot be negative")
        if self.price_increment <= 0:
            raise ValueError("price_increment must be positive")
        if not (
            0 <= self.asia_start_minute
            < self.asia_end_minute
            < self.london_end_minute
            < self.new_york_end_minute
            <= 1440
        ):
            raise ValueError("invalid session boundaries")


@dataclass(frozen=True, slots=True)
class TradePlan:
    scenario_id: str
    scenario: ScenarioKind
    direction: Direction
    entry_order: EntryOrder
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    expire_ts_ns: int | None
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
        return 0.5 if self.range <= 0 else (self.close - self.low) / self.range

    @property
    def signed_flow(self) -> float:
        if self.volume <= 0:
            return 0.0
        return max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))


@dataclass(frozen=True, slots=True)
class SessionRange:
    label: SessionLabel
    day_bucket: int
    high: float
    low: float
    close: float
    observed_ts_ns: int
    trade_start_minute: int
    trade_end_minute: int

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def close_location(self) -> float:
        return (self.close - self.low) / self.width


@dataclass(slots=True)
class RaidEpisode:
    scenario_id: str
    source: SessionRange
    side: str
    sweep_index: int
    sweep_ts_ns: int
    extreme: float
    phase: str = "WAIT_RECLAIM"
    reclaim_index: int | None = None
    reclaim_ts_ns: int | None = None
    reclaim_bar: FiveBar | None = None
    atr_at_reclaim: float | None = None
    confirm_index: int | None = None


@dataclass(frozen=True, slots=True)
class BullFVG:
    lower: float
    upper: float
    formed_index: int
    formed_ts_ns: int
    displacement_body_atr: float
    displacement_close_location: float


@dataclass(slots=True)
class _RangeBuilder:
    high: float = -math.inf
    low: float = math.inf
    close: float | None = None


@dataclass(slots=True)
class SourceState:
    source: SessionRange
    min_since_activity_open: float = math.inf
    trade_plan_emitted: bool = False
    high_rejection: RaidEpisode | None = None
    high_rejection_done: bool = False
    low_rejection: RaidEpisode | None = None
    low_rejection_done: bool = False
    outside_high_closes: int = 0
    active_fvg: BullFVG | None = None
    acceptance_phase: str = "WATCH"
    acceptance_started_index: int | None = None
    acceptance_scenario_id: str | None = None
    acceptance_pullback_low: float | None = None
    acceptance_peak: float | None = None
    initial_acceptance_attempted: bool = False
    had_high_acceptance: bool = False
    failed_high_acceptance: bool = False
    reacceptance_done: bool = False


class CausalLiquidityAuctionEngine:
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
        self._bars: Deque[FiveBar] = deque(maxlen=1_000)
        self._bar_atrs: Deque[float | None] = deque(maxlen=1_000)
        self._true_ranges: Deque[float] = deque(maxlen=config.atr_period)
        self._previous_five_close: float | None = None
        self._day_bucket: int | None = None
        self._builders: dict[SessionLabel, _RangeBuilder] = {}
        self._sources: dict[SessionLabel, SourceState] = {}
        self._range_history: list[SessionRange] = []
        self._scenario_counter = 0

    @property
    def pools(self) -> tuple[SessionRange, ...]:
        return tuple(self._range_history)

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
                    None if reference_price is None else f"{reference_price:.12f}".rstrip("0").rstrip(".")
                ),
                details=details or {},
            )
        )
        self._states[scenario_id] = next_state

    def _next_scenario_id(self, label: SessionLabel, route: str) -> str:
        self._scenario_counter += 1
        return f"{self.instrument_id}-{label.value}-{route}-{self._scenario_counter:06d}"

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

    def _session_spec(self, label: SessionLabel) -> tuple[int, int, int, int]:
        if label is SessionLabel.ASIA:
            return (
                self.config.asia_start_minute,
                self.config.asia_end_minute,
                self.config.asia_end_minute,
                self.config.london_end_minute,
            )
        return (
            self.config.asia_end_minute,
            self.config.london_end_minute,
            self.config.london_end_minute,
            self.config.new_york_end_minute,
        )

    def _roll_day(self, bar: FiveBar) -> None:
        day_bucket = (bar.ts_ns - 1) // NS_DAY
        if self._day_bucket == day_bucket:
            return
        self._day_bucket = day_bucket
        self._builders.clear()
        self._sources.clear()

    def _update_and_freeze_ranges(self, bar: FiveBar, minute: int) -> None:
        if self._day_bucket is None:
            return
        for label in (SessionLabel.ASIA, SessionLabel.LONDON):
            build_start, build_end, trade_start, trade_end = self._session_spec(label)
            if build_start < minute <= build_end:
                builder = self._builders.setdefault(label, _RangeBuilder())
                builder.high = max(builder.high, bar.high)
                builder.low = min(builder.low, bar.low)
                builder.close = bar.close
            if minute != build_end or label in self._sources:
                continue
            builder = self._builders.get(label)
            if (
                builder is None
                or not math.isfinite(builder.high)
                or not math.isfinite(builder.low)
                or builder.high <= builder.low
                or builder.close is None
            ):
                self.skips[f"INVALID_{label.value}_RANGE"] += 1
                continue
            source = SessionRange(
                label=label,
                day_bucket=self._day_bucket,
                high=builder.high,
                low=builder.low,
                close=builder.close,
                observed_ts_ns=bar.ts_ns,
                trade_start_minute=trade_start,
                trade_end_minute=trade_end,
            )
            self._sources[label] = SourceState(source=source)
            self._range_history.append(source)
            self.pool_counts[f"{label.value}_RANGE"] += 1
            range_id = f"{self.instrument_id}-{label.value}-RANGE-{self._day_bucket}"
            self._emit(
                scenario_id=range_id,
                event_type="SESSION_RANGE_FROZEN",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                next_state="RANGE_FROZEN",
                reason_code=f"COMPLETED_{label.value}_SESSION_RANGE",
                details={
                    "label": label.value,
                    "high": source.high,
                    "low": source.low,
                    "close": source.close,
                    "width": source.width,
                    "close_location": source.close_location,
                    "trade_start_minute": trade_start,
                    "trade_end_minute": trade_end,
                    "weekday": self._weekday(source.day_bucket),
                },
            )

    def _round_price(self, value: float, rounding: str) -> float:
        increment = Decimal(str(self.config.price_increment))
        mode = ROUND_CEILING if rounding == "CEIL" else ROUND_DOWN
        units = (Decimal(str(value)) / increment).to_integral_value(rounding=mode)
        return float(units * increment)

    def _costed_plan(
        self,
        *,
        scenario_id: str,
        scenario: ScenarioKind,
        direction: Direction,
        entry_order: EntryOrder,
        observed_ts_ns: int,
        bar: FiveBar,
        atr: float,
        entry_raw: float,
        stop_raw: float,
        target_raw: float,
        expire_ts_ns: int | None,
        details: dict[str, Any],
    ) -> TradePlan | None:
        if direction is Direction.LONG:
            entry = self._round_price(entry_raw, "CEIL")
            stop = self._round_price(stop_raw, "FLOOR")
            target = self._round_price(target_raw, "FLOOR")
            structural_loss = entry - stop
            structural_profit = target - entry
            target_preconsumed = bar.high >= target
        else:
            entry = self._round_price(entry_raw, "FLOOR")
            stop = self._round_price(stop_raw, "CEIL")
            target = self._round_price(target_raw, "CEIL")
            structural_loss = stop - entry
            structural_profit = entry - target
            target_preconsumed = bar.low <= target
        if structural_loss <= 0 or structural_loss > self.config.max_stop_atr * atr:
            self.skips["INVALID_STRUCTURAL_STOP"] += 1
            return None
        if structural_profit <= 0:
            self.skips["INVALID_STRUCTURAL_TARGET"] += 1
            return None
        if target_preconsumed:
            self.skips["STRUCTURAL_TARGET_REACHED_BEFORE_DECISION"] += 1
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
        merged = dict(details)
        merged.update(
            {
                "entry_order_type": entry_order.value,
                "entry_cost_per_unit": entry_cost,
                "stop_cost_per_unit": stop_cost,
                "target_cost_per_unit": target_cost,
                "slippage_allowance_per_unit": slippage,
                "decision_atr": atr,
            }
        )
        return TradePlan(
            scenario_id=scenario_id,
            scenario=scenario,
            direction=direction,
            entry_order=entry_order,
            observed_ts_ns=observed_ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            expire_ts_ns=expire_ts_ns,
            loss_per_unit=loss_per_unit,
            expected_profit_per_unit=expected_profit,
            net_r=net_r,
            details=merged,
        )

    def _emit_plan(self, plan: TradePlan, allow_entry: bool) -> TradePlan | None:
        if not allow_entry:
            self.skips["GLOBAL_SLOT_OR_EVALUATION_BLOCKED"] += 1
            self._emit(
                scenario_id=plan.scenario_id,
                event_type="TRADE_PLAN_REJECTED",
                event_time_ns=plan.observed_ts_ns,
                observed_time_ns=plan.observed_ts_ns,
                next_state="TERMINAL",
                reason_code="GLOBAL_SLOT_OR_EVALUATION_BLOCKED",
                reference_price=plan.expected_entry,
                details={"scenario": plan.scenario.value, "net_r": plan.net_r},
            )
            return None
        self._emit(
            scenario_id=plan.scenario_id,
            event_type="TRADE_PLAN_EMITTED",
            event_time_ns=plan.observed_ts_ns,
            observed_time_ns=plan.observed_ts_ns,
            next_state="PLAN_EMITTED",
            reason_code="COSTED_CAUSAL_SESSION_AUCTION_PLAN_VALID",
            reference_price=plan.expected_entry,
            details={
                "scenario": plan.scenario.value,
                "direction": plan.direction.value,
                "entry_order": plan.entry_order.value,
                "entry": plan.expected_entry,
                "stop": plan.stop_price,
                "target": plan.target_price,
                "expire_ts_ns": plan.expire_ts_ns,
                "net_r": plan.net_r,
            },
        )
        return plan

    def _start_raid(self, state: SourceState, side: str, bar: FiveBar, atr: float) -> RaidEpisode:
        source = state.source
        route = f"{side}-RAID"
        scenario_id = self._next_scenario_id(source.label, route)
        extreme = bar.high if side == "HIGH" else bar.low
        episode = RaidEpisode(
            scenario_id=scenario_id,
            source=source,
            side=side,
            sweep_index=self._five_index,
            sweep_ts_ns=bar.ts_ns,
            extreme=extreme,
        )
        self.scenario_counts[f"{source.label.value}_{side}_RAID"] += 1
        self._emit(
            scenario_id=scenario_id,
            event_type=f"SESSION_{side}_RAID_DETECTED",
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="WAIT_RECLAIM",
            reason_code=f"PRICE_TRADED_BEYOND_COMPLETED_{source.label.value}_{side}",
            reference_price=source.high if side == "HIGH" else source.low,
            details={
                "source": source.label.value,
                "session_high": source.high,
                "session_low": source.low,
                "session_close_location": source.close_location,
                "raid_extreme": extreme,
                "penetration_atr": (
                    (bar.high - source.high) / atr if side == "HIGH" else (source.low - bar.low) / atr
                ),
            },
        )
        return episode

    def _mark_raid_reclaim(self, episode: RaidEpisode, bar: FiveBar, atr: float) -> None:
        episode.phase = "WAIT_CONFIRM"
        episode.reclaim_index = self._five_index
        episode.reclaim_ts_ns = bar.ts_ns
        episode.reclaim_bar = bar
        episode.atr_at_reclaim = atr
        episode.confirm_index = self._five_index + self.config.confirmation_bars
        self._emit(
            scenario_id=episode.scenario_id,
            event_type=f"SESSION_{episode.side}_RECLAIM_OBSERVED",
            event_time_ns=episode.sweep_ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="WAIT_CONFIRM",
            reason_code=f"COMPLETED_BAR_CLOSED_BACK_INSIDE_{episode.side}_BOUNDARY",
            reference_price=episode.source.high if episode.side == "HIGH" else episode.source.low,
            details={
                "source": episode.source.label.value,
                "raid_extreme": episode.extreme,
                "bars_from_sweep": self._five_index - episode.sweep_index,
                "atr_at_reclaim": atr,
                "reclaim_body_atr": bar.body / atr,
                "reclaim_close_location": bar.close_location,
                "reclaim_direction": "UP" if bar.close > bar.open else "DOWN",
                "reclaim_flow": bar.signed_flow,
            },
        )

    def _invalidate_raid(self, state: SourceState, side: str, bar: FiveBar, reason: str) -> None:
        episode = state.high_rejection if side == "HIGH" else state.low_rejection
        if episode is None:
            return
        self.skips[reason] += 1
        self._emit(
            scenario_id=episode.scenario_id,
            event_type="SCENARIO_INVALIDATED",
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="TERMINAL",
            reason_code=reason,
            reference_price=episode.source.high if side == "HIGH" else episode.source.low,
            details={"source": episode.source.label.value, "side": side, "phase": episode.phase},
        )
        if side == "HIGH":
            state.high_rejection = None
            state.high_rejection_done = True
        else:
            state.low_rejection = None
            state.low_rejection_done = True

    def _advance_high_rejection(
        self, state: SourceState, bar: FiveBar, atr: float, allow_entry: bool
    ) -> TradePlan | None:
        source = state.source
        if state.trade_plan_emitted:
            return None
        if state.high_rejection_done:
            return None
        episode = state.high_rejection
        if episode is None:
            if bar.high < source.high + self.config.price_increment:
                return None
            if source.label is SessionLabel.ASIA:
                min_location = (state.min_since_activity_open - source.low) / source.width
                if min_location < self.config.asia_high_min_pre_raid_location:
                    state.high_rejection_done = True
                    reason = "ASIA_HIGH_RAID_AFTER_DEEP_DISCOUNT_TRAVERSAL"
                    self.skips[reason] += 1
                    sid = self._next_scenario_id(source.label, "HIGH-REJECTION-SKIP")
                    self._emit(
                        scenario_id=sid,
                        event_type="SCENARIO_INVALIDATED",
                        event_time_ns=bar.ts_ns,
                        observed_time_ns=bar.ts_ns,
                        next_state="TERMINAL",
                        reason_code=reason,
                        reference_price=source.high,
                        details={
                            "source": source.label.value,
                            "minimum_location_before_raid": min_location,
                            "required_minimum_location": self.config.asia_high_min_pre_raid_location,
                        },
                    )
                    return None
            episode = self._start_raid(state, "HIGH", bar, atr)
            state.high_rejection = episode
            if bar.close < source.high:
                self._mark_raid_reclaim(episode, bar, atr)
            return None

        if episode.phase == "WAIT_RECLAIM":
            episode.extreme = max(episode.extreme, bar.high)
            if bar.close < source.high:
                self._mark_raid_reclaim(episode, bar, atr)
                return None
            if self._five_index - episode.sweep_index >= self.config.reclaim_max_bars:
                self._invalidate_raid(state, "HIGH", bar, "HIGH_BOUNDARY_ACCEPTED_NOT_RECLAIMED")
            return None

        assert episode.confirm_index is not None
        if self._five_index < episode.confirm_index:
            return None
        assert episode.reclaim_bar is not None and episode.atr_at_reclaim is not None
        reclaim = episode.reclaim_bar
        reclaim_atr = episode.atr_at_reclaim
        if reclaim.body / reclaim_atr < self.config.rejection_reclaim_body_atr:
            self._invalidate_raid(state, "HIGH", bar, "HIGH_RECLAIM_LACKED_DISPLACEMENT")
            return None
        invalidation = episode.extreme + self.config.rejection_stop_buffer_atr * reclaim_atr
        if bar.high >= invalidation:
            self._invalidate_raid(state, "HIGH", bar, "HIGH_RAID_EXTREME_NOT_DEFENDED")
            return None
        if source.label is SessionLabel.ASIA:
            confirmed = (
                bar.close < bar.open
                and bar.body / atr >= self.config.asia_high_confirmation_body_atr
                and bar.close_location <= self.config.asia_high_confirmation_max_close_location
            )
            if not confirmed:
                self._invalidate_raid(state, "HIGH", bar, "ASIA_HIGH_REJECTION_LACKED_CONFIRMATION")
                return None
        scenario = (
            ScenarioKind.ASIA_HIGH_REJECTION
            if source.label is SessionLabel.ASIA
            else ScenarioKind.LONDON_HIGH_REJECTION
        )
        target = source.high - self.config.rejection_target_fraction * source.width
        plan = self._costed_plan(
            scenario_id=episode.scenario_id,
            scenario=scenario,
            direction=Direction.SHORT,
            entry_order=EntryOrder.MARKET,
            observed_ts_ns=bar.ts_ns,
            bar=bar,
            atr=reclaim_atr,
            entry_raw=bar.close,
            stop_raw=invalidation,
            target_raw=target,
            expire_ts_ns=None,
            details={
                "source": source.label.value,
                "route": "BUY_SIDE_FAILED_AUCTION",
                "session_high": source.high,
                "session_low": source.low,
                "session_width": source.width,
                "raid_extreme": episode.extreme,
                "sweep_ts_ns": episode.sweep_ts_ns,
                "reclaim_ts_ns": episode.reclaim_ts_ns,
                "decision_body_atr": bar.body / atr,
                "decision_close_location": bar.close_location,
                "decision_flow": bar.signed_flow,
                "target_semantics": "COMPLETED_RANGE_DISCOUNT_OBJECTIVE",
            },
        )
        state.high_rejection = None
        state.high_rejection_done = True
        if plan is None:
            self.skips["HIGH_REJECTION_COSTED_PLAN_REJECTED"] += 1
            return None
        state.trade_plan_emitted = True
        return self._emit_plan(plan, allow_entry)

    def _advance_low_rejection(
        self, state: SourceState, bar: FiveBar, atr: float, allow_entry: bool
    ) -> TradePlan | None:
        source = state.source
        if state.trade_plan_emitted:
            return None
        if state.low_rejection_done:
            return None
        episode = state.low_rejection
        if episode is None:
            if bar.low > source.low - self.config.price_increment:
                return None
            episode = self._start_raid(state, "LOW", bar, atr)
            state.low_rejection = episode
            if bar.close > source.low:
                self._mark_raid_reclaim(episode, bar, atr)
            return None

        if episode.phase == "WAIT_RECLAIM":
            episode.extreme = min(episode.extreme, bar.low)
            if bar.close > source.low:
                self._mark_raid_reclaim(episode, bar, atr)
                return None
            if self._five_index - episode.sweep_index >= self.config.reclaim_max_bars:
                self._invalidate_raid(state, "LOW", bar, "LOW_BOUNDARY_ACCEPTED_NOT_RECLAIMED")
            return None

        assert episode.confirm_index is not None
        if self._five_index < episode.confirm_index:
            return None
        assert episode.reclaim_bar is not None and episode.atr_at_reclaim is not None
        reclaim = episode.reclaim_bar
        reclaim_atr = episode.atr_at_reclaim
        reclaimed = (
            reclaim.close > reclaim.open
            and reclaim.body / reclaim_atr >= self.config.rejection_reclaim_body_atr
            and reclaim.close_location >= self.config.low_confirmation_min_close_location
        )
        if not reclaimed:
            self._invalidate_raid(state, "LOW", bar, "LOW_RECLAIM_LACKED_BULLISH_DISPLACEMENT")
            return None
        invalidation = episode.extreme - self.config.rejection_stop_buffer_atr * reclaim_atr
        confirmed = (
            bar.close > bar.open
            and bar.body / atr >= self.config.low_confirmation_body_atr
            and bar.close_location >= self.config.low_confirmation_min_close_location
            and bar.close > reclaim.high
        )
        if not confirmed:
            self._invalidate_raid(state, "LOW", bar, "LOW_REJECTION_LACKED_BULLISH_MSS")
            return None
        if bar.low <= invalidation:
            self._invalidate_raid(state, "LOW", bar, "LOW_RAID_EXTREME_NOT_DEFENDED")
            return None
        scenario = (
            ScenarioKind.ASIA_LOW_REJECTION
            if source.label is SessionLabel.ASIA
            else ScenarioKind.LONDON_LOW_REJECTION
        )
        target = source.low + self.config.rejection_target_fraction * source.width
        plan = self._costed_plan(
            scenario_id=episode.scenario_id,
            scenario=scenario,
            direction=Direction.LONG,
            entry_order=EntryOrder.MARKET,
            observed_ts_ns=bar.ts_ns,
            bar=bar,
            atr=reclaim_atr,
            entry_raw=bar.close,
            stop_raw=invalidation,
            target_raw=target,
            expire_ts_ns=None,
            details={
                "source": source.label.value,
                "route": "SELL_SIDE_FAILED_AUCTION_WITH_BULLISH_MSS",
                "session_high": source.high,
                "session_low": source.low,
                "session_width": source.width,
                "raid_extreme": episode.extreme,
                "sweep_ts_ns": episode.sweep_ts_ns,
                "reclaim_ts_ns": episode.reclaim_ts_ns,
                "reclaim_high": reclaim.high,
                "decision_body_atr": bar.body / atr,
                "decision_close_location": bar.close_location,
                "decision_flow": bar.signed_flow,
                "target_semantics": "COMPLETED_RANGE_PREMIUM_OBJECTIVE",
            },
        )
        state.low_rejection = None
        state.low_rejection_done = True
        if plan is None:
            self.skips["LOW_REJECTION_COSTED_PLAN_REJECTED"] += 1
            return None
        state.trade_plan_emitted = True
        return self._emit_plan(plan, allow_entry)

    def _fresh_bull_fvg(self, source: SessionRange, bar: FiveBar, atr: float) -> BullFVG | None:
        if len(self._bars) < 2 or len(self._bar_atrs) < 2:
            return None
        first = self._bars[-2]
        displacement = self._bars[-1]
        displacement_atr = self._bar_atrs[-1]
        if displacement_atr is None or displacement_atr <= 0:
            return None
        if not (
            bar.low > first.high
            and displacement.close > displacement.open
            and displacement.close > source.high
            and bar.close > source.high
            and displacement.close_location >= self.config.acceptance_displacement_min_close_location
            and bar.low >= source.high - self.config.fvg_boundary_tolerance_atr * atr
        ):
            return None
        return BullFVG(
            lower=first.high,
            upper=bar.low,
            formed_index=self._five_index,
            formed_ts_ns=bar.ts_ns,
            displacement_body_atr=displacement.body / displacement_atr,
            displacement_close_location=displacement.close_location,
        )

    def _acceptance_plan(
        self,
        *,
        state: SourceState,
        bar: FiveBar,
        atr: float,
        fvg: BullFVG,
        entry_order: EntryOrder,
        scenario_id: str,
    ) -> TradePlan | None:
        source = state.source
        scenario = (
            ScenarioKind.ASIA_HIGH_ACCEPTANCE
            if source.label is SessionLabel.ASIA
            else ScenarioKind.LONDON_HIGH_ACCEPTANCE
        )
        if entry_order is EntryOrder.MARKET:
            entry_raw = bar.close
            stop_raw = bar.low - self.config.fvg_stop_buffer_atr * atr
            target_raw = source.high + self.config.acceptance_market_projection * source.width
            expire_ts_ns = None
            route = "ACTIVE_FVG_RETEST_HOLD"
        else:
            entry_raw = fvg.upper
            stop_raw = fvg.lower - self.config.fvg_stop_buffer_atr * atr
            target_raw = source.high + self.config.acceptance_limit_projection * source.width
            expire_ts_ns = bar.ts_ns + self.config.limit_entry_expiry_bars * self.config.bar_minutes * NS_MINUTE
            route = "PASSIVE_FVG_MITIGATION_HOLD"
        return self._costed_plan(
            scenario_id=scenario_id,
            scenario=scenario,
            direction=Direction.LONG,
            entry_order=entry_order,
            observed_ts_ns=bar.ts_ns,
            bar=bar,
            atr=atr,
            entry_raw=entry_raw,
            stop_raw=stop_raw,
            target_raw=target_raw,
            expire_ts_ns=expire_ts_ns,
            details={
                "source": source.label.value,
                "route": route,
                "session_high": source.high,
                "session_low": source.low,
                "session_width": source.width,
                "fvg_lower": fvg.lower,
                "fvg_upper": fvg.upper,
                "fvg_formed_ts_ns": fvg.formed_ts_ns,
                "retest_low": bar.low,
                "retest_close": bar.close,
                "retest_body_atr": bar.body / atr,
                "retest_close_location": bar.close_location,
                "retest_flow": bar.signed_flow,
                "target_semantics": (
                    "FULL_COMPLETED_RANGE_EXPANSION"
                    if entry_order is EntryOrder.MARKET
                    else "HALF_COMPLETED_RANGE_EXPANSION"
                ),
            },
        )

    def _reacceptance_plan(
        self,
        *,
        state: SourceState,
        bar: FiveBar,
        atr: float,
        fvg: BullFVG,
    ) -> TradePlan | None:
        source = state.source
        scenario_id = self._next_scenario_id(source.label, "HIGH-REACCEPTANCE")
        target = source.high + self.config.reacceptance_projection * source.width
        return self._costed_plan(
            scenario_id=scenario_id,
            scenario=ScenarioKind.ASIA_HIGH_REACCEPTANCE,
            direction=Direction.LONG,
            entry_order=EntryOrder.MARKET,
            observed_ts_ns=bar.ts_ns,
            bar=bar,
            atr=atr,
            entry_raw=bar.close,
            stop_raw=source.high - self.config.fvg_stop_buffer_atr * atr,
            target_raw=target,
            expire_ts_ns=None,
            details={
                "source": source.label.value,
                "route": "FAILED_FIRST_ACCEPTANCE_THEN_FRESH_ASIA_REACCEPTANCE",
                "session_high": source.high,
                "session_low": source.low,
                "session_width": source.width,
                "fvg_lower": fvg.lower,
                "fvg_upper": fvg.upper,
                "fvg_formed_ts_ns": fvg.formed_ts_ns,
                "displacement_body_atr": fvg.displacement_body_atr,
                "displacement_close_location": fvg.displacement_close_location,
                "decision_flow": bar.signed_flow,
                "target_semantics": "FULL_COMPLETED_RANGE_EXPANSION",
            },
        )

    def _advance_high_acceptance(
        self, state: SourceState, bar: FiveBar, atr: float, allow_entry: bool
    ) -> TradePlan | None:
        source = state.source
        if state.trade_plan_emitted:
            return None
        # A completed close back inside terminates the current acceptance and
        # creates the only causal prerequisite for an Asia re-acceptance route.
        if bar.close < source.high and state.had_high_acceptance:
            if state.acceptance_phase in ("WAIT_RETEST", "WAIT_REACCELERATION", "MONITOR_FAILURE"):
                state.failed_high_acceptance = True
                state.acceptance_phase = "WATCH"
                state.outside_high_closes = 0
                state.active_fvg = None
                state.acceptance_started_index = None
                state.acceptance_scenario_id = None
                state.acceptance_pullback_low = None
                state.acceptance_peak = None
                sid = self._next_scenario_id(source.label, "HIGH-ACCEPTANCE-FAILURE")
                self._emit(
                    scenario_id=sid,
                    event_type="HIGH_ACCEPTANCE_FAILED_BACK_INSIDE",
                    event_time_ns=bar.ts_ns,
                    observed_time_ns=bar.ts_ns,
                    next_state="TERMINAL",
                    reason_code="COMPLETED_CLOSE_RETURNED_INSIDE_SESSION_RANGE",
                    reference_price=source.high,
                    details={"source": source.label.value, "close": bar.close},
                )

        if state.acceptance_phase == "WATCH":
            if bar.close > source.high:
                state.outside_high_closes += 1
            else:
                state.outside_high_closes = 0
            fresh = self._fresh_bull_fvg(source, bar, atr)

            if (
                state.failed_high_acceptance
                and source.label is SessionLabel.ASIA
                and not state.reacceptance_done
                and fresh is not None
                and fresh.displacement_body_atr >= self.config.reacceptance_displacement_body_atr
                and state.outside_high_closes >= self.config.acceptance_closes
            ):
                state.reacceptance_done = True
                state.failed_high_acceptance = False
                plan = self._reacceptance_plan(state=state, bar=bar, atr=atr, fvg=fresh)
                if plan is None:
                    self.skips["ASIA_REACCEPTANCE_COSTED_PLAN_REJECTED"] += 1
                    return None
                self.scenario_counts[ScenarioKind.ASIA_HIGH_REACCEPTANCE.value] += 1
                state.trade_plan_emitted = True
                return self._emit_plan(plan, allow_entry)

            if (
                not state.initial_acceptance_attempted
                and fresh is not None
                and fresh.displacement_body_atr >= self.config.acceptance_displacement_body_atr
            ):
                state.active_fvg = fresh

            if (
                not state.initial_acceptance_attempted
                and state.active_fvg is not None
                and state.outside_high_closes >= self.config.acceptance_closes
            ):
                state.had_high_acceptance = True
                state.acceptance_phase = "WAIT_RETEST"
                state.acceptance_started_index = self._five_index
                state.acceptance_peak = bar.high
                sid = self._next_scenario_id(source.label, "HIGH-ACCEPTANCE")
                state.acceptance_scenario_id = sid
                self.scenario_counts[f"{source.label.value}_HIGH_ACCEPTANCE"] += 1
                self._emit(
                    scenario_id=sid,
                    event_type="SESSION_HIGH_ACCEPTANCE_CONFIRMED",
                    event_time_ns=state.active_fvg.formed_ts_ns,
                    observed_time_ns=bar.ts_ns,
                    next_state="WAIT_FVG_RETEST",
                    reason_code="MULTI_CLOSE_DISPLACEMENT_CREATED_BULLISH_FVG_ABOVE_RANGE",
                    reference_price=source.high,
                    details={
                        "source": source.label.value,
                        "outside_closes": state.outside_high_closes,
                        "fvg_lower": state.active_fvg.lower,
                        "fvg_upper": state.active_fvg.upper,
                        "displacement_body_atr": state.active_fvg.displacement_body_atr,
                    },
                )
            return None

        if state.acceptance_phase == "WAIT_RETEST":
            assert state.active_fvg is not None and state.acceptance_started_index is not None
            state.acceptance_peak = max(state.acceptance_peak or source.high, bar.high)
            if self._five_index - state.acceptance_started_index > self.config.acceptance_retest_expiry_bars:
                state.acceptance_phase = "MONITOR_FAILURE"
                self.skips["ACCEPTANCE_FVG_RETEST_EXPIRED"] += 1
                return None
            if bar.low > state.active_fvg.upper or bar.ts_ns <= state.active_fvg.formed_ts_ns:
                return None
            if bar.close <= source.high:
                return None
            active = (
                bar.close > bar.open
                and bar.body / atr >= self.config.active_retest_body_atr
                and bar.close_location >= self.config.active_retest_min_close_location
                and bar.close > state.active_fvg.upper
            )
            passive = (
                bar.low >= state.active_fvg.lower
                and bar.close < bar.open
                and bar.body / atr >= self.config.passive_retest_body_atr
                and bar.close_location <= self.config.passive_retest_max_close_location
            )
            # Trading through the FVG lower edge is not passive absorption.
            # If the completed session boundary still holds, wait for a fresh
            # bullish FVG before considering a protected continuation limit.
            if bar.low < state.active_fvg.lower and not active:
                state.initial_acceptance_attempted = True
                state.acceptance_phase = "WAIT_REACCELERATION"
                state.acceptance_started_index = self._five_index
                state.acceptance_pullback_low = bar.low
                sid = state.acceptance_scenario_id
                if sid is None:
                    sid = self._next_scenario_id(source.label, "HIGH-ACCEPTANCE")
                    state.acceptance_scenario_id = sid
                self.skips["INITIAL_FVG_LOWER_EDGE_BREACHED"] += 1
                self._emit(
                    scenario_id=sid,
                    event_type="INITIAL_FVG_MITIGATION_FAILED",
                    event_time_ns=bar.ts_ns,
                    observed_time_ns=bar.ts_ns,
                    next_state="WAIT_REACCELERATION",
                    reason_code="FVG_LOWER_EDGE_TRADED_THROUGH_BUT_SESSION_HIGH_HELD",
                    reference_price=state.active_fvg.lower,
                    details={
                        "source": source.label.value,
                        "session_high": source.high,
                        "pullback_low": bar.low,
                        "fvg_lower": state.active_fvg.lower,
                        "fvg_upper": state.active_fvg.upper,
                    },
                )
                state.active_fvg = None
                return None

            # The first completed mitigation consumes this acceptance attempt.
            # A weak/indecisive mitigation is not a trade, but it is still the
            # causal event which can later make an Asia re-acceptance eligible.
            state.initial_acceptance_attempted = True
            state.acceptance_phase = "MONITOR_FAILURE"
            sid = state.acceptance_scenario_id
            if sid is None:
                sid = self._next_scenario_id(source.label, "HIGH-ACCEPTANCE")
                state.acceptance_scenario_id = sid
            if not active and not passive:
                self.skips["FVG_RETEST_NOT_EXECUTABLE"] += 1
                self._emit(
                    scenario_id=sid,
                    event_type="ACCEPTED_FVG_RETEST_REJECTED",
                    event_time_ns=bar.ts_ns,
                    observed_time_ns=bar.ts_ns,
                    next_state="MONITOR_FAILURE",
                    reason_code="FIRST_FVG_MITIGATION_LACKED_ACTIVE_HOLD_OR_PASSIVE_EXHAUSTION",
                    reference_price=state.active_fvg.upper,
                    details={
                        "source": source.label.value,
                        "retest_body_atr": bar.body / atr,
                        "retest_close_location": bar.close_location,
                        "retest_close": bar.close,
                    },
                )
                return None
            entry_order = EntryOrder.MARKET if active else EntryOrder.LIMIT_GTD
            plan = self._acceptance_plan(
                state=state,
                bar=bar,
                atr=atr,
                fvg=state.active_fvg,
                entry_order=entry_order,
                scenario_id=sid,
            )
            if plan is None:
                self.skips["HIGH_ACCEPTANCE_COSTED_PLAN_REJECTED"] += 1
                return None
            state.trade_plan_emitted = True
            return self._emit_plan(plan, allow_entry)

        if state.acceptance_phase == "WAIT_REACCELERATION":
            assert state.acceptance_started_index is not None
            if self._five_index - state.acceptance_started_index > self.config.acceptance_retest_expiry_bars:
                state.acceptance_phase = "MONITOR_FAILURE"
                self.skips["ACCEPTANCE_REACCELERATION_EXPIRED"] += 1
                return None
            fresh = self._fresh_bull_fvg(source, bar, atr)
            if (
                fresh is None
                or fresh.displacement_body_atr < self.config.acceptance_displacement_body_atr
                or bar.close <= source.high
            ):
                return None
            state.active_fvg = fresh
            state.acceptance_phase = "MONITOR_FAILURE"
            sid = state.acceptance_scenario_id
            if sid is None:
                sid = self._next_scenario_id(source.label, "HIGH-ACCEPTANCE-REACCELERATION")
                state.acceptance_scenario_id = sid
            prior_peak = state.acceptance_peak or source.high
            plan = self._costed_plan(
                scenario_id=sid,
                scenario=(
                    ScenarioKind.ASIA_HIGH_ACCEPTANCE
                    if source.label is SessionLabel.ASIA
                    else ScenarioKind.LONDON_HIGH_ACCEPTANCE
                ),
                direction=Direction.LONG,
                entry_order=EntryOrder.LIMIT_GTD,
                observed_ts_ns=bar.ts_ns,
                bar=bar,
                atr=atr,
                entry_raw=fresh.upper,
                stop_raw=fresh.lower - self.config.fvg_stop_buffer_atr * atr,
                target_raw=prior_peak,
                expire_ts_ns=(
                    bar.ts_ns
                    + self.config.limit_entry_expiry_bars
                    * self.config.bar_minutes
                    * NS_MINUTE
                ),
                details={
                    "source": source.label.value,
                    "route": "FVG_BREACH_HELD_BOUNDARY_THEN_FRESH_REACCELERATION_LIMIT",
                    "session_high": source.high,
                    "session_low": source.low,
                    "session_width": source.width,
                    "fvg_lower": fresh.lower,
                    "fvg_upper": fresh.upper,
                    "fvg_formed_ts_ns": fresh.formed_ts_ns,
                    "initial_pullback_low": state.acceptance_pullback_low,
                    "prior_acceptance_peak": prior_peak,
                    "target_semantics": "PRIOR_ACCEPTANCE_EXPANSION_HIGH",
                },
            )
            if plan is None:
                self.skips["ACCEPTANCE_REACCELERATION_COSTED_PLAN_REJECTED"] += 1
                return None
            state.trade_plan_emitted = True
            return self._emit_plan(plan, allow_entry)

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
        self._update_and_freeze_ranges(bar, minute)

        plan: TradePlan | None = None
        if atr is not None and self._day_bucket is not None and self._weekday(self._day_bucket) < 5:
            for label in (SessionLabel.ASIA, SessionLabel.LONDON):
                state = self._sources.get(label)
                if state is None:
                    continue
                source = state.source
                if not (source.trade_start_minute < minute <= source.trade_end_minute):
                    continue
                state.min_since_activity_open = min(state.min_since_activity_open, bar.low)
                route_allow = allow_entry and plan is None
                candidate = self._advance_high_rejection(state, bar, atr, route_allow)
                if candidate is not None and plan is None:
                    plan = candidate

                route_allow = allow_entry and plan is None
                candidate = self._advance_low_rejection(state, bar, atr, route_allow)
                if candidate is not None and plan is None:
                    plan = candidate

                route_allow = allow_entry and plan is None
                candidate = self._advance_high_acceptance(state, bar, atr, route_allow)
                if candidate is not None and plan is None:
                    plan = candidate
        self._bars.append(bar)
        self._bar_atrs.append(atr)
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
            reason_code="NAUTILUS_ORDER_LIST_SUBMITTED",
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
    "BarObs", "CausalLiquidityAuctionEngine", "Direction", "EntryOrder", "LogicConfig",
    "RiskSizer", "ScenarioKind", "SizeDecision", "TradePlan",
]
