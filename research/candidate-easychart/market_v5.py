"""Intrinsic-time structural salience and delayed Trap logic for v5.

The PDFs repeatedly use words such as "meaningful" swing, obvious structure,
contraction and a delayed W/M-shaped Trap.  A human does not treat every fixed
number of bars as a new market structure.  This module replaces the v4
fixed-span pivot stream with a causal volatility-adaptive directional-change
stream: a wick extreme becomes a structural pivot only after price closes a
prior-ATR-sized distance away from it.

This is not an extra safety filter.  It changes the representation of market
structure so each detected point denotes one completed auction leg.  The
channel, liquidity interaction, sponsored OB/BOS/FVG response, first mitigation,
fixed stop and fixed opposite-boundary target remain the source-defined roles.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from domain_v3 import Candle, Side
from market_v4 import (
    EasyChartStructuralEpisodeEngine,
    OutsideExcursion,
    ParallelChannel,
    ScenarioConfigV4,
    StructuralPivot,
)


@dataclass(frozen=True, slots=True)
class ScenarioConfigV5(ScenarioConfigV4):
    channel_dc_atr_multiple: float = 1.0
    micro_dc_atr_multiple: float = 1.0
    dc_atr_period: int = 14
    accepted_break_channel_widths: float = 1.0

    def __post_init__(self) -> None:
        for name, value in (
            ("channel_dc_atr_multiple", self.channel_dc_atr_multiple),
            ("micro_dc_atr_multiple", self.micro_dc_atr_multiple),
            ("accepted_break_channel_widths", self.accepted_break_channel_widths),
        ):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be positive")
        if self.dc_atr_period < 2:
            raise ValueError("dc_atr_period must be at least two")


class DirectionalChangePivotDetector:
    """Online wick-extreme pivots confirmed by a close reversal of prior ATR.

    Thresholds are computed from true ranges that were fully known before the
    current candle opened.  A newly printed extreme cannot be confirmed by the
    same candle, avoiding an unknowable intrabar order.  Confirmed HIGH and LOW
    events alternate by construction.
    """

    def __init__(
        self,
        *,
        timeframe_minutes: int,
        atr_period: int = 14,
        atr_multiple: float = 1.0,
    ) -> None:
        if timeframe_minutes <= 0:
            raise ValueError("timeframe_minutes must be positive")
        if atr_period < 2:
            raise ValueError("atr_period must be at least two")
        if not math.isfinite(atr_multiple) or atr_multiple <= 0.0:
            raise ValueError("atr_multiple must be positive")
        self.timeframe_minutes = int(timeframe_minutes)
        self.atr_period = int(atr_period)
        self.atr_multiple = float(atr_multiple)
        self.mode: str | None = None  # UP tracks a HIGH; DOWN tracks a LOW.
        self.running_high: float | None = None
        self.running_high_index: int | None = None
        self.running_high_time_ns: int | None = None
        self.running_low: float | None = None
        self.running_low_index: int | None = None
        self.running_low_time_ns: int | None = None
        self.previous_close: float | None = None
        self.true_ranges: list[float] = []
        self.last_pivot_side: str | None = None
        self.diagnostics: dict[str, int] = {}

    def _count(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _threshold(self) -> float | None:
        if len(self.true_ranges) < self.atr_period:
            return None
        atr = sum(self.true_ranges[-self.atr_period :]) / self.atr_period
        threshold = atr * self.atr_multiple
        return threshold if math.isfinite(threshold) and threshold > 0.0 else None

    def _append_true_range(self, candle: Candle) -> None:
        if self.previous_close is None:
            value = candle.high - candle.low
        else:
            value = max(
                candle.high - candle.low,
                abs(candle.high - self.previous_close),
                abs(candle.low - self.previous_close),
            )
        self.true_ranges.append(float(value))
        self.previous_close = candle.close

    def _high_pivot(self, candle: Candle, observed_index: int) -> StructuralPivot:
        assert self.running_high is not None
        assert self.running_high_index is not None
        assert self.running_high_time_ns is not None
        return StructuralPivot(
            center_index=self.running_high_index,
            observed_index=observed_index,
            side="HIGH",
            level=self.running_high,
            event_time_ns=self.running_high_time_ns,
            observed_time_ns=candle.ts_close_ns,
        )

    def _low_pivot(self, candle: Candle, observed_index: int) -> StructuralPivot:
        assert self.running_low is not None
        assert self.running_low_index is not None
        assert self.running_low_time_ns is not None
        return StructuralPivot(
            center_index=self.running_low_index,
            observed_index=observed_index,
            side="LOW",
            level=self.running_low,
            event_time_ns=self.running_low_time_ns,
            observed_time_ns=candle.ts_close_ns,
        )

    def on_candle(self, candle: Candle, index: int) -> StructuralPivot | None:
        if self.running_high is None:
            self.running_high = candle.high
            self.running_high_index = index
            self.running_high_time_ns = candle.ts_close_ns
            self.running_low = candle.low
            self.running_low_index = index
            self.running_low_time_ns = candle.ts_close_ns
            self._append_true_range(candle)
            return None

        threshold = self._threshold()
        pivot: StructuralPivot | None = None

        # Test reversal from an extreme known before this candle.  Updating the
        # extreme happens only after this test if no reversal is confirmed.
        if threshold is not None:
            if self.mode == "UP":
                assert self.running_high is not None
                if candle.close <= self.running_high - threshold:
                    pivot = self._high_pivot(candle, index)
                    self.mode = "DOWN"
            elif self.mode == "DOWN":
                assert self.running_low is not None
                if candle.close >= self.running_low + threshold:
                    pivot = self._low_pivot(candle, index)
                    self.mode = "UP"
            else:
                assert self.running_high is not None and self.running_low is not None
                high_reversal = candle.close <= self.running_high - threshold
                low_reversal = candle.close >= self.running_low + threshold
                # Before the first event, the later of the two running extremes
                # identifies the most recent completed leg.  Ambiguous equality
                # is left unresolved rather than imposing an arbitrary order.
                if (
                    high_reversal
                    and not low_reversal
                    and (self.running_high_index or 0) > (self.running_low_index or 0)
                ):
                    pivot = self._high_pivot(candle, index)
                    self.mode = "DOWN"
                elif (
                    low_reversal
                    and not high_reversal
                    and (self.running_low_index or 0) > (self.running_high_index or 0)
                ):
                    pivot = self._low_pivot(candle, index)
                    self.mode = "UP"
                elif high_reversal or low_reversal:
                    self._count("initial_reversal_ambiguous")

        if pivot is not None:
            if self.last_pivot_side == pivot.side:
                raise AssertionError("directional-change pivots must alternate")
            self.last_pivot_side = pivot.side
            self._count(f"confirmed_{pivot.side.lower()}")
            if pivot.side == "HIGH":
                self.running_low = candle.low
                self.running_low_index = index
                self.running_low_time_ns = candle.ts_close_ns
                self.running_high = candle.high
                self.running_high_index = index
                self.running_high_time_ns = candle.ts_close_ns
            else:
                self.running_high = candle.high
                self.running_high_index = index
                self.running_high_time_ns = candle.ts_close_ns
                self.running_low = candle.low
                self.running_low_index = index
                self.running_low_time_ns = candle.ts_close_ns
        else:
            if self.mode != "DOWN" and candle.high > float(self.running_high):
                self.running_high = candle.high
                self.running_high_index = index
                self.running_high_time_ns = candle.ts_close_ns
            if self.mode != "UP" and candle.low < float(self.running_low):
                self.running_low = candle.low
                self.running_low_index = index
                self.running_low_time_ns = candle.ts_close_ns

        self._append_true_range(candle)
        return pivot


class EasyChartIntrinsicStructureEngine(EasyChartStructuralEpisodeEngine):
    """v4 sponsored episode logic with a genuinely delayed Trap lifecycle."""

    config: ScenarioConfigV5

    def __init__(self, symbol: str, config: ScenarioConfigV5) -> None:
        super().__init__(symbol, config)
        self.config = config

    def add_directional_channel_pivot(
        self,
        pivot: StructuralPivot,
        candles: list[Candle],
    ) -> None:
        self.structure_pivots.append(pivot)
        self.structure_pivots = self.structure_pivots[-12:]
        self._count(f"channel_pivots_{pivot.side.lower()}")
        channel = self._latest_channel(candles, pivot)
        if channel is None or channel.channel_id in self.used_channel_ids:
            return
        self.active_channel = channel
        self.outside = None
        self.used_channel_ids.add(channel.channel_id)
        self._count("channels_formed")
        self._count(f"channels_expect_{channel.expected_side.name.lower()}")

    def add_directional_micro_pivot(self, pivot: StructuralPivot) -> None:
        if pivot.side == "HIGH":
            self.micro_high = pivot
        else:
            self.micro_low = pivot
        self._count(f"micro_pivots_{pivot.side.lower()}")

    @staticmethod
    def _outside_distance(
        channel: ParallelChannel,
        current: Candle,
    ) -> tuple[float, float]:
        boundary = channel.entry_boundary(current.ts_close_ns)
        if channel.expected_side is Side.LONG:
            return boundary, max(0.0, boundary - current.close)
        return boundary, max(0.0, current.close - boundary)

    def _accepted_break(
        self,
        channel: ParallelChannel,
        current: Candle,
    ) -> bool:
        _, distance = self._outside_distance(channel, current)
        return distance >= channel.width * self.config.accepted_break_channel_widths

    def _observe_channel_interaction(self, current: Candle, index: int) -> None:
        channel = self.active_channel
        if channel is None or channel.observed_time_ns >= current.ts_open_ns:
            return
        boundary = channel.entry_boundary(current.ts_close_ns)
        if channel.expected_side is Side.LONG:
            touched = current.low <= boundary
            crossed = current.low < boundary
            inside = current.close >= boundary
            extreme = current.low
            remains_outside = current.close < boundary
        else:
            touched = current.high >= boundary
            crossed = current.high > boundary
            inside = current.close <= boundary
            extreme = current.high
            remains_outside = current.close > boundary

        if self.outside is not None and self.outside.channel.channel_id == channel.channel_id:
            if channel.expected_side is Side.LONG:
                self.outside.extreme = min(self.outside.extreme, current.low)
            else:
                self.outside.extreme = max(self.outside.extreme, current.high)

            if inside and self.config.enable_one_bar_trap:
                duration = index - self.outside.first_index
                self._count("delayed_trap_reclaims")
                self._count(f"delayed_trap_duration_{min(duration, 20)}")
                self._interaction(
                    channel,
                    current,
                    index,
                    "CHANNEL_POINT4_DELAYED_TRAP",
                    self.outside.extreme,
                )
                return
            if remains_outside and self._accepted_break(channel, current):
                self._count("channel_accepted_break_full_width")
                self.active_channel = None
                self.outside = None
            return

        if crossed and inside and self.config.enable_immediate_fakeout:
            self._interaction(
                channel,
                current,
                index,
                "CHANNEL_POINT4_FAKEOUT",
                extreme,
            )
            return
        if touched and inside and self.config.enable_boundary_touch:
            self._interaction(
                channel,
                current,
                index,
                "CHANNEL_POINT4_TOUCH",
                extreme,
            )
            return
        if crossed and remains_outside:
            if self._accepted_break(channel, current):
                self._count("channel_accepted_break_full_width")
                self.active_channel = None
                return
            self.outside = OutsideExcursion(
                channel=channel,
                first_index=index,
                extreme=extreme,
            )
            self._count("channel_outside_close")


__all__ = [
    "DirectionalChangePivotDetector",
    "EasyChartIntrinsicStructureEngine",
    "ScenarioConfigV5",
]
