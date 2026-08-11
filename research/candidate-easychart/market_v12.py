"""Source-shape delayed W/M Trap interaction for candidate-easychart v12.

The Fake out/Trap PDF distinguishes an immediate single-extreme Fake out from a
Trap that spends time outside and forms a double-bottom/double-top shape before
reclaiming.  Earlier screens called every delayed outside-close/reclaim a Trap.
This module makes the stated shape a causal state transition rather than adding
another generic filter.
"""
from __future__ import annotations

from dataclasses import dataclass

from domain_v3 import Candle, Side
from market_v7 import (
    EasyChartSessionTrapEngine,
    ExpiringArmedSetup,
    SessionLiquidityRange,
    SessionTrapConfig,
)


@dataclass(slots=True)
class WMRangeState:
    liquidity_range: SessionLiquidityRange
    outside_side: Side | None = None
    outside_first_index: int | None = None
    extreme: float | None = None
    previous_close: float | None = None
    previous_low: float | None = None
    previous_high: float | None = None
    rebound_seen: bool = False
    second_leg_seen: bool = False
    completed: bool = False


class EasyChartWMTrapEngine(EasyChartSessionTrapEngine):
    """Session engine that accepts a delayed Trap only after a W/M sequence."""

    def _activate_and_expire(self, current: Candle) -> None:
        while (
            self.range_cursor < len(self.pending_ranges)
            and self.pending_ranges[self.range_cursor].trade_start_ns <= current.ts_open_ns
        ):
            liquidity_range = self.pending_ranges[self.range_cursor]
            self.range_cursor += 1
            if liquidity_range.trade_end_ns <= current.ts_open_ns:
                self._count("range_missed_before_activation")
                continue
            self.active[liquidity_range.range_id] = WMRangeState(liquidity_range)
            self._count("ranges_activated")
            self._count(f"ranges_activated_{liquidity_range.reference_family}_{liquidity_range.trade_window}")
        for range_id, state in list(self.active.items()):
            if state.liquidity_range.trade_end_ns <= current.ts_open_ns:
                self.active.pop(range_id, None)
                if not state.completed:
                    self._count("range_window_expired")

    def _start_outside(
        self,
        state: WMRangeState,
        current: Candle,
        index: int,
        side: Side,
    ) -> None:
        state.outside_side = side
        state.outside_first_index = index
        state.extreme = current.low if side is Side.LONG else current.high
        state.previous_close = current.close
        state.previous_low = current.low
        state.previous_high = current.high
        self._count("wm_outside_closes")

    def _advance_shape(self, state: WMRangeState, current: Candle) -> None:
        assert state.outside_side is not None
        assert state.previous_close is not None
        assert state.previous_low is not None
        assert state.previous_high is not None
        if state.outside_side is Side.LONG:
            if not state.rebound_seen and current.close > state.previous_close:
                state.rebound_seen = True
                self._count("w_rebound_seen")
            elif (
                state.rebound_seen
                and not state.second_leg_seen
                and current.low < state.previous_low
                and current.close < state.previous_close
            ):
                state.second_leg_seen = True
                self._count("w_second_leg_seen")
            state.extreme = min(float(state.extreme), current.low)
        else:
            if not state.rebound_seen and current.close < state.previous_close:
                state.rebound_seen = True
                self._count("m_rebound_seen")
            elif (
                state.rebound_seen
                and not state.second_leg_seen
                and current.high > state.previous_high
                and current.close > state.previous_close
            ):
                state.second_leg_seen = True
                self._count("m_second_leg_seen")
            state.extreme = max(float(state.extreme), current.high)
        state.previous_close = current.close
        state.previous_low = current.low
        state.previous_high = current.high

    def _observe_state(
        self,
        state: WMRangeState,
        current: Candle,
        index: int,
    ) -> ExpiringArmedSetup | None:
        liquidity_range = state.liquidity_range
        if state.completed:
            return None
        if not (
            liquidity_range.trade_start_ns <= current.ts_open_ns
            and current.ts_open_ns < liquidity_range.trade_end_ns
        ):
            return None
        lower_cross = current.low < liquidity_range.low
        upper_cross = current.high > liquidity_range.high
        if lower_cross and upper_cross:
            state.completed = True
            self._count("two_sided_same_bar_ambiguous")
            return None

        if state.outside_side is not None:
            side = state.outside_side
            reclaimed = (
                current.close >= liquidity_range.low
                if side is Side.LONG
                else current.close <= liquidity_range.high
            )
            if reclaimed:
                # The reclaim bar itself may complete the second leg by first
                # printing a new extreme and then closing inside.  OHLC cannot
                # establish that ordering, so shape evidence must exist before
                # this bar.
                if self.config.enable_delayed_trap and state.second_leg_seen:
                    state.completed = True
                    self._count("wm_trap_reclaims")
                    return self._build_setup(
                        state,
                        current,
                        side,
                        float(state.extreme),
                        "WM_TRAP_RETEST",
                    )
                state.completed = True
                self._count("delayed_reclaim_without_wm_shape")
                return None
            self._advance_shape(state, current)
            if self._accepted_break(
                liquidity_range,
                side,
                current.close,
                self.config.accepted_break_range_widths,
            ):
                state.completed = True
                self._count("accepted_break_full_range")
            return None

        if lower_cross:
            if current.close >= liquidity_range.low and self.config.enable_immediate_fakeout:
                state.completed = True
                self._count("immediate_fakeouts")
                return self._build_setup(state, current, Side.LONG, current.low, "IMMEDIATE_FAKEOUT_RETEST")
            if current.close < liquidity_range.low:
                if self._accepted_break(liquidity_range, Side.LONG, current.close, self.config.accepted_break_range_widths):
                    state.completed = True
                    self._count("accepted_break_full_range")
                else:
                    self._start_outside(state, current, index, Side.LONG)
            return None
        if upper_cross:
            if current.close <= liquidity_range.high and self.config.enable_immediate_fakeout:
                state.completed = True
                self._count("immediate_fakeouts")
                return self._build_setup(state, current, Side.SHORT, current.high, "IMMEDIATE_FAKEOUT_RETEST")
            if current.close > liquidity_range.high:
                if self._accepted_break(liquidity_range, Side.SHORT, current.close, self.config.accepted_break_range_widths):
                    state.completed = True
                    self._count("accepted_break_full_range")
                else:
                    self._start_outside(state, current, index, Side.SHORT)
        return None


__all__ = ["EasyChartWMTrapEngine", "WMRangeState"]
