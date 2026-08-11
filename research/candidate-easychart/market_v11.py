"""Whole-impulse value, broken-trendline support and session-raid confluence.

This module encodes the three *different roles* explicitly described in the
EasyChart trade review rather than stacking interchangeable confirmations:

1. a complete directional impulse defines value (the 0.618 retracement),
2. a previously broken meaningful wick trendline defines directional S/R,
3. a session liquidity raid supplies the executable state transition.

Meaningful swings are causal directional-change pivots.  A down-trendline is
known from two descending HIGH pivots and becomes long support only after a
body close above it; the mirror applies to short trades.  The terminal pivot of
the ensuing impulse must then be confirmed before its 0.618 and objective are
known.  No future pivot is backdated to its visual center.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from domain_v3 import Candle, Side
from market_v4 import StructuralPivot


@dataclass(frozen=True, slots=True)
class WickTrendline:
    side: Side
    first: StructuralPivot
    second: StructuralPivot
    slope_per_ns: float

    def __post_init__(self) -> None:
        if self.second.event_time_ns <= self.first.event_time_ns:
            raise ValueError("trendline pivots must be time ordered")
        if not math.isfinite(self.slope_per_ns):
            raise ValueError("trendline slope must be finite")
        if self.side is Side.LONG:
            if self.first.side != "HIGH" or self.second.side != "HIGH":
                raise ValueError("long support conversion begins with two HIGH pivots")
            if not self.second.level < self.first.level:
                raise ValueError("long conversion requires a descending high trendline")
        else:
            if self.first.side != "LOW" or self.second.side != "LOW":
                raise ValueError("short resistance conversion begins with two LOW pivots")
            if not self.second.level > self.first.level:
                raise ValueError("short conversion requires an ascending low trendline")

    def price(self, time_ns: int) -> float:
        return self.second.level + self.slope_per_ns * (time_ns - self.second.event_time_ns)


@dataclass(slots=True)
class PendingBrokenTrendline:
    line: WickTrendline
    break_time_ns: int
    break_observed_time_ns: int
    origin: StructuralPivot


@dataclass(frozen=True, slots=True)
class ImpulseConfluenceContext:
    context_id: str
    side: Side
    observed_time_ns: int
    break_time_ns: int
    origin: StructuralPivot
    terminal: StructuralPivot
    line: WickTrendline

    def __post_init__(self) -> None:
        if self.observed_time_ns < self.terminal.observed_time_ns:
            raise ValueError("context cannot precede terminal pivot confirmation")
        if self.side is Side.LONG and not self.terminal.level > self.origin.level:
            raise ValueError("long impulse terminal must exceed origin")
        if self.side is Side.SHORT and not self.terminal.level < self.origin.level:
            raise ValueError("short impulse terminal must be below origin")

    @property
    def objective(self) -> float:
        return self.terminal.level

    @property
    def fib_0618(self) -> float:
        distance = abs(self.terminal.level - self.origin.level)
        if self.side is Side.LONG:
            return self.terminal.level - 0.618 * distance
        return self.terminal.level + 0.618 * distance

    def trendline_price(self, time_ns: int) -> float:
        return self.line.price(time_ns)


class TrendlineImpulseContextEngine:
    """Online trendline-break and complete-impulse context detector."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.highs: list[StructuralPivot] = []
        self.lows: list[StructuralPivot] = []
        self.pending: dict[Side, PendingBrokenTrendline] = {}
        self.contexts: list[ImpulseConfluenceContext] = []
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}

    def _count(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _new_id(self, side: Side) -> str:
        self.sequence += 1
        return f"impulse-{self.symbol}-{side.name}-{self.sequence:08d}"

    @staticmethod
    def _line(side: Side, first: StructuralPivot, second: StructuralPivot) -> WickTrendline:
        slope = (second.level - first.level) / (second.event_time_ns - first.event_time_ns)
        return WickTrendline(side=side, first=first, second=second, slope_per_ns=slope)

    def _latest_origin(self, side: Side, before_ns: int) -> StructuralPivot | None:
        collection = self.lows if side is Side.LONG else self.highs
        eligible = [pivot for pivot in collection if pivot.event_time_ns < before_ns]
        return eligible[-1] if eligible else None

    def on_close(self, candle: Candle) -> None:
        # Breaks use only pivots observed before this close.  The body close,
        # not a wick poke, changes the role of the trendline.
        if len(self.highs) >= 2:
            first, second = self.highs[-2], self.highs[-1]
            if second.level < first.level:
                line = self._line(Side.LONG, first, second)
                if (
                    candle.ts_open_ns > second.observed_time_ns
                    and candle.close > line.price(candle.ts_close_ns)
                ):
                    origin = self._latest_origin(Side.LONG, candle.ts_open_ns)
                    if origin is not None and origin.level < candle.close:
                        current = self.pending.get(Side.LONG)
                        if current is None or current.line.second.event_time_ns != second.event_time_ns:
                            self.pending[Side.LONG] = PendingBrokenTrendline(
                                line=line,
                                break_time_ns=candle.ts_close_ns,
                                break_observed_time_ns=candle.ts_close_ns,
                                origin=origin,
                            )
                            self._count("descending_trendline_broken")
        if len(self.lows) >= 2:
            first, second = self.lows[-2], self.lows[-1]
            if second.level > first.level:
                line = self._line(Side.SHORT, first, second)
                if (
                    candle.ts_open_ns > second.observed_time_ns
                    and candle.close < line.price(candle.ts_close_ns)
                ):
                    origin = self._latest_origin(Side.SHORT, candle.ts_open_ns)
                    if origin is not None and origin.level > candle.close:
                        current = self.pending.get(Side.SHORT)
                        if current is None or current.line.second.event_time_ns != second.event_time_ns:
                            self.pending[Side.SHORT] = PendingBrokenTrendline(
                                line=line,
                                break_time_ns=candle.ts_close_ns,
                                break_observed_time_ns=candle.ts_close_ns,
                                origin=origin,
                            )
                            self._count("ascending_trendline_broken")

    def on_pivot(self, pivot: StructuralPivot) -> None:
        if pivot.side == "HIGH":
            self.highs.append(pivot)
            self.highs = self.highs[-12:]
            pending = self.pending.get(Side.LONG)
            if (
                pending is not None
                and pivot.event_time_ns > pending.break_time_ns
                and pivot.level > pending.origin.level
            ):
                context = ImpulseConfluenceContext(
                    context_id=self._new_id(Side.LONG),
                    side=Side.LONG,
                    observed_time_ns=pivot.observed_time_ns,
                    break_time_ns=pending.break_time_ns,
                    origin=pending.origin,
                    terminal=pivot,
                    line=pending.line,
                )
                self.contexts.append(context)
                self.pending.pop(Side.LONG, None)
                self._count("long_impulse_completed")
        else:
            self.lows.append(pivot)
            self.lows = self.lows[-12:]
            pending = self.pending.get(Side.SHORT)
            if (
                pending is not None
                and pivot.event_time_ns > pending.break_time_ns
                and pivot.level < pending.origin.level
            ):
                context = ImpulseConfluenceContext(
                    context_id=self._new_id(Side.SHORT),
                    side=Side.SHORT,
                    observed_time_ns=pivot.observed_time_ns,
                    break_time_ns=pending.break_time_ns,
                    origin=pending.origin,
                    terminal=pivot,
                    line=pending.line,
                )
                self.contexts.append(context)
                self.pending.pop(Side.SHORT, None)
                self._count("short_impulse_completed")
        self.contexts = self.contexts[-24:]

    def latest_context(self, side: Side, observed_time_ns: int) -> ImpulseConfluenceContext | None:
        eligible = [
            context
            for context in self.contexts
            if context.side is side and context.observed_time_ns < observed_time_ns
        ]
        return eligible[-1] if eligible else None


@dataclass(frozen=True, slots=True)
class ConfluenceEvaluation:
    accepted: bool
    reason: str
    entry: float | None = None
    target: float | None = None
    fib_price: float | None = None
    trendline_price: float | None = None


def evaluate_session_impulse_confluence(
    *,
    side: Side,
    context: ImpulseConfluenceContext,
    observed_time_ns: int,
    reclaim_close: float,
    sweep_extreme: float,
    session_boundary: float,
    require_fib: bool = True,
    require_trendline: bool = True,
) -> ConfluenceEvaluation:
    """Map three source roles into one executable geometry.

    The sweep must trade through each required support/resistance and the
    reclaim close must finish back through it.  This avoids inventing a numeric
    proximity tolerance: interaction itself proves that the level participated.
    """
    fib = context.fib_0618
    line = context.trendline_price(observed_time_ns)
    if side is Side.LONG:
        if not context.origin.level < reclaim_close < context.objective:
            return ConfluenceEvaluation(False, "LONG_CONTEXT_ALREADY_INVALID")
        if require_fib and not (sweep_extreme <= fib <= reclaim_close):
            return ConfluenceEvaluation(False, "FIB_NOT_SWEPT_AND_RECLAIMED", fib_price=fib, trendline_price=line)
        if require_trendline and not (sweep_extreme <= line <= reclaim_close):
            return ConfluenceEvaluation(False, "TRENDLINE_NOT_SWEPT_AND_RECLAIMED", fib_price=fib, trendline_price=line)
        supports = [session_boundary]
        if require_fib:
            supports.append(fib)
        if require_trendline:
            supports.append(line)
        entry = max(supports)
        if not sweep_extreme < entry <= reclaim_close:
            return ConfluenceEvaluation(False, "LONG_ENTRY_NOT_BELOW_RECLAIM", fib_price=fib, trendline_price=line)
    else:
        if not context.objective < reclaim_close < context.origin.level:
            return ConfluenceEvaluation(False, "SHORT_CONTEXT_ALREADY_INVALID")
        if require_fib and not (reclaim_close <= fib <= sweep_extreme):
            return ConfluenceEvaluation(False, "FIB_NOT_SWEPT_AND_RECLAIMED", fib_price=fib, trendline_price=line)
        if require_trendline and not (reclaim_close <= line <= sweep_extreme):
            return ConfluenceEvaluation(False, "TRENDLINE_NOT_SWEPT_AND_RECLAIMED", fib_price=fib, trendline_price=line)
        resistances = [session_boundary]
        if require_fib:
            resistances.append(fib)
        if require_trendline:
            resistances.append(line)
        entry = min(resistances)
        if not reclaim_close <= entry < sweep_extreme:
            return ConfluenceEvaluation(False, "SHORT_ENTRY_NOT_ABOVE_RECLAIM", fib_price=fib, trendline_price=line)
    return ConfluenceEvaluation(
        accepted=True,
        reason="ACCEPTED",
        entry=entry,
        target=context.objective,
        fib_price=fib,
        trendline_price=line,
    )


__all__ = [
    "ConfluenceEvaluation",
    "ImpulseConfluenceContext",
    "PendingBrokenTrendline",
    "TrendlineImpulseContextEngine",
    "WickTrendline",
    "evaluate_session_impulse_confluence",
]
