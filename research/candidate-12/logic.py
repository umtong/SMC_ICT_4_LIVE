"""Causal session-high failed-auction state machine for Candidate 12.

The executable scenario is deliberately asymmetric:

* During London, a completed Asia-session high raid must reclaim with material
  displacement and then print a completed bearish confirmation bar.
* During New York, a completed London-session high raid must reclaim with
  material displacement and survive one completed confirmation bar.

Low-side raids and accepted breakouts remain observable market states, but are
not executable until a separately verified causal route exists.  This prevents
one entry rule from being forced onto economically different auction outcomes.

The module emits causal trade plans only.  NautilusTrader remains the sole
matching, fill, fee, margin, position, contingent-order, and account-NAV
authority.
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


class ScenarioKind(str, Enum):
    ASIA_HIGH_REJECTION = "ASIA_HIGH_REJECTION"
    LONDON_HIGH_REJECTION = "LONDON_HIGH_REJECTION"


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
    reclaim_body_atr: float = 0.80
    asia_confirmation_body_atr: float = 0.50
    asia_confirmation_max_close_location: float = 0.35
    stop_buffer_atr: float = 0.80
    rejection_target_fraction: float = 0.60
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
        if not 0 < self.reclaim_body_atr:
            raise ValueError("reclaim_body_atr must be positive")
        if not 0 < self.asia_confirmation_body_atr:
            raise ValueError("asia_confirmation_body_atr must be positive")
        if not 0 < self.asia_confirmation_max_close_location < 0.5:
            raise ValueError("Asia confirmation must close in the lower half")
        if not 0.5 <= self.rejection_target_fraction <= 0.618:
            raise ValueError("rejection target must remain equilibrium-to-discount")
        if self.stop_buffer_atr <= 0 or self.max_stop_atr <= self.stop_buffer_atr:
            raise ValueError("invalid stop-distance bounds")
        if self.price_increment <= 0:
            raise ValueError("price_increment must be positive")
        if self.min_net_r < 0:
            raise ValueError("min_net_r cannot be negative")
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
class HighRaidEpisode:
    scenario_id: str
    source: SessionRange
    sweep_index: int
    sweep_ts_ns: int
    extreme: float
    phase: str = "WAIT_RECLAIM"
    reclaim_index: int | None = None
    reclaim_ts_ns: int | None = None
    reclaim_bar: FiveBar | None = None
    atr_at_reclaim: float | None = None
    confirm_index: int | None = None


@dataclass(slots=True)
class _RangeBuilder:
    high: float = -math.inf
    low: float = math.inf
    close: float | None = None


class CausalLiquidityAuctionEngine:
    """Completed-session buy-side raid and failed-auction router."""

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
        self._builders: dict[SessionLabel, _RangeBuilder] = {}
        self._ranges: dict[SessionLabel, SessionRange] = {}
        self._range_history: list[SessionRange] = []
        self._episodes: dict[SessionLabel, HighRaidEpisode] = {}
        self._done: set[SessionLabel] = set()
        self._observed_low_raids: set[SessionLabel] = set()
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
        for label in tuple(self._episodes):
            self._terminate(label, bar.ts_ns, "DAY_ROLLOVER")
        self._day_bucket = day_bucket
        self._builders.clear()
        self._ranges.clear()
        self._episodes.clear()
        self._done.clear()
        self._observed_low_raids.clear()

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
            if minute != build_end or label in self._ranges:
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
            self._ranges[label] = source
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
                    "trade_start_minute": source.trade_start_minute,
                    "trade_end_minute": source.trade_end_minute,
                    "weekday": self._weekday(source.day_bucket),
                },
            )

    def _observe_low_raid(self, source: SessionRange, bar: FiveBar) -> None:
        if source.label in self._observed_low_raids:
            return
        if bar.low > source.low - self.config.price_increment:
            return
        self._observed_low_raids.add(source.label)
        scenario_id = f"{self.instrument_id}-{source.label.value}-LOW-DIAGNOSTIC-{source.day_bucket}"
        self._emit(
            scenario_id=scenario_id,
            event_type="SESSION_LOW_RAID_DIAGNOSTIC",
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="TERMINAL",
            reason_code="LOW_SIDE_ROUTE_NOT_EXECUTABLE_WITHOUT_SEPARATE_STRUCTURE_PROOF",
            reference_price=source.low,
            details={
                "source": source.label.value,
                "session_high": source.high,
                "session_low": source.low,
                "raid_low": bar.low,
            },
        )

    def _start_episode(self, source: SessionRange, bar: FiveBar, atr: float) -> HighRaidEpisode:
        self._scenario_counter += 1
        scenario_id = f"{self.instrument_id}-{source.label.value}-HIGH-{self._scenario_counter:06d}"
        episode = HighRaidEpisode(
            scenario_id=scenario_id,
            source=source,
            sweep_index=self._five_index,
            sweep_ts_ns=bar.ts_ns,
            extreme=bar.high,
        )
        self._episodes[source.label] = episode
        self.scenario_counts[f"{source.label.value}_HIGH_RAID"] += 1
        self._emit(
            scenario_id=scenario_id,
            event_type="SESSION_HIGH_RAID_DETECTED",
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="WAIT_RECLAIM",
            reason_code=f"PRICE_TRADED_ABOVE_COMPLETED_{source.label.value}_HIGH",
            reference_price=source.high,
            details={
                "source": source.label.value,
                "session_high": source.high,
                "session_low": source.low,
                "session_close_location": source.close_location,
                "raid_extreme": bar.high,
                "penetration_atr": (bar.high - source.high) / atr,
            },
        )
        return episode

    def _mark_reclaim(self, episode: HighRaidEpisode, bar: FiveBar, atr: float) -> None:
        episode.phase = "WAIT_CONFIRM"
        episode.reclaim_index = self._five_index
        episode.reclaim_ts_ns = bar.ts_ns
        episode.reclaim_bar = bar
        episode.atr_at_reclaim = atr
        episode.confirm_index = self._five_index + self.config.confirmation_bars
        self._emit(
            scenario_id=episode.scenario_id,
            event_type="SESSION_HIGH_RECLAIM_OBSERVED",
            event_time_ns=episode.sweep_ts_ns,
            observed_time_ns=bar.ts_ns,
            next_state="WAIT_CONFIRM",
            reason_code="COMPLETED_BAR_CLOSED_BACK_BELOW_SESSION_HIGH",
            reference_price=episode.source.high,
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

    def _terminate(self, label: SessionLabel, ts_ns: int, reason: str) -> None:
        episode = self._episodes.pop(label, None)
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
            details={
                "source": label.value,
                "phase": episode.phase,
                "raid_extreme": episode.extreme,
            },
        )
        self._done.add(label)

    def _round_price(self, value: float, rounding: str) -> float:
        increment = Decimal(str(self.config.price_increment))
        mode = ROUND_CEILING if rounding == "CEIL" else ROUND_DOWN
        units = (Decimal(str(value)) / increment).to_integral_value(rounding=mode)
        return float(units * increment)

    def _build_plan(
        self,
        *,
        episode: HighRaidEpisode,
        bar: FiveBar,
        atr: float,
        stop_raw: float,
    ) -> TradePlan | None:
        source = episode.source
        target_raw = source.high - self.config.rejection_target_fraction * source.width
        if bar.low <= target_raw:
            self.skips["STRUCTURAL_TARGET_REACHED_BEFORE_DECISION"] += 1
            return None
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
        scenario = (
            ScenarioKind.ASIA_HIGH_REJECTION
            if source.label is SessionLabel.ASIA
            else ScenarioKind.LONDON_HIGH_REJECTION
        )
        return TradePlan(
            scenario_id=episode.scenario_id,
            scenario=scenario,
            direction=Direction.SHORT,
            observed_ts_ns=bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            loss_per_unit=loss_per_unit,
            expected_profit_per_unit=expected_profit,
            net_r=net_r,
            details={
                "source": f"COMPLETED_{source.label.value}_RANGE",
                "source_label": source.label.value,
                "session_high": source.high,
                "session_low": source.low,
                "session_close": source.close,
                "session_close_location": source.close_location,
                "session_width": source.width,
                "raid_extreme": episode.extreme,
                "sweep_ts_ns": episode.sweep_ts_ns,
                "reclaim_ts_ns": episode.reclaim_ts_ns,
                "decision_ts_ns": bar.ts_ns,
                "decision_atr": atr,
                "decision_body_atr": bar.body / atr,
                "decision_close_location": bar.close_location,
                "decision_flow": bar.signed_flow,
                "target_semantics": "COMPLETED_RANGE_DISCOUNT_OBJECTIVE",
                "entry_semantics": "MARKET_AFTER_COMPLETED_CAUSAL_CONFIRMATION",
                "entry_cost_per_unit": entry_cost,
                "stop_cost_per_unit": stop_cost,
                "target_cost_per_unit": target_cost,
                "slippage_allowance_per_unit": slippage,
            },
        )

    def _emit_plan(
        self,
        episode: HighRaidEpisode,
        bar: FiveBar,
        plan: TradePlan,
        allow_entry: bool,
    ) -> TradePlan | None:
        label = episode.source.label
        self._episodes.pop(label, None)
        self._done.add(label)
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
            reason_code="COSTED_CAUSAL_SESSION_REJECTION_PLAN_VALID",
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

    def _advance_episode(
        self,
        label: SessionLabel,
        bar: FiveBar,
        atr: float,
        allow_entry: bool,
    ) -> TradePlan | None:
        episode = self._episodes.get(label)
        if episode is None:
            return None
        source = episode.source
        minute = self._minute_of_day(bar.ts_ns)
        if minute > source.trade_end_minute:
            self._terminate(label, bar.ts_ns, "SESSION_ACTIVITY_WINDOW_EXPIRED")
            return None

        if episode.phase == "WAIT_RECLAIM":
            episode.extreme = max(episode.extreme, bar.high)
            if bar.close < source.high:
                self._mark_reclaim(episode, bar, atr)
                return None
            if self._five_index - episode.sweep_index >= self.config.reclaim_max_bars:
                self._terminate(label, bar.ts_ns, "BOUNDARY_ACCEPTED_NOT_RECLAIMED_IN_TIME")
            return None

        assert episode.confirm_index is not None
        if self._five_index < episode.confirm_index:
            return None
        assert episode.reclaim_bar is not None and episode.atr_at_reclaim is not None
        reclaim = episode.reclaim_bar
        reclaim_atr = episode.atr_at_reclaim
        if reclaim.body / reclaim_atr < self.config.reclaim_body_atr:
            self._terminate(label, bar.ts_ns, "RECLAIM_LACKED_DISPLACEMENT")
            return None
        invalidation = episode.extreme + self.config.stop_buffer_atr * reclaim_atr
        if bar.high >= invalidation:
            self._terminate(label, bar.ts_ns, "RAID_EXTREME_NOT_DEFENDED")
            return None
        if source.label is SessionLabel.ASIA:
            confirmed = (
                bar.close < bar.open
                and bar.body / atr >= self.config.asia_confirmation_body_atr
                and bar.close_location <= self.config.asia_confirmation_max_close_location
            )
            if not confirmed:
                self._terminate(label, bar.ts_ns, "ASIA_REJECTION_LACKED_DOWNSIDE_CONFIRMATION")
                return None
        plan = self._build_plan(
            episode=episode,
            bar=bar,
            atr=reclaim_atr,
            stop_raw=invalidation,
        )
        if plan is None:
            self._terminate(label, bar.ts_ns, "COSTED_PLAN_REJECTED")
            return None
        return self._emit_plan(episode, bar, plan, allow_entry)

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

        if (
            atr is None
            or self._day_bucket is None
            or self._weekday(self._day_bucket) >= 5
        ):
            return None

        plan: TradePlan | None = None
        for label in (SessionLabel.ASIA, SessionLabel.LONDON):
            source = self._ranges.get(label)
            if source is None or not (
                source.trade_start_minute < minute <= source.trade_end_minute
            ):
                continue
            self._observe_low_raid(source, bar)
            if label in self._done:
                continue
            episode = self._episodes.get(label)
            if episode is None:
                if bar.high >= source.high + self.config.price_increment:
                    episode = self._start_episode(source, bar, atr)
                    if bar.close < source.high:
                        self._mark_reclaim(episode, bar, atr)
                continue
            candidate = self._advance_episode(label, bar, atr, allow_entry)
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
            reason_code="NAUTILUS_MARKET_BRACKET_SUBMITTED",
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
    "BarObs",
    "CausalLiquidityAuctionEngine",
    "Direction",
    "FiveBar",
    "LogicConfig",
    "RiskSizer",
    "ScenarioKind",
    "SessionLabel",
    "SizeDecision",
    "TradePlan",
]
