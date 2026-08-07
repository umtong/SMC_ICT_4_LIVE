"""Cross-market failed-auction state machine for candidate 01 v36.

Pattern detector
    Freeze the immediately preceding 30 synchronized UTC minutes only when both
    BTCUSDT spot and USD-M futures behaved as two-sided balances. Detect a
    cost-resolved futures excursion beyond one frozen balance boundary with
    aggressive futures flow aligned to that excursion.

Trading scenario
    Keep the detected sweep active for the next three *completed* synchronized
    minutes. A failed auction is confirmed only when futures closes back
    inside its frozen boundary with opposite aggressive flow. The primary also
    requires that spot never completed its own cost-resolved excursion through
    the corresponding frozen spot boundary before that futures failure. The
    control removes only this spot non-confirmation variable.

Every one-minute observation becomes available at the exact UTC minute end.
This module contains no order, fill, PnL, portfolio or NAV logic.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Iterable, Iterator, Sequence

from aggtrade_data import AggTrade, AggTradeDownload, iter_downloads
from core import Side
from impact_regime_probe import ScenarioPlan

MINUTE_NS = 60_000_000_000
BALANCE_MINUTES = 30
CONFIRMATION_MINUTES = 3
COST_PER_SIDE = 0.0007
MIN_STRUCTURE_WIDTH_FRACTION = 4.0 * COST_PER_SIDE
MIN_SWEEP_FRACTION = COST_PER_SIDE
PRIMARY_SUFFIX = ":spot-unconfirmed-primary"
CONTROL_SUFFIX = ":futures-failure-control"


@dataclass(frozen=True, slots=True)
class MinuteBar:
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
        if self.quote_notional <= 0.0:
            return 0.0
        return self.signed_aggressive_quote / self.quote_notional

    @property
    def close_location(self) -> float:
        width = self.high - self.low
        return (self.close - self.low) / width if width > 0.0 else 0.5

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class JointMinute:
    start_time_ns: int
    end_time_ns: int
    futures: MinuteBar
    spot: MinuteBar


@dataclass(frozen=True, slots=True)
class BalanceState:
    start_time_ns: int
    end_time_ns: int
    futures_high: float
    futures_low: float
    futures_midpoint: float
    spot_high: float
    spot_low: float
    spot_midpoint: float
    futures_midpoint_crosses: int
    spot_midpoint_crosses: int
    futures_width_fraction: float
    spot_width_fraction: float


@dataclass(frozen=True, slots=True)
class SweepPattern:
    scenario_id: str
    created_index: int
    event_time_ns: int
    outward_side: Side
    reversal_side: Side
    balance: BalanceState
    futures_boundary: float
    futures_opposite_boundary: float
    spot_boundary: float
    spot_opposite_boundary: float
    initial_futures_high: float
    initial_futures_low: float
    initial_spot_high: float
    initial_spot_low: float
    initial_futures_imbalance: float
    initial_spot_imbalance: float
    futures_excursion_fraction: float
    spot_excursion_fraction: float
    spot_confirmed_at_sweep: bool


@dataclass(slots=True)
class ActiveSweep:
    pattern: SweepPattern
    expiry_index: int
    futures_sweep_high: float
    futures_sweep_low: float
    maximum_spot_excursion_fraction: float
    spot_confirmed: bool
    spot_confirmation_time_ns: int | None


@dataclass(frozen=True, slots=True)
class SweepEvent:
    scenario_id: str
    created_index: int
    event_time_ns: int
    outward_side: str
    reversal_side: str
    balance_start_time_ns: int
    balance_end_time_ns: int
    futures_boundary: float
    futures_opposite_boundary: float
    futures_midpoint: float
    spot_boundary: float
    spot_opposite_boundary: float
    spot_midpoint: float
    futures_midpoint_crosses: int
    spot_midpoint_crosses: int
    futures_balance_width_fraction: float
    spot_balance_width_fraction: float
    initial_futures_high: float
    initial_futures_low: float
    initial_spot_high: float
    initial_spot_low: float
    futures_imbalance: float
    spot_imbalance: float
    futures_excursion_fraction: float
    spot_excursion_fraction: float
    spot_confirmed_at_sweep: bool


@dataclass(frozen=True, slots=True)
class CrossMarketDiagnostic:
    scenario_id: str
    outward_side: str
    reversal_side: str
    balance_start_time_ns: int
    balance_end_time_ns: int
    sweep_time_ns: int
    resolution_time_ns: int | None
    expiry_time_ns: int
    minutes_to_resolution: int | None
    futures_boundary: float
    futures_opposite_boundary: float
    futures_midpoint: float
    spot_boundary: float
    spot_opposite_boundary: float
    spot_midpoint: float
    futures_midpoint_crosses: int
    spot_midpoint_crosses: int
    futures_balance_width_fraction: float
    spot_balance_width_fraction: float
    futures_sweep_high: float
    futures_sweep_low: float
    futures_sweep_excursion_fraction: float
    maximum_spot_excursion_fraction: float
    spot_confirmed_before_resolution: bool
    spot_confirmation_time_ns: int | None
    failure_close: float | None
    failure_imbalance: float | None
    full_excursion_rejection_ratio: float | None
    futures_failed_auction_confirmed: bool
    primary_plan_id: str | None
    control_plan_id: str | None
    reason_code: str


@dataclass(slots=True)
class _MinuteAccumulator:
    start_time_ns: int
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
    def from_trade(cls, minute_start: int, trade: AggTrade) -> "_MinuteAccumulator":
        return cls(
            start_time_ns=minute_start,
            open=float(trade.price),
            high=float(trade.price),
            low=float(trade.price),
            close=float(trade.price),
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

    def freeze(self) -> MinuteBar:
        return MinuteBar(
            start_time_ns=self.start_time_ns,
            end_time_ns=self.start_time_ns + MINUTE_NS,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            quote_notional=self.quote_notional,
            signed_aggressive_quote=self.signed_aggressive_quote,
            trade_count=self.trade_count,
            first_trade_time_ns=self.first_trade_time_ns,
            last_trade_time_ns=self.last_trade_time_ns,
        )


def iter_minute_bars(
    records: Iterable[AggTradeDownload],
    *,
    start_ns: int,
    end_ns: int,
) -> Iterator[MinuteBar]:
    """Aggregate official trades into causal UTC minutes in ``[start, end)``."""

    current: _MinuteAccumulator | None = None
    for trade in iter_downloads(records):
        ts = int(trade.ts_event_ns)
        if ts < start_ns:
            continue
        if ts >= end_ns:
            break
        minute_start = (ts // MINUTE_NS) * MINUTE_NS
        if current is None:
            current = _MinuteAccumulator.from_trade(minute_start, trade)
            continue
        if minute_start < current.start_time_ns:
            raise ValueError("minute timestamp regression")
        if minute_start == current.start_time_ns:
            current.update(trade)
            continue
        yield current.freeze()
        current = _MinuteAccumulator.from_trade(minute_start, trade)
    if current is not None:
        yield current.freeze()


def build_joint_minutes(
    *,
    futures_records: Iterable[AggTradeDownload],
    spot_records: Iterable[AggTradeDownload],
    start_ns: int,
    end_ns: int,
) -> tuple[list[JointMinute], dict[str, int]]:
    futures = {
        row.start_time_ns: row
        for row in iter_minute_bars(
            futures_records,
            start_ns=start_ns,
            end_ns=end_ns,
        )
    }
    spot = {
        row.start_time_ns: row
        for row in iter_minute_bars(
            spot_records,
            start_ns=start_ns,
            end_ns=end_ns,
        )
    }
    common = sorted(set(futures).intersection(spot))
    joint = [
        JointMinute(
            start_time_ns=value,
            end_time_ns=value + MINUTE_NS,
            futures=futures[value],
            spot=spot[value],
        )
        for value in common
    ]
    gaps = sum(
        1
        for left, right in zip(common, common[1:])
        if right - left != MINUTE_NS
    )
    counts = {
        "futures_minute_count": len(futures),
        "spot_minute_count": len(spot),
        "joint_minute_count": len(joint),
        "futures_only_minutes": len(set(futures).difference(spot)),
        "spot_only_minutes": len(set(spot).difference(futures)),
        "joint_time_gaps": gaps,
    }
    return joint, counts


def _midpoint_crosses(closes: Sequence[float], midpoint: float) -> int:
    prior = 0
    crosses = 0
    for value in closes:
        side = 1 if value > midpoint else -1 if value < midpoint else 0
        if side == 0:
            continue
        if prior != 0 and side != prior:
            crosses += 1
        prior = side
    return crosses


def _balance(window: Sequence[JointMinute]) -> BalanceState | None:
    if len(window) != BALANCE_MINUTES:
        return None
    if any(
        right.start_time_ns - left.start_time_ns != MINUTE_NS
        for left, right in zip(window, window[1:])
    ):
        return None
    futures_high = max(row.futures.high for row in window)
    futures_low = min(row.futures.low for row in window)
    spot_high = max(row.spot.high for row in window)
    spot_low = min(row.spot.low for row in window)
    if not all(
        isfinite(value) and value > 0.0
        for value in (futures_high, futures_low, spot_high, spot_low)
    ):
        return None
    futures_width_fraction = (futures_high - futures_low) / futures_low
    spot_width_fraction = (spot_high - spot_low) / spot_low
    if futures_width_fraction < MIN_STRUCTURE_WIDTH_FRACTION:
        return None
    if spot_width_fraction < MIN_STRUCTURE_WIDTH_FRACTION:
        return None
    futures_mid = 0.5 * (futures_high + futures_low)
    spot_mid = 0.5 * (spot_high + spot_low)
    futures_crosses = _midpoint_crosses(
        [row.futures.close for row in window],
        futures_mid,
    )
    spot_crosses = _midpoint_crosses(
        [row.spot.close for row in window],
        spot_mid,
    )
    if futures_crosses < 2 or spot_crosses < 2:
        return None
    return BalanceState(
        start_time_ns=window[0].start_time_ns,
        end_time_ns=window[-1].end_time_ns,
        futures_high=futures_high,
        futures_low=futures_low,
        futures_midpoint=futures_mid,
        spot_high=spot_high,
        spot_low=spot_low,
        spot_midpoint=spot_mid,
        futures_midpoint_crosses=futures_crosses,
        spot_midpoint_crosses=spot_crosses,
        futures_width_fraction=futures_width_fraction,
        spot_width_fraction=spot_width_fraction,
    )


def _excursion_fraction(bar: MinuteBar, boundary: float, side: Side) -> float:
    if side is Side.LONG:
        return max(bar.high / boundary - 1.0, 0.0)
    return max(1.0 - bar.low / boundary, 0.0)


def detect_sweep_pattern(
    minutes: Sequence[JointMinute],
    index: int,
) -> tuple[SweepPattern | None, str | None]:
    """Pure pattern detector; it creates no trade plan or active scenario."""

    if index < BALANCE_MINUTES:
        return None, None
    minute = minutes[index]
    balance = _balance(minutes[index - BALANCE_MINUTES : index])
    if balance is None:
        return None, "NO_JOINT_TWO_SIDED_BALANCE"
    upper = (
        minute.futures.high
        >= balance.futures_high * (1.0 + MIN_SWEEP_FRACTION)
        and minute.futures.signed_aggressive_quote > 0.0
    )
    lower = (
        minute.futures.low
        <= balance.futures_low * (1.0 - MIN_SWEEP_FRACTION)
        and minute.futures.signed_aggressive_quote < 0.0
    )
    if upper and lower:
        return None, "AMBIGUOUS_TWO_SIDED_FUTURES_SWEEP"
    if not upper and not lower:
        return None, None

    outward = Side.LONG if upper else Side.SHORT
    reversal = Side.SHORT if upper else Side.LONG
    futures_boundary = balance.futures_high if upper else balance.futures_low
    futures_opposite = balance.futures_low if upper else balance.futures_high
    spot_boundary = balance.spot_high if upper else balance.spot_low
    spot_opposite = balance.spot_low if upper else balance.spot_high
    futures_excursion = _excursion_fraction(
        minute.futures,
        futures_boundary,
        outward,
    )
    spot_excursion = _excursion_fraction(
        minute.spot,
        spot_boundary,
        outward,
    )
    scenario_id = (
        f"cross-market-failed-auction:{index}:"
        f"{outward.value.lower()}:{minute.end_time_ns}"
    )
    return (
        SweepPattern(
            scenario_id=scenario_id,
            created_index=index,
            event_time_ns=minute.end_time_ns,
            outward_side=outward,
            reversal_side=reversal,
            balance=balance,
            futures_boundary=futures_boundary,
            futures_opposite_boundary=futures_opposite,
            spot_boundary=spot_boundary,
            spot_opposite_boundary=spot_opposite,
            initial_futures_high=minute.futures.high,
            initial_futures_low=minute.futures.low,
            initial_spot_high=minute.spot.high,
            initial_spot_low=minute.spot.low,
            initial_futures_imbalance=minute.futures.imbalance,
            initial_spot_imbalance=minute.spot.imbalance,
            futures_excursion_fraction=futures_excursion,
            spot_excursion_fraction=spot_excursion,
            spot_confirmed_at_sweep=(
                spot_excursion >= MIN_SWEEP_FRACTION
            ),
        ),
        None,
    )


def _event(pattern: SweepPattern) -> SweepEvent:
    balance = pattern.balance
    return SweepEvent(
        scenario_id=pattern.scenario_id,
        created_index=pattern.created_index,
        event_time_ns=pattern.event_time_ns,
        outward_side=pattern.outward_side.value,
        reversal_side=pattern.reversal_side.value,
        balance_start_time_ns=balance.start_time_ns,
        balance_end_time_ns=balance.end_time_ns,
        futures_boundary=pattern.futures_boundary,
        futures_opposite_boundary=pattern.futures_opposite_boundary,
        futures_midpoint=balance.futures_midpoint,
        spot_boundary=pattern.spot_boundary,
        spot_opposite_boundary=pattern.spot_opposite_boundary,
        spot_midpoint=balance.spot_midpoint,
        futures_midpoint_crosses=balance.futures_midpoint_crosses,
        spot_midpoint_crosses=balance.spot_midpoint_crosses,
        futures_balance_width_fraction=balance.futures_width_fraction,
        spot_balance_width_fraction=balance.spot_width_fraction,
        initial_futures_high=pattern.initial_futures_high,
        initial_futures_low=pattern.initial_futures_low,
        initial_spot_high=pattern.initial_spot_high,
        initial_spot_low=pattern.initial_spot_low,
        futures_imbalance=pattern.initial_futures_imbalance,
        spot_imbalance=pattern.initial_spot_imbalance,
        futures_excursion_fraction=pattern.futures_excursion_fraction,
        spot_excursion_fraction=pattern.spot_excursion_fraction,
        spot_confirmed_at_sweep=pattern.spot_confirmed_at_sweep,
    )


def _futures_excursion_fraction(setup: ActiveSweep) -> float:
    pattern = setup.pattern
    if pattern.outward_side is Side.LONG:
        return max(setup.futures_sweep_high / pattern.futures_boundary - 1.0, 0.0)
    return max(1.0 - setup.futures_sweep_low / pattern.futures_boundary, 0.0)


def _full_rejection_ratio(setup: ActiveSweep, close: float) -> float:
    pattern = setup.pattern
    if pattern.outward_side is Side.LONG:
        excursion = setup.futures_sweep_high - pattern.futures_boundary
        return (
            (setup.futures_sweep_high - close) / excursion
            if excursion > 0.0
            else 0.0
        )
    excursion = pattern.futures_boundary - setup.futures_sweep_low
    return (
        (close - setup.futures_sweep_low) / excursion
        if excursion > 0.0
        else 0.0
    )


def _plan_from_setup(
    setup: ActiveSweep,
    *,
    index: int,
    minute: JointMinute,
    primary: bool,
) -> ScenarioPlan:
    pattern = setup.pattern
    if pattern.reversal_side is Side.SHORT:
        stop = setup.futures_sweep_high * (1.0 + COST_PER_SIDE)
        target = pattern.futures_opposite_boundary
    else:
        stop = setup.futures_sweep_low * (1.0 - COST_PER_SIDE)
        target = pattern.futures_opposite_boundary
    width = abs(pattern.futures_boundary - pattern.futures_opposite_boundary)
    excursion = (
        setup.futures_sweep_high - pattern.futures_boundary
        if pattern.outward_side is Side.LONG
        else pattern.futures_boundary - setup.futures_sweep_low
    )
    rejection_ratio = _full_rejection_ratio(setup, minute.futures.close)
    return ScenarioPlan(
        scenario_id=(
            pattern.scenario_id + (PRIMARY_SUFFIX if primary else CONTROL_SUFFIX)
        ),
        response="EXHAUSTION_REVERSAL",
        side=pattern.reversal_side,
        signal_bar_index=index,
        signal_time_ns=minute.end_time_ns,
        stop_price=stop,
        target_price=target,
        confirmation_hold_price=pattern.futures_boundary,
        structure_high=max(
            pattern.futures_boundary,
            pattern.futures_opposite_boundary,
        ),
        structure_low=min(
            pattern.futures_boundary,
            pattern.futures_opposite_boundary,
        ),
        structure_midpoint=pattern.balance.futures_midpoint,
        pulse_high=setup.futures_sweep_high,
        pulse_low=setup.futures_sweep_low,
        pulse_flow_score=pattern.initial_futures_imbalance,
        pulse_move_atr=excursion / width if width > 0.0 else 0.0,
        pulse_path_efficiency=min(max(rejection_ratio, 0.0), 1.0),
        pulse_close_location=minute.futures.close_location,
        reason_code=(
            "FUTURES_EXTERNAL_SWEEP_SPOT_UNCONFIRMED_COMPLETED_REENTRY"
            if primary
            else "FUTURES_EXTERNAL_SWEEP_COMPLETED_REENTRY_CONTROL"
        ),
    )


class CrossMarketFailedAuctionStateMachine:
    def __init__(self) -> None:
        self.minutes: list[JointMinute] = []
        self.active: ActiveSweep | None = None
        self.sweep_events: list[SweepEvent] = []
        self.diagnostics: list[CrossMarketDiagnostic] = []
        self.primary_plans: list[ScenarioPlan] = []
        self.control_plans: list[ScenarioPlan] = []
        self.counts: Counter[str] = Counter()

    @staticmethod
    def _scheduled_expiry_time(setup: ActiveSweep) -> int:
        return setup.pattern.event_time_ns + CONFIRMATION_MINUTES * MINUTE_NS

    def _diagnostic(
        self,
        setup: ActiveSweep,
        *,
        resolution_time_ns: int | None,
        failure_close: float | None,
        failure_imbalance: float | None,
        failed: bool,
        primary_plan_id: str | None,
        control_plan_id: str | None,
        reason_code: str,
    ) -> CrossMarketDiagnostic:
        pattern = setup.pattern
        resolution_minutes = (
            int((resolution_time_ns - pattern.event_time_ns) // MINUTE_NS)
            if resolution_time_ns is not None
            else None
        )
        return CrossMarketDiagnostic(
            scenario_id=pattern.scenario_id,
            outward_side=pattern.outward_side.value,
            reversal_side=pattern.reversal_side.value,
            balance_start_time_ns=pattern.balance.start_time_ns,
            balance_end_time_ns=pattern.balance.end_time_ns,
            sweep_time_ns=pattern.event_time_ns,
            resolution_time_ns=resolution_time_ns,
            expiry_time_ns=self._scheduled_expiry_time(setup),
            minutes_to_resolution=resolution_minutes,
            futures_boundary=pattern.futures_boundary,
            futures_opposite_boundary=pattern.futures_opposite_boundary,
            futures_midpoint=pattern.balance.futures_midpoint,
            spot_boundary=pattern.spot_boundary,
            spot_opposite_boundary=pattern.spot_opposite_boundary,
            spot_midpoint=pattern.balance.spot_midpoint,
            futures_midpoint_crosses=pattern.balance.futures_midpoint_crosses,
            spot_midpoint_crosses=pattern.balance.spot_midpoint_crosses,
            futures_balance_width_fraction=pattern.balance.futures_width_fraction,
            spot_balance_width_fraction=pattern.balance.spot_width_fraction,
            futures_sweep_high=setup.futures_sweep_high,
            futures_sweep_low=setup.futures_sweep_low,
            futures_sweep_excursion_fraction=_futures_excursion_fraction(setup),
            maximum_spot_excursion_fraction=(
                setup.maximum_spot_excursion_fraction
            ),
            spot_confirmed_before_resolution=setup.spot_confirmed,
            spot_confirmation_time_ns=setup.spot_confirmation_time_ns,
            failure_close=failure_close,
            failure_imbalance=failure_imbalance,
            full_excursion_rejection_ratio=(
                _full_rejection_ratio(setup, failure_close)
                if failure_close is not None
                else None
            ),
            futures_failed_auction_confirmed=failed,
            primary_plan_id=primary_plan_id,
            control_plan_id=control_plan_id,
            reason_code=reason_code,
        )

    def _process_active(self, index: int, minute: JointMinute) -> bool:
        setup = self.active
        if setup is None:
            return False
        pattern = setup.pattern
        if index <= pattern.created_index:
            return False
        if index > setup.expiry_index:
            self.diagnostics.append(
                self._diagnostic(
                    setup,
                    resolution_time_ns=None,
                    failure_close=None,
                    failure_imbalance=None,
                    failed=False,
                    primary_plan_id=None,
                    control_plan_id=None,
                    reason_code="FUTURES_SWEEP_RESPONSE_WINDOW_EXPIRED",
                ),
            )
            self.counts["response_window_expired"] += 1
            self.active = None
            return False

        setup.futures_sweep_high = max(
            setup.futures_sweep_high,
            minute.futures.high,
        )
        setup.futures_sweep_low = min(
            setup.futures_sweep_low,
            minute.futures.low,
        )
        spot_excursion = _excursion_fraction(
            minute.spot,
            pattern.spot_boundary,
            pattern.outward_side,
        )
        setup.maximum_spot_excursion_fraction = max(
            setup.maximum_spot_excursion_fraction,
            spot_excursion,
        )
        if not setup.spot_confirmed and spot_excursion >= MIN_SWEEP_FRACTION:
            setup.spot_confirmed = True
            setup.spot_confirmation_time_ns = minute.end_time_ns

        if pattern.outward_side is Side.LONG:
            failed = (
                minute.futures.close < pattern.futures_boundary
                and minute.futures.signed_aggressive_quote < 0.0
            )
        else:
            failed = (
                minute.futures.close > pattern.futures_boundary
                and minute.futures.signed_aggressive_quote > 0.0
            )
        if not failed:
            return False

        control = _plan_from_setup(
            setup,
            index=index,
            minute=minute,
            primary=False,
        )
        self.control_plans.append(control)
        primary: ScenarioPlan | None = None
        if not setup.spot_confirmed:
            primary = _plan_from_setup(
                setup,
                index=index,
                minute=minute,
                primary=True,
            )
            self.primary_plans.append(primary)
            self.counts["spot_unconfirmed_failures"] += 1
        else:
            self.counts["spot_confirmed_control_only_failures"] += 1
        self.diagnostics.append(
            self._diagnostic(
                setup,
                resolution_time_ns=minute.end_time_ns,
                failure_close=minute.futures.close,
                failure_imbalance=minute.futures.imbalance,
                failed=True,
                primary_plan_id=(
                    primary.scenario_id if primary is not None else None
                ),
                control_plan_id=control.scenario_id,
                reason_code=(
                    "SPOT_UNCONFIRMED_FUTURES_FAILED_AUCTION"
                    if primary is not None
                    else "SPOT_CONFIRMED_FUTURES_FAILED_AUCTION_CONTROL_ONLY"
                ),
            ),
        )
        self.counts["futures_failed_auction_confirmed"] += 1
        self.active = None
        return True

    def _arm(self, index: int) -> None:
        if self.active is not None:
            return
        pattern, reason = detect_sweep_pattern(self.minutes, index)
        if reason is not None:
            self.counts[reason] += 1
        if pattern is None:
            return
        self.active = ActiveSweep(
            pattern=pattern,
            expiry_index=index + CONFIRMATION_MINUTES,
            futures_sweep_high=pattern.initial_futures_high,
            futures_sweep_low=pattern.initial_futures_low,
            maximum_spot_excursion_fraction=pattern.spot_excursion_fraction,
            spot_confirmed=pattern.spot_confirmed_at_sweep,
            spot_confirmation_time_ns=(
                pattern.event_time_ns if pattern.spot_confirmed_at_sweep else None
            ),
        )
        self.sweep_events.append(_event(pattern))
        self.counts["futures_external_sweeps"] += 1
        if pattern.spot_confirmed_at_sweep:
            self.counts["spot_confirmed_at_sweep"] += 1
        else:
            self.counts["spot_unconfirmed_at_sweep"] += 1

    def on_minute(
        self,
        minute: JointMinute,
    ) -> tuple[list[ScenarioPlan], list[ScenarioPlan]]:
        if self.minutes and minute.start_time_ns <= self.minutes[-1].start_time_ns:
            raise ValueError("joint minute timestamp regression")
        if minute.end_time_ns - minute.start_time_ns != MINUTE_NS:
            raise ValueError("joint minute duration regression")
        if minute.futures.end_time_ns != minute.end_time_ns:
            raise ValueError("futures availability must equal exact UTC minute end")
        if minute.spot.end_time_ns != minute.end_time_ns:
            raise ValueError("spot availability must equal exact UTC minute end")
        self.minutes.append(minute)
        index = len(self.minutes) - 1
        primary_before = len(self.primary_plans)
        control_before = len(self.control_plans)
        consumed = self._process_active(index, minute)
        if not consumed:
            self._arm(index)
        return (
            self.primary_plans[primary_before:],
            self.control_plans[control_before:],
        )

    def finish(self) -> None:
        setup = self.active
        if setup is None:
            return
        self.diagnostics.append(
            self._diagnostic(
                setup,
                resolution_time_ns=None,
                failure_close=None,
                failure_imbalance=None,
                failed=False,
                primary_plan_id=None,
                control_plan_id=None,
                reason_code="DATA_END_BEFORE_RESPONSE_RESOLUTION",
            ),
        )
        self.counts["data_end_before_resolution"] += 1
        self.active = None


def build_cross_market_plans(
    minutes: Iterable[JointMinute],
) -> CrossMarketFailedAuctionStateMachine:
    machine = CrossMarketFailedAuctionStateMachine()
    for minute in minutes:
        machine.on_minute(minute)
    machine.finish()
    if len(machine.primary_plans) > len(machine.control_plans):
        raise RuntimeError("v36 primary cannot exceed futures-only control")
    primary_sources = {
        row.scenario_id.removesuffix(PRIMARY_SUFFIX)
        for row in machine.primary_plans
    }
    control_sources = {
        row.scenario_id.removesuffix(CONTROL_SUFFIX)
        for row in machine.control_plans
    }
    if not primary_sources.issubset(control_sources):
        raise RuntimeError("every v36 primary requires an identical control event")
    return machine


__all__ = [
    "BALANCE_MINUTES",
    "CONFIRMATION_MINUTES",
    "CONTROL_SUFFIX",
    "COST_PER_SIDE",
    "CrossMarketDiagnostic",
    "CrossMarketFailedAuctionStateMachine",
    "JointMinute",
    "MINUTE_NS",
    "MIN_STRUCTURE_WIDTH_FRACTION",
    "MIN_SWEEP_FRACTION",
    "MinuteBar",
    "PRIMARY_SUFFIX",
    "SweepEvent",
    "SweepPattern",
    "build_cross_market_plans",
    "build_joint_minutes",
    "detect_sweep_pattern",
    "iter_minute_bars",
]
