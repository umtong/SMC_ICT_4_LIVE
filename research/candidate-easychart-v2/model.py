"""Causal rejection/acceptance state transitions for EasyChart v2."""
from __future__ import annotations

from typing import Iterable

from boundary_state import BoundaryState
from domain import AcceptanceCandidate, Boundary, Candle, EngineConfig, Family, Side, TradePlan


class EasyChartStateEngine(BoundaryState):
    def _confirm_acceptance(self, current: Candle, index: int) -> list[TradePlan]:
        plans: list[TradePlan] = []
        remaining: list[AcceptanceCandidate] = []
        for candidate in self.acceptance:
            if index != candidate.break_index + 1:
                if index <= candidate.break_index + 1:
                    remaining.append(candidate)
                continue
            source = candidate.source
            if candidate.side is Side.LONG:
                held = current.open > source.level and current.close > source.level
                stop = candidate.origin - self.config.tick_size
            else:
                held = current.open < source.level and current.close < source.level
                stop = candidate.origin + self.config.tick_size
            if not held:
                self._inc("acceptance_failed_hold")
                continue
            target = self._nearest_target(candidate.side, current, source)
            plan = self._plan(
                family=Family.ACCEPTANCE_HOLD_CLOSE,
                side=candidate.side,
                current=current,
                source=source,
                entry=current.close,
                stop=stop,
                target=target,
                event_suffix=str(candidate.break_index),
            )
            if plan is not None:
                plans.append(plan)
        self.acceptance = remaining
        return plans

    def _interactions(self, current: Candle, previous: Candle, index: int) -> list[TradePlan]:
        plans: list[TradePlan] = []
        rejection_candidates: list[tuple[Boundary, Side, float]] = []
        break_candidates: list[tuple[Boundary, Side, float]] = []
        for boundary in self._active():
            if boundary.observed_time_ns >= current.ts_close_ns:
                continue
            if boundary.side == "LOW":
                if current.low < boundary.level and current.close > boundary.level:
                    rejection_candidates.append((boundary, Side.LONG, current.low))
                elif previous.close >= boundary.level and current.close < boundary.level:
                    break_candidates.append((boundary, Side.SHORT, current.low))
            else:
                if current.high > boundary.level and current.close < boundary.level:
                    rejection_candidates.append((boundary, Side.SHORT, current.high))
                elif previous.close <= boundary.level and current.close > boundary.level:
                    break_candidates.append((boundary, Side.LONG, current.high))

        def strongest(items: Iterable[tuple[Boundary, Side, float]]) -> list[tuple[Boundary, Side, float]]:
            selected: dict[Side, tuple[Boundary, Side, float]] = {}
            for item in items:
                boundary, side, _ = item
                old = selected.get(side)
                if old is None or (boundary.span, boundary.prominence_atr, boundary.observed_time_ns) > (
                    old[0].span,
                    old[0].prominence_atr,
                    old[0].observed_time_ns,
                ):
                    selected[side] = item
                else:
                    self._inc("nested_interaction_collapsed")
            return list(selected.values())

        if self.config.enable_rejection:
            for source, side, excursion in strongest(rejection_candidates):
                target = self._nearest_target(side, current, source)
                stop = excursion - self.config.tick_size if side is Side.LONG else excursion + self.config.tick_size
                plan = self._plan(
                    family=Family.REJECTION_CLOSE,
                    side=side,
                    current=current,
                    source=source,
                    entry=current.close,
                    stop=stop,
                    target=target,
                    event_suffix=str(index),
                )
                source.consumed = True
                if plan is not None:
                    plans.append(plan)

        if self.config.enable_acceptance:
            for source, side, extreme in strongest(break_candidates):
                origin = self._latest_origin(side, current.ts_close_ns)
                source.consumed = True
                if origin is None:
                    self._inc("acceptance_no_causal_origin")
                    continue
                if side is Side.LONG and not origin < source.level:
                    self._inc("acceptance_bad_origin")
                    continue
                if side is Side.SHORT and not origin > source.level:
                    self._inc("acceptance_bad_origin")
                    continue
                self.acceptance.append(
                    AcceptanceCandidate(
                        source=source,
                        side=side,
                        break_index=index,
                        break_extreme=extreme,
                        origin=origin,
                    ),
                )
                self._inc("acceptance_armed")
        return plans

    def on_bar(self, bar: Candle) -> list[TradePlan]:
        self._update_true_range(bar)
        self.bars.append(bar)
        index = len(self.bars) - 1
        self._register_pivots(index)
        plans = self._confirm_acceptance(bar, index)
        if index >= 1:
            plans.extend(self._interactions(bar, self.bars[index - 1], index))
        return sorted(
            plans,
            key=lambda plan: (
                plan.observed_time_ns,
                -plan.source_span,
                -plan.source_prominence_atr,
                plan.symbol,
                plan.plan_id,
            ),
        )


__all__ = [
    "AcceptanceCandidate",
    "Boundary",
    "Candle",
    "EasyChartStateEngine",
    "EngineConfig",
    "Family",
    "Side",
    "TradePlan",
]
