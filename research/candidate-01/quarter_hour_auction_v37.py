"""Quarter-hour algorithmic-auction continuation for candidate 01 v37.

Pattern detector
    At a standardized five-minute boundary, freeze the immediately preceding
    fifteen completed one-minute bars only when they form a two-sided dealing
    range. Observe the first ten seconds of the boundary minute. A pattern is
    emitted only when that window opens inside the frozen range, closes at
    least one all-in per-side cost beyond one boundary, and aggressive flow is
    aligned with the displacement. The completed boundary minute must then
    accept beyond the same cost-resolved boundary with aligned total flow.

Scenario router
    Quarter-hour boundaries (minute divisible by 15) form the primary. Other
    five-minute boundaries form the single clock-salience ablation. A trade is
    emitted only when a causally confirmed and still unswept five-minute swing
    liquidity level exists beyond the signal-minute extreme. Equilibrium
    reacceptance through the frozen dealing-range midpoint invalidates the
    continuation; the unswept swing is the destination.

No order, fill, fee, PnL, position or NAV logic lives in this module.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Iterable, Iterator, Sequence

from aggtrade_data import AggTrade, AggTradeDownload, iter_downloads
from core import Side
from impact_regime_probe import ScenarioPlan

SECOND_NS = 1_000_000_000
TEN_SECONDS_NS = 10 * SECOND_NS
MINUTE_NS = 60 * SECOND_NS
FIVE_MINUTES_NS = 5 * MINUTE_NS
BALANCE_MINUTES = 15
LIQUIDITY_LOOKBACK_MINUTES = 72 * 60
SWING_RADIUS = 2
COST_PER_SIDE = 0.0007
MIN_BALANCE_WIDTH_FRACTION = 4.0 * COST_PER_SIDE
PRIMARY_RULE = "quarter-hour-clock-primary"
CONTROL_RULE = "ordinary-five-minute-clock-control"
PRIMARY_SUFFIX = ":quarter-hour-clock-primary"
CONTROL_SUFFIX = ":ordinary-five-minute-clock-control"


@dataclass(frozen=True, slots=True)
class IntervalBar:
    start_time_ns: int
    end_time_ns: int
    open: float
    high: float
    low: float
    close: float
    quote_notional: float
    signed_aggressive_quote: float
    trade_count: int
    first_trade_time_ns: int
    last_trade_time_ns: int

    @property
    def imbalance(self) -> float:
        return (
            self.signed_aggressive_quote / self.quote_notional
            if self.quote_notional > 0.0
            else 0.0
        )

    @property
    def close_location(self) -> float:
        width = self.high - self.low
        return (self.close - self.low) / width if width > 0.0 else 0.5


@dataclass(frozen=True, slots=True)
class ClockMinute:
    start_time_ns: int
    end_time_ns: int
    minute: IntervalBar
    first_ten_seconds: IntervalBar | None


@dataclass(frozen=True, slots=True)
class DealingRange:
    start_time_ns: int
    end_time_ns: int
    high: float
    low: float
    midpoint: float
    midpoint_crosses: int
    width_fraction: float


@dataclass(frozen=True, slots=True)
class FiveMinuteBar:
    start_time_ns: int
    end_time_ns: int
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class LiquidityLevel:
    side: str
    price: float
    swing_time_ns: int
    confirmation_time_ns: int
    source_index: int


@dataclass(frozen=True, slots=True)
class ClockAuctionPattern:
    scenario_id: str
    signal_index: int
    clock_class: str
    boundary_minute_of_hour: int
    signal_time_ns: int
    side: Side
    dealing_range: DealingRange
    boundary_price: float
    impulse_open: float
    impulse_high: float
    impulse_low: float
    impulse_close: float
    impulse_imbalance: float
    impulse_quote_notional: float
    minute_high: float
    minute_low: float
    minute_close: float
    minute_imbalance: float
    displacement_fraction: float
    acceptance_fraction: float


@dataclass(frozen=True, slots=True)
class ClockAuctionDiagnostic:
    scenario_id: str
    clock_class: str
    boundary_minute_of_hour: int
    signal_time_ns: int
    side: str
    range_start_time_ns: int
    range_end_time_ns: int
    range_high: float
    range_low: float
    range_midpoint: float
    range_midpoint_crosses: int
    range_width_fraction: float
    impulse_open: float
    impulse_high: float
    impulse_low: float
    impulse_close: float
    impulse_imbalance: float
    impulse_quote_notional: float
    minute_high: float
    minute_low: float
    minute_close: float
    minute_imbalance: float
    displacement_fraction: float
    acceptance_fraction: float
    liquidity_side: str | None
    liquidity_price: float | None
    liquidity_swing_time_ns: int | None
    liquidity_confirmation_time_ns: int | None
    stop_price: float | None
    target_price: float | None
    signal_close_price_risk_fraction: float | None
    signal_close_net_reward_risk: float | None
    plan_id: str | None
    reason_code: str


@dataclass(slots=True)
class _Accumulator:
    start_time_ns: int
    end_time_ns: int
    open: float
    high: float
    low: float
    close: float
    quote_notional: float
    signed_aggressive_quote: float
    trade_count: int
    first_trade_time_ns: int
    last_trade_time_ns: int

    @classmethod
    def from_trade(
        cls,
        start_time_ns: int,
        end_time_ns: int,
        trade: AggTrade,
    ) -> "_Accumulator":
        price = float(trade.price)
        return cls(
            start_time_ns=start_time_ns,
            end_time_ns=end_time_ns,
            open=price,
            high=price,
            low=price,
            close=price,
            quote_notional=float(trade.quote_notional),
            signed_aggressive_quote=float(trade.signed_aggressive_quote),
            trade_count=1,
            first_trade_time_ns=int(trade.ts_event_ns),
            last_trade_time_ns=int(trade.ts_event_ns),
        )

    def update(self, trade: AggTrade) -> None:
        price = float(trade.price)
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.quote_notional += float(trade.quote_notional)
        self.signed_aggressive_quote += float(trade.signed_aggressive_quote)
        self.trade_count += 1
        self.last_trade_time_ns = int(trade.ts_event_ns)

    def freeze(self) -> IntervalBar:
        return IntervalBar(**asdict(self))


def iter_interval_bars(
    records: Iterable[AggTradeDownload],
    *,
    interval_ns: int,
    start_ns: int,
    end_ns: int,
) -> Iterator[IntervalBar]:
    current: _Accumulator | None = None
    for trade in iter_downloads(records):
        ts_ns = int(trade.ts_event_ns)
        if ts_ns < start_ns:
            continue
        if ts_ns >= end_ns:
            break
        interval_start = (ts_ns // interval_ns) * interval_ns
        if current is None:
            current = _Accumulator.from_trade(
                interval_start,
                interval_start + interval_ns,
                trade,
            )
            continue
        if interval_start < current.start_time_ns:
            raise ValueError("interval timestamp regression")
        if interval_start == current.start_time_ns:
            current.update(trade)
            continue
        yield current.freeze()
        current = _Accumulator.from_trade(
            interval_start,
            interval_start + interval_ns,
            trade,
        )
    if current is not None:
        yield current.freeze()


def build_clock_minutes(
    records: Iterable[AggTradeDownload],
    *,
    start_ns: int,
    end_ns: int,
) -> tuple[list[ClockMinute], dict[str, int]]:
    """Build exact UTC minutes and first-ten-second impulse bars in one pass."""

    rows: list[ClockMinute] = []
    minute_accumulator: _Accumulator | None = None
    impulse_accumulator: _Accumulator | None = None

    def freeze_current() -> None:
        nonlocal minute_accumulator, impulse_accumulator
        if minute_accumulator is None:
            return
        minute_bar = minute_accumulator.freeze()
        impulse_bar = (
            impulse_accumulator.freeze()
            if impulse_accumulator is not None
            else None
        )
        rows.append(
            ClockMinute(
                start_time_ns=minute_bar.start_time_ns,
                end_time_ns=minute_bar.end_time_ns,
                minute=minute_bar,
                first_ten_seconds=impulse_bar,
            ),
        )
        minute_accumulator = None
        impulse_accumulator = None

    for trade in iter_downloads(records):
        ts_ns = int(trade.ts_event_ns)
        if ts_ns < start_ns:
            continue
        if ts_ns >= end_ns:
            break
        minute_start = (ts_ns // MINUTE_NS) * MINUTE_NS
        if (
            minute_accumulator is not None
            and minute_start < minute_accumulator.start_time_ns
        ):
            raise ValueError("minute timestamp regression")
        if (
            minute_accumulator is None
            or minute_start != minute_accumulator.start_time_ns
        ):
            freeze_current()
            minute_accumulator = _Accumulator.from_trade(
                minute_start,
                minute_start + MINUTE_NS,
                trade,
            )
            if ts_ns < minute_start + TEN_SECONDS_NS:
                impulse_accumulator = _Accumulator.from_trade(
                    minute_start,
                    minute_start + TEN_SECONDS_NS,
                    trade,
                )
            continue
        minute_accumulator.update(trade)
        if ts_ns < minute_start + TEN_SECONDS_NS:
            if impulse_accumulator is None:
                impulse_accumulator = _Accumulator.from_trade(
                    minute_start,
                    minute_start + TEN_SECONDS_NS,
                    trade,
                )
            else:
                impulse_accumulator.update(trade)
    freeze_current()

    gaps = sum(
        right.start_time_ns - left.start_time_ns != MINUTE_NS
        for left, right in zip(rows, rows[1:])
    )
    return rows, {
        "minute_count": len(rows),
        "first_ten_second_bar_count": sum(
            row.first_ten_seconds is not None for row in rows
        ),
        "minutes_without_first_ten_second_trade": sum(
            row.first_ten_seconds is None for row in rows
        ),
        "minute_time_gaps": gaps,
    }


def _midpoint_crosses(closes: Sequence[float], midpoint: float) -> int:
    prior = 0
    crosses = 0
    for value in closes:
        side = 1 if value > midpoint else -1 if value < midpoint else 0
        if side == 0:
            continue
        if prior and side != prior:
            crosses += 1
        prior = side
    return crosses


def dealing_range(window: Sequence[ClockMinute]) -> DealingRange | None:
    if len(window) != BALANCE_MINUTES:
        return None
    if any(
        right.start_time_ns - left.start_time_ns != MINUTE_NS
        for left, right in zip(window, window[1:])
    ):
        return None
    high = max(row.minute.high for row in window)
    low = min(row.minute.low for row in window)
    if not all(isfinite(value) and value > 0.0 for value in (high, low)):
        return None
    width_fraction = high / low - 1.0
    if width_fraction < MIN_BALANCE_WIDTH_FRACTION:
        return None
    midpoint = 0.5 * (high + low)
    crosses = _midpoint_crosses(
        [row.minute.close for row in window],
        midpoint,
    )
    if crosses < 2:
        return None
    return DealingRange(
        start_time_ns=window[0].start_time_ns,
        end_time_ns=window[-1].end_time_ns,
        high=high,
        low=low,
        midpoint=midpoint,
        midpoint_crosses=crosses,
        width_fraction=width_fraction,
    )


def _minute_of_hour(ts_ns: int) -> int:
    return int((ts_ns // MINUTE_NS) % 60)


def _clock_class(minute_of_hour: int) -> str | None:
    if minute_of_hour % 15 == 0:
        return PRIMARY_RULE
    if minute_of_hour % 5 == 0:
        return CONTROL_RULE
    return None


def detect_clock_auction_pattern(
    minutes: Sequence[ClockMinute],
    index: int,
) -> tuple[ClockAuctionPattern | None, str | None]:
    if index < BALANCE_MINUTES:
        return None, None
    current = minutes[index]
    minute_of_hour = _minute_of_hour(current.start_time_ns)
    clock_class = _clock_class(minute_of_hour)
    if clock_class is None:
        return None, None
    impulse = current.first_ten_seconds
    if impulse is None:
        return None, "NO_TRADE_IN_FIRST_TEN_SECONDS"
    if impulse.start_time_ns != current.start_time_ns:
        raise ValueError("first ten-second bar must start at exact minute boundary")
    if impulse.end_time_ns != current.start_time_ns + TEN_SECONDS_NS:
        raise ValueError("first ten-second bar must end at boundary plus ten seconds")
    frozen = dealing_range(minutes[index - BALANCE_MINUTES : index])
    if frozen is None:
        return None, "NO_TWO_SIDED_PRIOR_DEALING_RANGE"
    if not frozen.low <= impulse.open <= frozen.high:
        return None, "BOUNDARY_IMPULSE_DID_NOT_OPEN_INSIDE_RANGE"

    upper = (
        impulse.close >= frozen.high * (1.0 + COST_PER_SIDE)
        and impulse.signed_aggressive_quote > 0.0
        and current.minute.close >= frozen.high * (1.0 + COST_PER_SIDE)
        and current.minute.signed_aggressive_quote > 0.0
    )
    lower = (
        impulse.close <= frozen.low * (1.0 - COST_PER_SIDE)
        and impulse.signed_aggressive_quote < 0.0
        and current.minute.close <= frozen.low * (1.0 - COST_PER_SIDE)
        and current.minute.signed_aggressive_quote < 0.0
    )
    if upper and lower:
        return None, "AMBIGUOUS_TWO_SIDED_CLOCK_DISPLACEMENT"
    if not upper and not lower:
        return None, None
    side = Side.LONG if upper else Side.SHORT
    boundary = frozen.high if upper else frozen.low
    displacement = (
        impulse.close / boundary - 1.0
        if upper
        else 1.0 - impulse.close / boundary
    )
    acceptance = (
        current.minute.close / boundary - 1.0
        if upper
        else 1.0 - current.minute.close / boundary
    )
    scenario_id = (
        f"quarter-hour-auction:{index}:{side.value.lower()}:"
        f"{current.end_time_ns}:{clock_class}"
    )
    return ClockAuctionPattern(
        scenario_id=scenario_id,
        signal_index=index,
        clock_class=clock_class,
        boundary_minute_of_hour=minute_of_hour,
        signal_time_ns=current.end_time_ns,
        side=side,
        dealing_range=frozen,
        boundary_price=boundary,
        impulse_open=impulse.open,
        impulse_high=impulse.high,
        impulse_low=impulse.low,
        impulse_close=impulse.close,
        impulse_imbalance=impulse.imbalance,
        impulse_quote_notional=impulse.quote_notional,
        minute_high=current.minute.high,
        minute_low=current.minute.low,
        minute_close=current.minute.close,
        minute_imbalance=current.minute.imbalance,
        displacement_fraction=displacement,
        acceptance_fraction=acceptance,
    ), None


def build_five_minute_bars(minutes: Sequence[ClockMinute]) -> list[FiveMinuteBar]:
    result: list[FiveMinuteBar] = []
    current_start: int | None = None
    bucket: list[ClockMinute] = []
    for row in minutes:
        bucket_start = (row.start_time_ns // FIVE_MINUTES_NS) * FIVE_MINUTES_NS
        if current_start is None:
            current_start = bucket_start
        if bucket_start != current_start:
            if (
                len(bucket) == 5
                and bucket[0].start_time_ns == current_start
                and bucket[-1].end_time_ns == current_start + FIVE_MINUTES_NS
            ):
                result.append(
                    FiveMinuteBar(
                        start_time_ns=current_start,
                        end_time_ns=current_start + FIVE_MINUTES_NS,
                        high=max(item.minute.high for item in bucket),
                        low=min(item.minute.low for item in bucket),
                        close=bucket[-1].minute.close,
                    ),
                )
            current_start = bucket_start
            bucket = []
        bucket.append(row)
    if (
        current_start is not None
        and len(bucket) == 5
        and bucket[0].start_time_ns == current_start
        and bucket[-1].end_time_ns == current_start + FIVE_MINUTES_NS
    ):
        result.append(
            FiveMinuteBar(
                start_time_ns=current_start,
                end_time_ns=current_start + FIVE_MINUTES_NS,
                high=max(item.minute.high for item in bucket),
                low=min(item.minute.low for item in bucket),
                close=bucket[-1].minute.close,
            ),
        )
    return result


def confirmed_unswept_liquidity(
    five_minute_bars: Sequence[FiveMinuteBar],
    *,
    signal_start_ns: int,
    signal_high: float,
    signal_low: float,
    side: Side,
) -> LiquidityLevel | None:
    eligible = [row for row in five_minute_bars if row.end_time_ns <= signal_start_ns]
    minimum_start = signal_start_ns - LIQUIDITY_LOOKBACK_MINUTES * MINUTE_NS
    eligible = [row for row in eligible if row.start_time_ns >= minimum_start]
    candidates: list[LiquidityLevel] = []
    for index in range(SWING_RADIUS, len(eligible) - SWING_RADIUS):
        row = eligible[index]
        left = eligible[index - SWING_RADIUS : index]
        right = eligible[index + 1 : index + 1 + SWING_RADIUS]
        confirmation = eligible[index + SWING_RADIUS]
        if side is Side.LONG:
            is_swing = all(row.high > item.high for item in [*left, *right])
            if not is_swing or row.high <= signal_high:
                continue
            later = eligible[index + SWING_RADIUS + 1 :]
            if any(item.high >= row.high for item in later):
                continue
            candidates.append(
                LiquidityLevel(
                    side="BUY_SIDE",
                    price=row.high,
                    swing_time_ns=row.end_time_ns,
                    confirmation_time_ns=confirmation.end_time_ns,
                    source_index=index,
                ),
            )
        else:
            is_swing = all(row.low < item.low for item in [*left, *right])
            if not is_swing or row.low >= signal_low:
                continue
            later = eligible[index + SWING_RADIUS + 1 :]
            if any(item.low <= row.low for item in later):
                continue
            candidates.append(
                LiquidityLevel(
                    side="SELL_SIDE",
                    price=row.low,
                    swing_time_ns=row.end_time_ns,
                    confirmation_time_ns=confirmation.end_time_ns,
                    source_index=index,
                ),
            )
    if not candidates:
        return None
    return (
        min(candidates, key=lambda item: item.price)
        if side is Side.LONG
        else max(candidates, key=lambda item: item.price)
    )


def _signal_close_geometry(
    pattern: ClockAuctionPattern,
    *,
    stop: float,
    target: float,
) -> tuple[float, float]:
    entry = pattern.minute_close
    price_risk = abs(entry - stop)
    planned_loss = price_risk + entry * COST_PER_SIDE + stop * COST_PER_SIDE
    planned_gain = abs(target - entry) - entry * COST_PER_SIDE - target * COST_PER_SIDE
    price_fraction = price_risk / planned_loss if planned_loss > 0.0 else 0.0
    net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
    return price_fraction, net_rr


def route_pattern(
    pattern: ClockAuctionPattern,
    *,
    five_minute_bars: Sequence[FiveMinuteBar],
) -> tuple[ScenarioPlan | None, ClockAuctionDiagnostic]:
    level = confirmed_unswept_liquidity(
        five_minute_bars,
        signal_start_ns=pattern.signal_time_ns - MINUTE_NS,
        signal_high=pattern.minute_high,
        signal_low=pattern.minute_low,
        side=pattern.side,
    )
    common = dict(
        scenario_id=pattern.scenario_id,
        clock_class=pattern.clock_class,
        boundary_minute_of_hour=pattern.boundary_minute_of_hour,
        signal_time_ns=pattern.signal_time_ns,
        side=pattern.side.value,
        range_start_time_ns=pattern.dealing_range.start_time_ns,
        range_end_time_ns=pattern.dealing_range.end_time_ns,
        range_high=pattern.dealing_range.high,
        range_low=pattern.dealing_range.low,
        range_midpoint=pattern.dealing_range.midpoint,
        range_midpoint_crosses=pattern.dealing_range.midpoint_crosses,
        range_width_fraction=pattern.dealing_range.width_fraction,
        impulse_open=pattern.impulse_open,
        impulse_high=pattern.impulse_high,
        impulse_low=pattern.impulse_low,
        impulse_close=pattern.impulse_close,
        impulse_imbalance=pattern.impulse_imbalance,
        impulse_quote_notional=pattern.impulse_quote_notional,
        minute_high=pattern.minute_high,
        minute_low=pattern.minute_low,
        minute_close=pattern.minute_close,
        minute_imbalance=pattern.minute_imbalance,
        displacement_fraction=pattern.displacement_fraction,
        acceptance_fraction=pattern.acceptance_fraction,
    )
    if level is None:
        return None, ClockAuctionDiagnostic(
            **common,
            liquidity_side=None,
            liquidity_price=None,
            liquidity_swing_time_ns=None,
            liquidity_confirmation_time_ns=None,
            stop_price=None,
            target_price=None,
            signal_close_price_risk_fraction=None,
            signal_close_net_reward_risk=None,
            plan_id=None,
            reason_code="NO_CAUSALLY_CONFIRMED_UNSWEPT_EXTERNAL_LIQUIDITY",
        )
    stop = (
        pattern.dealing_range.midpoint * (1.0 - COST_PER_SIDE)
        if pattern.side is Side.LONG
        else pattern.dealing_range.midpoint * (1.0 + COST_PER_SIDE)
    )
    target = level.price
    price_fraction, net_rr = _signal_close_geometry(
        pattern,
        stop=stop,
        target=target,
    )
    suffix = PRIMARY_SUFFIX if pattern.clock_class == PRIMARY_RULE else CONTROL_SUFFIX
    plan_id = pattern.scenario_id + suffix
    plan = ScenarioPlan(
        scenario_id=plan_id,
        response="ACCEPTED_AUCTION_CONTINUATION",
        side=pattern.side,
        signal_bar_index=pattern.signal_index,
        signal_time_ns=pattern.signal_time_ns,
        stop_price=stop,
        target_price=target,
        confirmation_hold_price=pattern.boundary_price,
        structure_high=pattern.dealing_range.high,
        structure_low=pattern.dealing_range.low,
        structure_midpoint=pattern.dealing_range.midpoint,
        pulse_high=pattern.impulse_high,
        pulse_low=pattern.impulse_low,
        pulse_flow_score=pattern.impulse_imbalance,
        pulse_move_atr=(
            abs(pattern.impulse_close - pattern.boundary_price)
            / (pattern.dealing_range.high - pattern.dealing_range.low)
        ),
        pulse_path_efficiency=min(
            abs(pattern.impulse_close - pattern.impulse_open)
            / max(pattern.impulse_high - pattern.impulse_low, 1e-12),
            1.0,
        ),
        pulse_close_location=(
            pattern.impulse_close - pattern.impulse_low
        ) / max(pattern.impulse_high - pattern.impulse_low, 1e-12),
        reason_code=(
            "QUARTER_HOUR_FIRST_TEN_SECOND_DISPLACEMENT_ACCEPTED_TO_UNSWEPT_LIQUIDITY"
            if pattern.clock_class == PRIMARY_RULE
            else "ORDINARY_FIVE_MINUTE_FIRST_TEN_SECOND_DISPLACEMENT_CONTROL"
        ),
    )
    return plan, ClockAuctionDiagnostic(
        **common,
        liquidity_side=level.side,
        liquidity_price=level.price,
        liquidity_swing_time_ns=level.swing_time_ns,
        liquidity_confirmation_time_ns=level.confirmation_time_ns,
        stop_price=stop,
        target_price=target,
        signal_close_price_risk_fraction=price_fraction,
        signal_close_net_reward_risk=net_rr,
        plan_id=plan_id,
        reason_code="PLAN_ROUTED_TO_CAUSAL_UNSWEPT_EXTERNAL_LIQUIDITY",
    )


@dataclass(slots=True)
class QuarterHourAuctionResult:
    patterns: list[ClockAuctionPattern]
    diagnostics: list[ClockAuctionDiagnostic]
    primary_plans: list[ScenarioPlan]
    control_plans: list[ScenarioPlan]
    five_minute_bars: list[FiveMinuteBar]
    counts: Counter[str]


def build_quarter_hour_auction_plans(
    minutes: Sequence[ClockMinute],
) -> QuarterHourAuctionResult:
    five_minute_bars = build_five_minute_bars(minutes)
    patterns: list[ClockAuctionPattern] = []
    diagnostics: list[ClockAuctionDiagnostic] = []
    primary: list[ScenarioPlan] = []
    control: list[ScenarioPlan] = []
    counts: Counter[str] = Counter()
    for index in range(len(minutes)):
        pattern, reason = detect_clock_auction_pattern(minutes, index)
        if reason is not None:
            counts[reason] += 1
        if pattern is None:
            continue
        patterns.append(pattern)
        counts[f"pattern:{pattern.clock_class}"] += 1
        plan, diagnostic = route_pattern(
            pattern,
            five_minute_bars=five_minute_bars,
        )
        diagnostics.append(diagnostic)
        counts[diagnostic.reason_code] += 1
        if plan is None:
            continue
        if pattern.clock_class == PRIMARY_RULE:
            primary.append(plan)
        else:
            control.append(plan)
    return QuarterHourAuctionResult(
        patterns=patterns,
        diagnostics=diagnostics,
        primary_plans=primary,
        control_plans=control,
        five_minute_bars=five_minute_bars,
        counts=counts,
    )


__all__ = [
    "BALANCE_MINUTES",
    "CONTROL_RULE",
    "CONTROL_SUFFIX",
    "COST_PER_SIDE",
    "ClockAuctionDiagnostic",
    "ClockAuctionPattern",
    "ClockMinute",
    "DealingRange",
    "FIVE_MINUTES_NS",
    "FiveMinuteBar",
    "IntervalBar",
    "LIQUIDITY_LOOKBACK_MINUTES",
    "MINUTE_NS",
    "MIN_BALANCE_WIDTH_FRACTION",
    "PRIMARY_RULE",
    "PRIMARY_SUFFIX",
    "QuarterHourAuctionResult",
    "SECOND_NS",
    "SWING_RADIUS",
    "TEN_SECONDS_NS",
    "build_clock_minutes",
    "build_five_minute_bars",
    "build_quarter_hour_auction_plans",
    "confirmed_unswept_liquidity",
    "dealing_range",
    "detect_clock_auction_pattern",
    "iter_interval_bars",
    "route_pattern",
]
