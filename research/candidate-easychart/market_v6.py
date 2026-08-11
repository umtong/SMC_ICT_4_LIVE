"""Directional-change liquidity range episodes for candidate-easychart v6.

Channels are only one source-defined structure.  The PDFs also make repeated
use of meaningful swing highs/lows, box boundaries and support/resistance as
liquidity pools.  v6 therefore uses the latest alternating intrinsic-time HIGH
and LOW as the current dealing range, waits for one boundary to be swept and
reclaimed, then reuses the same sponsored OB -> BOS/FVG -> first mitigation
logic and targets the opposite boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

from domain_v3 import Candle, Side
from market_v4 import ParallelChannel, StructuralPivot
from market_v5 import EasyChartIntrinsicStructureEngine, ScenarioConfigV5


@dataclass(frozen=True, slots=True)
class DirectionalLiquidityRange:
    range_id: str
    observed_time_ns: int
    high: StructuralPivot
    low: StructuralPivot

    @property
    def upper(self) -> float:
        return self.high.level

    @property
    def lower(self) -> float:
        return self.low.level

    @property
    def width(self) -> float:
        return self.upper - self.lower


@dataclass(slots=True)
class RangeExcursion:
    liquidity_range: DirectionalLiquidityRange
    side: Side
    first_index: int
    extreme: float


class EasyChartDirectionalLiquidityEngine(EasyChartIntrinsicStructureEngine):
    """Meaningful DC range -> sweep/reclaim -> sponsored mitigation."""

    def __init__(self, symbol: str, config: ScenarioConfigV5) -> None:
        super().__init__(symbol, config)
        self.latest_context_high: StructuralPivot | None = None
        self.latest_context_low: StructuralPivot | None = None
        self.active_liquidity_range: DirectionalLiquidityRange | None = None
        self.range_excursion: RangeExcursion | None = None
        self.used_range_ids: set[str] = set()

    def add_directional_context_pivot(self, pivot: StructuralPivot) -> None:
        if pivot.side == "HIGH":
            self.latest_context_high = pivot
        else:
            self.latest_context_low = pivot
        self._count(f"context_pivots_{pivot.side.lower()}")
        high = self.latest_context_high
        low = self.latest_context_low
        if high is None or low is None or high.level <= low.level + self.config.tick_size:
            return
        observed = max(high.observed_time_ns, low.observed_time_ns)
        range_id = f"{self.symbol}:DC_RANGE:{high.event_time_ns}:{low.event_time_ns}"
        if range_id in self.used_range_ids:
            return
        self.active_liquidity_range = DirectionalLiquidityRange(
            range_id=range_id,
            observed_time_ns=observed,
            high=high,
            low=low,
        )
        self.range_excursion = None
        self.used_range_ids.add(range_id)
        self._count("liquidity_ranges_formed")

    @staticmethod
    def _pseudo_channel(
        liquidity_range: DirectionalLiquidityRange,
        side: Side,
    ) -> ParallelChannel:
        if side is Side.LONG:
            anchor_side = "HIGH"
            base_level = liquidity_range.upper
        else:
            anchor_side = "LOW"
            base_level = liquidity_range.lower
        return ParallelChannel(
            channel_id=f"{liquidity_range.range_id}:{side.name}",
            observed_time_ns=liquidity_range.observed_time_ns,
            timeframe_minutes=0,
            anchor_side=anchor_side,
            expected_side=side,
            base_time_ns=liquidity_range.observed_time_ns,
            base_level=base_level,
            slope_per_ns=0.0,
            width=liquidity_range.width,
            p1=liquidity_range.high,
            p2=liquidity_range.low,
            p3=liquidity_range.high,
        )

    def _complete_range_interaction(
        self,
        liquidity_range: DirectionalLiquidityRange,
        side: Side,
        current: Candle,
        index: int,
        family: str,
        extreme: float,
    ) -> None:
        channel = self._pseudo_channel(liquidity_range, side)
        self._interaction(channel, current, index, family, extreme)
        self.active_liquidity_range = None
        self.range_excursion = None

    def _accepted_range_break(
        self,
        liquidity_range: DirectionalLiquidityRange,
        side: Side,
        close: float,
    ) -> bool:
        if side is Side.LONG:
            return close <= liquidity_range.lower - liquidity_range.width
        return close >= liquidity_range.upper + liquidity_range.width

    def _observe_channel_interaction(self, current: Candle, index: int) -> None:
        liquidity_range = self.active_liquidity_range
        if liquidity_range is None or liquidity_range.observed_time_ns >= current.ts_open_ns:
            return

        lower_cross = current.low < liquidity_range.lower
        upper_cross = current.high > liquidity_range.upper
        if lower_cross and upper_cross:
            self._count("range_two_sided_same_bar_ambiguous")
            self.active_liquidity_range = None
            self.range_excursion = None
            return

        excursion = self.range_excursion
        if excursion is not None:
            if excursion.liquidity_range.range_id != liquidity_range.range_id:
                self.range_excursion = None
                return
            if excursion.side is Side.LONG:
                excursion.extreme = min(excursion.extreme, current.low)
                reclaimed = current.close >= liquidity_range.lower
            else:
                excursion.extreme = max(excursion.extreme, current.high)
                reclaimed = current.close <= liquidity_range.upper
            if reclaimed and self.config.enable_one_bar_trap:
                self._count("dc_range_delayed_trap_reclaims")
                self._complete_range_interaction(
                    liquidity_range,
                    excursion.side,
                    current,
                    index,
                    "DC_SWING_DELAYED_TRAP",
                    excursion.extreme,
                )
                return
            if self._accepted_range_break(liquidity_range, excursion.side, current.close):
                self._count("dc_range_accepted_break_full_width")
                self.active_liquidity_range = None
                self.range_excursion = None
            return

        if lower_cross:
            if current.close >= liquidity_range.lower and self.config.enable_immediate_fakeout:
                self._complete_range_interaction(
                    liquidity_range,
                    Side.LONG,
                    current,
                    index,
                    "DC_SWING_FAKEOUT",
                    current.low,
                )
            elif current.close < liquidity_range.lower:
                if self._accepted_range_break(liquidity_range, Side.LONG, current.close):
                    self._count("dc_range_accepted_break_full_width")
                    self.active_liquidity_range = None
                else:
                    self.range_excursion = RangeExcursion(
                        liquidity_range=liquidity_range,
                        side=Side.LONG,
                        first_index=index,
                        extreme=current.low,
                    )
                    self._count("dc_range_outside_close")
            return

        if upper_cross:
            if current.close <= liquidity_range.upper and self.config.enable_immediate_fakeout:
                self._complete_range_interaction(
                    liquidity_range,
                    Side.SHORT,
                    current,
                    index,
                    "DC_SWING_FAKEOUT",
                    current.high,
                )
            elif current.close > liquidity_range.upper:
                if self._accepted_range_break(liquidity_range, Side.SHORT, current.close):
                    self._count("dc_range_accepted_break_full_width")
                    self.active_liquidity_range = None
                else:
                    self.range_excursion = RangeExcursion(
                        liquidity_range=liquidity_range,
                        side=Side.SHORT,
                        first_index=index,
                        extreme=current.high,
                    )
                    self._count("dc_range_outside_close")


__all__ = [
    "DirectionalLiquidityRange",
    "EasyChartDirectionalLiquidityEngine",
    "RangeExcursion",
]
