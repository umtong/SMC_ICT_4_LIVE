"""Accepted-leg first-pullback owners translated from the RE1 branch.

The local owner is the current accepted 15-minute leg and the residual owner
is the established accepted 60-minute leg.  Both use the same physical law:
an aligned 5-minute body break with completed constituent flow, the immediate
next 5-minute outside hold, full detachment, first return, and the first later
flow-backed response.  A newly accepted same-side 5-minute level supersedes an
older untested level.  This module finds completed episodes only; the active
policy still owns target-first route geometry and global account arbitration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .domain import Bar, LiquidityBoundary, Pivot, stable_id
from .factor_continuation import CausalFlowAnalyzer, FlowObservation


NS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class AcceptedLeg:
    side: str
    pivot: Pivot
    break_time_ns: int
    accepted_time_ns: int


@dataclass(frozen=True, slots=True)
class PendingLeg:
    side: str
    pivot: Pivot
    break_time_ns: int


@dataclass(slots=True)
class AcceptedPullbackSetup:
    setup_id: str
    owner: str
    side: str
    leg: AcceptedLeg
    break_pivot: Pivot
    break_time_ns: int
    break_high: float
    break_low: float
    state: str = "WAITING_HOLD"
    hold_time_ns: int | None = None
    detached_time_ns: int | None = None
    retest_time_ns: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    destination: LiquidityBoundary | None = None


@dataclass(frozen=True, slots=True)
class AcceptedPullbackCompletion:
    setup: AcceptedPullbackSetup
    decision_bar: Bar
    response: FlowObservation
    response_mechanism: str


class AcceptedPullbackOwners:
    """Current 15m leg first, then the non-overlapping residual 60m leg."""

    def __init__(self, symbol: str, tick_size: float) -> None:
        self.symbol = symbol
        self.tick_size = float(tick_size)
        self._processed = {15: 0, 60: 0, 5: 0}
        self._pending_leg: dict[int, PendingLeg | None] = {15: None, 60: None}
        self._accepted_leg: dict[int, AcceptedLeg | None] = {15: None, 60: None}
        self._used_leg_pivots: dict[int, set[str]] = {15: set(), 60: set()}
        self._used_break_pivots: set[str] = set()
        self._setups: dict[str, AcceptedPullbackSetup] = {}

    @staticmethod
    def _breaks(side: str, pivot: Pivot, previous: Bar, bar: Bar) -> bool:
        if side == "LONG":
            return (
                previous.close <= pivot.price
                and bar.close > pivot.price
                and bar.close > bar.open
            )
        return (
            previous.close >= pivot.price
            and bar.close < pivot.price
            and bar.close < bar.open
        )

    @staticmethod
    def _holds(side: str, pivot: Pivot, bar: Bar) -> bool:
        if side == "LONG":
            return bar.open > pivot.price and bar.close > pivot.price
        return bar.open < pivot.price and bar.close < pivot.price

    @staticmethod
    def _side_for_pivot(pivot: Pivot) -> str:
        return "LONG" if pivot.side == "HIGH" else "SHORT"

    def _finish(self, setup: AcceptedPullbackSetup) -> None:
        self._setups.pop(setup.setup_id, None)

    def _clear_opposite(self, side: str, owner: str) -> None:
        for setup in tuple(self._setups.values()):
            if setup.owner == owner and setup.side != side:
                self._finish(setup)

    def _process_leg_bar(
        self,
        timeframe: int,
        previous: Bar,
        bar: Bar,
        pivots: Sequence[Pivot],
    ) -> None:
        pending = self._pending_leg[timeframe]
        self._pending_leg[timeframe] = None
        if pending is not None:
            expected = pending.break_time_ns + timeframe * NS_PER_MINUTE
            if bar.close_time_ns == expected and self._holds(
                pending.side, pending.pivot, bar,
            ):
                leg = AcceptedLeg(
                    pending.side,
                    pending.pivot,
                    pending.break_time_ns,
                    bar.close_time_ns,
                )
                self._accepted_leg[timeframe] = leg
                self._clear_opposite(
                    leg.side,
                    "LOCAL_15M" if timeframe == 15 else "RESIDUAL_60M",
                )
            else:
                self._used_leg_pivots[timeframe].discard(pending.pivot.pivot_id)

        candidates: list[tuple[str, Pivot]] = []
        for pivot in pivots:
            if (
                pivot.pivot_id in self._used_leg_pivots[timeframe]
                or pivot.observed_time_ns >= bar.close_time_ns
            ):
                continue
            side = self._side_for_pivot(pivot)
            if self._breaks(side, pivot, previous, bar):
                candidates.append((side, pivot))
        if not candidates:
            return
        side, pivot = max(
            candidates,
            key=lambda item: (
                item[1].event_time_ns,
                item[1].observed_time_ns,
                item[1].pivot_id,
            ),
        )
        self._used_leg_pivots[timeframe].add(pivot.pivot_id)
        current = self._accepted_leg[timeframe]
        if current is not None and current.side == side:
            self._accepted_leg[timeframe] = AcceptedLeg(
                side, pivot, bar.close_time_ns, current.accepted_time_ns,
            )
        else:
            self._pending_leg[timeframe] = PendingLeg(
                side, pivot, bar.close_time_ns,
            )

    @staticmethod
    def _aligned_flow(
        side: str,
        observations: Sequence[FlowObservation],
    ) -> bool:
        if not observations:
            return False
        sign = 1.0 if side == "LONG" else -1.0
        cumulative = sign * sum(item.signed_taker_quote for item in observations)
        progress = sign * (observations[-1].close - observations[0].open)
        coherent = any(
            item.active
            and item.directed
            and item.material_progress
            and sign * item.signed_taker_quote > 0.0
            and sign * item.body > 0.0
            for item in observations
        )
        return cumulative > 0.0 and progress > 0.0 and coherent

    def _arm_break(
        self,
        previous: Bar,
        bar: Bar,
        pivots: Sequence[Pivot],
        flow: CausalFlowAnalyzer,
    ) -> None:
        candidates: list[Pivot] = []
        owners: dict[str, tuple[str, AcceptedLeg]] = {}
        local = self._accepted_leg[15]
        macro = self._accepted_leg[60]
        for pivot in pivots:
            if (
                pivot.pivot_id in self._used_break_pivots
                or pivot.observed_time_ns >= bar.close_time_ns
            ):
                continue
            side = self._side_for_pivot(pivot)
            owner: tuple[str, AcceptedLeg] | None = None
            if local is not None and local.side == side:
                owner = ("LOCAL_15M", local)
            elif macro is not None and macro.side == side:
                owner = ("RESIDUAL_60M", macro)
            if owner is not None and self._breaks(side, pivot, previous, bar):
                candidates.append(pivot)
                owners[pivot.pivot_id] = owner
        if not candidates:
            return
        pivot = max(
            candidates,
            key=lambda item: (
                item.event_time_ns, item.observed_time_ns, item.pivot_id,
            ),
        )
        owner, leg = owners[pivot.pivot_id]
        observations = flow.between(bar.open_time_ns, bar.close_time_ns)
        if not self._aligned_flow(leg.side, observations):
            return
        self._used_break_pivots.add(pivot.pivot_id)
        setup_id = stable_id(
            self.symbol,
            owner,
            leg.pivot.pivot_id,
            pivot.pivot_id,
            bar.close_time_ns,
            prefix="ACCEPTED_PULLBACK:",
        )
        self._setups[setup_id] = AcceptedPullbackSetup(
            setup_id=setup_id,
            owner=owner,
            side=leg.side,
            leg=leg,
            break_pivot=pivot,
            break_time_ns=bar.close_time_ns,
            break_high=bar.high,
            break_low=bar.low,
        )

    def observe_completed_bars(
        self,
        *,
        five: Sequence[Bar],
        fifteen: Sequence[Bar],
        sixty: Sequence[Bar],
        pivots: Mapping[int, Sequence[Pivot]],
        flow: CausalFlowAnalyzer,
        destinations: Iterable[LiquidityBoundary],
        claimed_break_time_ns: int | None = None,
    ) -> None:
        for timeframe, bars in ((60, sixty), (15, fifteen)):
            start = self._processed[timeframe]
            for index in range(max(1, start), len(bars)):
                self._process_leg_bar(
                    timeframe,
                    bars[index - 1],
                    bars[index],
                    pivots.get(timeframe, ()),
                )
            self._processed[timeframe] = len(bars)

        start = self._processed[5]
        for index in range(max(1, start), len(five)):
            bar = five[index]
            self._advance_holds(bar, tuple(destinations))
            if bar.close_time_ns != claimed_break_time_ns:
                self._arm_break(five[index - 1], bar, pivots.get(5, ()), flow)
        self._processed[5] = len(five)

    def _advance_holds(
        self,
        bar: Bar,
        destinations: Sequence[LiquidityBoundary],
    ) -> None:
        for setup in tuple(self._setups.values()):
            if setup.state != "WAITING_HOLD":
                continue
            expected = setup.break_time_ns + 5 * NS_PER_MINUTE
            if bar.close_time_ns < expected:
                continue
            if bar.close_time_ns != expected or not self._holds(
                setup.side, setup.break_pivot, bar,
            ):
                self._finish(setup)
                continue
            wanted = "HIGH" if setup.side == "LONG" else "LOW"
            sign = 1.0 if setup.side == "LONG" else -1.0
            reference = bar.high if setup.side == "LONG" else bar.low
            available = [
                item
                for item in destinations
                if item.side == wanted
                and item.observed_time_ns < bar.close_time_ns
                and (
                    item.consumed_time_ns is None
                    or item.consumed_time_ns > bar.close_time_ns
                )
                and sign * (item.price - reference) > self.tick_size
            ]
            if not available:
                self._finish(setup)
                continue
            setup.destination = min(
                available,
                key=lambda item: (
                    sign * (item.price - reference),
                    -item.timeframe_minutes,
                    -item.strength,
                    item.boundary_id,
                ),
            )
            setup.hold_time_ns = bar.close_time_ns
            setup.detached_time_ns = None
            setup.state = "WAITING_RETEST"
            for older in tuple(self._setups.values()):
                if (
                    older.setup_id != setup.setup_id
                    and older.owner == setup.owner
                    and older.side == setup.side
                    and older.break_time_ns < setup.break_time_ns
                    and older.state in {"WAITING_RETEST", "WAITING_RESPONSE"}
                ):
                    self._finish(older)

    @staticmethod
    def _response_mechanism(
        side: str,
        observation: FlowObservation | None,
    ) -> str | None:
        if observation is None or not observation.active or not observation.directed:
            return None
        sign = 1.0 if side == "LONG" else -1.0
        intended = sign * observation.body > 0.0
        signed = sign * observation.signed_taker_quote
        if signed > 0.0 and intended and observation.material_progress:
            return "AGGRESSOR_INITIATIVE_CONTROL"
        if signed < 0.0 and intended:
            return "OPPOSING_AGGRESSION_ABSORBED"
        return None

    def advance_minute(
        self,
        bar: Bar,
        observation: FlowObservation | None,
    ) -> list[AcceptedPullbackCompletion]:
        output: list[AcceptedPullbackCompletion] = []
        for setup in tuple(self._setups.values()):
            if setup.state not in {"WAITING_RETEST", "WAITING_RESPONSE"}:
                continue
            if setup.hold_time_ns is None or bar.close_time_ns <= setup.hold_time_ns:
                continue
            destination = setup.destination
            if destination is None:
                self._finish(setup)
                continue
            target_spent = (
                bar.high >= destination.price
                if setup.side == "LONG"
                else bar.low <= destination.price
            )
            if target_spent:
                self._finish(setup)
                continue
            pivot = setup.break_pivot.price
            if setup.state == "WAITING_RETEST":
                if setup.detached_time_ns is None:
                    fully_detached = (
                        bar.low > pivot + self.tick_size
                        if setup.side == "LONG"
                        else bar.high < pivot - self.tick_size
                    )
                    if fully_detached:
                        setup.detached_time_ns = bar.close_time_ns
                    # Detachment is a completed observation before the return.
                    continue
                touched = (
                    bar.low <= pivot + self.tick_size
                    and bar.high >= pivot - self.tick_size
                )
                if not touched:
                    continue
                held = bar.close > pivot if setup.side == "LONG" else bar.close < pivot
                if not held:
                    self._finish(setup)
                    continue
                setup.retest_time_ns = bar.close_time_ns
                setup.retest_high = bar.high
                setup.retest_low = bar.low
                setup.state = "WAITING_RESPONSE"
                continue
            if setup.retest_time_ns is None or bar.close_time_ns <= setup.retest_time_ns:
                continue
            assert setup.retest_high is not None and setup.retest_low is not None
            response_price = (
                bar.close > setup.retest_high
                if setup.side == "LONG"
                else bar.close < setup.retest_low
            )
            mechanism = self._response_mechanism(setup.side, observation)
            if not response_price or mechanism is None:
                self._finish(setup)
                continue
            output.append(
                AcceptedPullbackCompletion(setup, bar, observation, mechanism),
            )
            self._finish(setup)
        return output

    @property
    def accepted_local_leg(self) -> AcceptedLeg | None:
        return self._accepted_leg[15]

    @property
    def accepted_macro_leg(self) -> AcceptedLeg | None:
        return self._accepted_leg[60]
