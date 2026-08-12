"""Causal multi-scale boundary construction and plan geometry."""
from __future__ import annotations

from collections import deque

from domain import AcceptanceCandidate, Boundary, Candle, EngineConfig, Family, RejectionCandidate, Side, TradePlan


class BoundaryState:
    """Causal multi-scale boundary construction and same-leg geometry."""

    def __init__(self, symbol: str, config: EngineConfig) -> None:
        if not config.pivot_spans or min(config.pivot_spans) < 1:
            raise ValueError("pivot_spans must contain positive integers")
        self.symbol = symbol
        self.config = config
        self.bars: list[Candle] = []
        self.true_ranges: deque[float] = deque(maxlen=max(config.atr_period, 2))
        self.boundaries: list[Boundary] = []
        self.rejections: list[RejectionCandidate] = []
        self.acceptance: list[AcceptanceCandidate] = []
        self.used_events: set[str] = set()
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _atr(self) -> float | None:
        if len(self.true_ranges) < self.config.atr_period:
            return None
        value = sum(self.true_ranges) / len(self.true_ranges)
        return value if value > 0.0 else None

    def _update_true_range(self, bar: Candle) -> None:
        if not self.bars:
            self.true_ranges.append(bar.high - bar.low)
            return
        previous = self.bars[-1]
        self.true_ranges.append(max(bar.high - bar.low, abs(bar.high - previous.close), abs(bar.low - previous.close)))

    def _boundary_id(self, side: str, center: int, span: int, level: float) -> str:
        return f"{self.symbol}:{side}:{center}:{span}:{level:.12g}"

    def _register_pivots(self, observed_index: int) -> None:
        atr = self._atr()
        if atr is None:
            return
        for span in self.config.pivot_spans:
            center = observed_index - span
            if center < span:
                continue
            window = self.bars[center - span : center + span + 1]
            if len(window) != 2 * span + 1:
                continue
            pivot = self.bars[center]
            highs = [bar.high for bar in window]
            lows = [bar.low for bar in window]
            unique_high = pivot.high == max(highs) and highs.count(pivot.high) == 1
            unique_low = pivot.low == min(lows) and lows.count(pivot.low) == 1
            if unique_high:
                prominence = min(
                    pivot.high - min(bar.low for bar in window[:span]),
                    pivot.high - min(bar.low for bar in window[span + 1 :]),
                ) / atr
                self._maybe_add_boundary("HIGH", center, span, pivot.high, prominence, observed_index)
            if unique_low:
                prominence = min(
                    max(bar.high for bar in window[:span]) - pivot.low,
                    max(bar.high for bar in window[span + 1 :]) - pivot.low,
                ) / atr
                self._maybe_add_boundary("LOW", center, span, pivot.low, prominence, observed_index)

    def _maybe_add_boundary(
        self,
        side: str,
        center: int,
        span: int,
        level: float,
        prominence: float,
        observed_index: int,
    ) -> None:
        if prominence + 1e-12 < self.config.min_prominence_atr:
            self._inc("pivot_below_prominence")
            return
        boundary_id = self._boundary_id(side, center, span, level)
        if any(boundary.boundary_id == boundary_id for boundary in self.boundaries):
            return
        tolerance = self.config.tick_size * 2.0
        for boundary in self.boundaries:
            if boundary.consumed or boundary.side != side:
                continue
            if abs(boundary.level - level) <= tolerance:
                if (span, prominence) > (boundary.span, boundary.prominence_atr):
                    boundary.consumed = True
                else:
                    self._inc("nested_pivot_collapsed")
                    return
        pivot = self.bars[center]
        observed = self.bars[observed_index]
        self.boundaries.append(
            Boundary(
                boundary_id=boundary_id,
                side=side,
                level=level,
                event_time_ns=pivot.ts_close_ns,
                observed_time_ns=observed.ts_close_ns,
                span=span,
                prominence_atr=prominence,
            ),
        )
        self._inc(f"boundary_{side.lower()}")

    def _active(self, side: str | None = None, min_span: int = 1) -> list[Boundary]:
        return [
            boundary
            for boundary in self.boundaries
            if not boundary.consumed
            and boundary.span >= min_span
            and (side is None or boundary.side == side)
        ]

    def _nearest_target(self, side: Side, current: Candle, source: Boundary) -> Boundary | None:
        # Entry, invalidation and objective must describe the same auction scale.
        # Smaller nested pivots are geometry, not a license to terminate a larger leg.
        if side is Side.LONG:
            candidates = [
                boundary
                for boundary in self._active("HIGH", source.span)
                if boundary.boundary_id != source.boundary_id
                and boundary.observed_time_ns < current.ts_close_ns
                and boundary.level > current.high
            ]
            return min(candidates, key=lambda item: item.level, default=None)
        candidates = [
            boundary
            for boundary in self._active("LOW", source.span)
            if boundary.boundary_id != source.boundary_id
            and boundary.observed_time_ns < current.ts_close_ns
            and boundary.level < current.low
        ]
        return max(candidates, key=lambda item: item.level, default=None)

    def _latest_origin(self, side: Side, before_ns: int, min_span: int = 1) -> Boundary | None:
        opposite = "LOW" if side is Side.LONG else "HIGH"
        candidates = [
            boundary
            for boundary in self._active(opposite, min_span)
            if boundary.observed_time_ns < before_ns
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.observed_time_ns, item.span, item.prominence_atr))

    def _plan(
        self,
        *,
        family: Family,
        side: Side,
        current: Candle,
        source: Boundary,
        entry: float,
        stop: float,
        target: Boundary | None,
        event_suffix: str,
        interaction_index: int,
        confirmation_index: int,
        trigger_extreme: float,
        origin: Boundary | None = None,
    ) -> TradePlan | None:
        if target is None:
            self._inc("no_preexisting_opposite_target")
            return None
        if side is Side.LONG and not stop < entry < target.level:
            self._inc("invalid_long_geometry")
            return None
        if side is Side.SHORT and not target.level < entry < stop:
            self._inc("invalid_short_geometry")
            return None
        risk = abs(entry - stop)
        if risk <= 0.0:
            return None
        rr = abs(target.level - entry) / risk
        if rr + 1e-12 < self.config.min_gross_rr:
            self._inc("gross_rr_below_minimum")
            return None
        causal_event_id = f"{family.value}:{source.boundary_id}:{event_suffix}"
        if causal_event_id in self.used_events:
            return None
        if not 0 <= interaction_index < len(self.bars):
            raise RuntimeError(f"interaction index out of range: {interaction_index}")
        if not 0 <= confirmation_index < len(self.bars):
            raise RuntimeError(f"confirmation index out of range: {confirmation_index}")
        self.sequence += 1
        self.used_events.add(causal_event_id)
        self._inc(f"plan_{family.value.lower()}")
        return TradePlan(
            plan_id=f"ecv2-{self.symbol}-{self.sequence:08d}",
            causal_event_id=causal_event_id,
            symbol=self.symbol,
            family=family,
            side=side,
            observed_time_ns=current.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target.level,
            gross_rr=rr,
            source_boundary_id=source.boundary_id,
            target_boundary_id=target.boundary_id,
            source_level=source.level,
            source_event_time_ns=source.event_time_ns,
            source_observed_time_ns=source.observed_time_ns,
            source_span=source.span,
            source_prominence_atr=source.prominence_atr,
            target_event_time_ns=target.event_time_ns,
            target_observed_time_ns=target.observed_time_ns,
            target_span=target.span,
            target_prominence_atr=target.prominence_atr,
            interaction_index=interaction_index,
            confirmation_index=confirmation_index,
            interaction_time_ns=self.bars[interaction_index].ts_close_ns,
            confirmation_time_ns=self.bars[confirmation_index].ts_close_ns,
            trigger_extreme=trigger_extreme,
            origin_boundary_id=None if origin is None else origin.boundary_id,
            origin_level=None if origin is None else origin.level,
        )
