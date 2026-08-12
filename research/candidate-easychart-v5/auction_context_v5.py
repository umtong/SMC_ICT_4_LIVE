"""Causal directional and cross-asset context diagnostics for EasyChart v5.

The supplied trading cases do not choose a pattern in isolation.  They first
compare market direction, Bitcoin versus altcoin behaviour, the strongest
current trend and where trading activity is concentrated.  This module makes
those cues observable without yet turning them into an optimized filter.

Only completed bars are used.  Confirmed wick pivots provide the same
source-grounded definition of market structure already used by the scenario
engine.  Rolling 24-hour return and notional activity are recorded as explicit
research diagnostics because the source says "recent/today" but does not give
an exact machine window.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import math
from typing import Any

from domain import Candle


SOURCE_CONTEXT_RULES: tuple[str, ...] = (
    "SOURCE_EXPLICIT:MARKET_STRUCTURE_SUPPLIES_DIRECTION",
    "SOURCE_EXPLICIT:RELATIVE_MARKET_TREND_INFORMS_INSTRUMENT_AND_SIDE_SELECTION",
    "SOURCE_EXPLICIT:TRADING_ACTIVITY_IDENTIFIES_WHERE_CURRENT_FLOW_IS_CONCENTRATED",
)

CONTEXT_HYPOTHESES: tuple[str, ...] = (
    "RESEARCH_HYPOTHESIS:CONFIRMED_DECISION_TIMEFRAME_PIVOTS_PROXY_CURRENT_TREND",
    "RESEARCH_HYPOTHESIS:ROLLING_24H_RETURN_PROXIES_RECENT_RELATIVE_STRENGTH",
    "RESEARCH_HYPOTHESIS:ROLLING_24H_NOTIONAL_VOLUME_PROXIES_CURRENT_ACTIVITY",
)


class AuctionState(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    TRANSITION = "TRANSITION"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class ContextPivot:
    side: str
    price: float
    event_time_ns: int
    observed_time_ns: int
    span: int
    index: int


@dataclass(frozen=True, slots=True)
class AuctionContextSnapshot:
    symbol: str
    timeframe_minutes: int
    observed_time_ns: int
    close: float
    local_state: AuctionState
    structural_state: AuctionState
    local_span: int
    structural_span: int
    local_last_high: float | None
    local_previous_high: float | None
    local_last_low: float | None
    local_previous_low: float | None
    structural_last_high: float | None
    structural_previous_high: float | None
    structural_last_low: float | None
    structural_previous_low: float | None
    return_24h: float | None
    notional_volume_24h: float | None
    range_position_24h: float | None
    bars_observed: int
    provenance: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        output = asdict(self)
        output["local_state"] = self.local_state.value
        output["structural_state"] = self.structural_state.value
        return output


class CausalAuctionContext:
    """Online HH/HL, LH/LL and recent-activity context from closed bars."""

    def __init__(
        self,
        symbol: str,
        timeframe_minutes: int,
        *,
        pivot_spans: tuple[int, ...] = (2, 6),
    ) -> None:
        if timeframe_minutes <= 0:
            raise ValueError("timeframe_minutes must be positive")
        if len(pivot_spans) < 2 or any(span <= 0 for span in pivot_spans):
            raise ValueError("at least two positive pivot spans are required")
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.pivot_spans = tuple(sorted(set(pivot_spans)))
        self.local_span = self.pivot_spans[0]
        self.structural_span = self.pivot_spans[-1]
        self.bars: list[Candle] = []
        self.pivots: dict[int, list[ContextPivot]] = {
            span: [] for span in self.pivot_spans
        }
        self._pivot_ids: set[tuple[int, str, int]] = set()
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _register(self, observed_index: int) -> None:
        for span in self.pivot_spans:
            center = observed_index - span
            if center < span:
                continue
            window = self.bars[center - span : center + span + 1]
            if len(window) != 2 * span + 1:
                continue
            pivot = self.bars[center]
            highs = [bar.high for bar in window]
            lows = [bar.low for bar in window]
            if pivot.high == max(highs) and highs.count(pivot.high) == 1:
                self._add_pivot("HIGH", pivot.high, center, observed_index, span)
            if pivot.low == min(lows) and lows.count(pivot.low) == 1:
                self._add_pivot("LOW", pivot.low, center, observed_index, span)

    def _add_pivot(
        self,
        side: str,
        price: float,
        center: int,
        observed_index: int,
        span: int,
    ) -> None:
        identity = (span, side, center)
        if identity in self._pivot_ids:
            return
        self._pivot_ids.add(identity)
        self.pivots[span].append(
            ContextPivot(
                side=side,
                price=price,
                event_time_ns=self.bars[center].ts_close_ns,
                observed_time_ns=self.bars[observed_index].ts_close_ns,
                span=span,
                index=center,
            ),
        )
        self._inc(f"context_{side.lower()}_confirmed_s{span}")

    def on_bar(self, bar: Candle) -> AuctionContextSnapshot:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("context bars must arrive in increasing close time")
        self.bars.append(bar)
        self._register(len(self.bars) - 1)
        return self.snapshot()

    def _two(self, span: int, side: str) -> tuple[ContextPivot | None, ContextPivot | None]:
        values = [pivot for pivot in self.pivots[span] if pivot.side == side]
        if len(values) < 2:
            return None, None
        return values[-2], values[-1]

    def state(self, span: int) -> AuctionState:
        previous_high, last_high = self._two(span, "HIGH")
        previous_low, last_low = self._two(span, "LOW")
        if None in {previous_high, last_high, previous_low, last_low}:
            return AuctionState.UNRESOLVED
        assert previous_high is not None and last_high is not None
        assert previous_low is not None and last_low is not None
        higher_high = last_high.price > previous_high.price
        higher_low = last_low.price > previous_low.price
        lower_high = last_high.price < previous_high.price
        lower_low = last_low.price < previous_low.price
        if higher_high and higher_low:
            return AuctionState.UP
        if lower_high and lower_low:
            return AuctionState.DOWN
        return AuctionState.TRANSITION

    def _prices(self, span: int, side: str) -> tuple[float | None, float | None]:
        previous, last = self._two(span, side)
        return (
            None if last is None else last.price,
            None if previous is None else previous.price,
        )

    def _rolling_24h(self) -> tuple[float | None, float | None, float | None]:
        if not self.bars:
            return None, None, None
        periods = max(1, 1440 // self.timeframe_minutes)
        sample = self.bars[-periods:]
        notional = sum(bar.close * bar.volume for bar in sample)
        high = max(bar.high for bar in sample)
        low = min(bar.low for bar in sample)
        close = self.bars[-1].close
        position = None if high <= low else (close - low) / (high - low)
        prior_index = len(self.bars) - periods - 1
        recent_return = (
            None
            if prior_index < 0 or self.bars[prior_index].close <= 0.0
            else close / self.bars[prior_index].close - 1.0
        )
        values = (recent_return, notional, position)
        if not all(value is None or math.isfinite(value) for value in values):
            raise RuntimeError("non-finite rolling context")
        return recent_return, notional, position

    def snapshot(self) -> AuctionContextSnapshot:
        if not self.bars:
            raise RuntimeError("context snapshot requested before any bar")
        local_last_high, local_previous_high = self._prices(self.local_span, "HIGH")
        local_last_low, local_previous_low = self._prices(self.local_span, "LOW")
        structural_last_high, structural_previous_high = self._prices(
            self.structural_span,
            "HIGH",
        )
        structural_last_low, structural_previous_low = self._prices(
            self.structural_span,
            "LOW",
        )
        recent_return, notional, position = self._rolling_24h()
        current = self.bars[-1]
        return AuctionContextSnapshot(
            symbol=self.symbol,
            timeframe_minutes=self.timeframe_minutes,
            observed_time_ns=current.ts_close_ns,
            close=current.close,
            local_state=self.state(self.local_span),
            structural_state=self.state(self.structural_span),
            local_span=self.local_span,
            structural_span=self.structural_span,
            local_last_high=local_last_high,
            local_previous_high=local_previous_high,
            local_last_low=local_last_low,
            local_previous_low=local_previous_low,
            structural_last_high=structural_last_high,
            structural_previous_high=structural_previous_high,
            structural_last_low=structural_last_low,
            structural_previous_low=structural_previous_low,
            return_24h=recent_return,
            notional_volume_24h=notional,
            range_position_24h=position,
            bars_observed=len(self.bars),
            provenance=SOURCE_CONTEXT_RULES + CONTEXT_HYPOTHESES,
        )
