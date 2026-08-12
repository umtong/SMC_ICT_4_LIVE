"""Causal rejection/acceptance state transitions for EasyChart v2."""
from __future__ import annotations

from typing import Iterable

from boundary_state import BoundaryState
from domain import AcceptanceCandidate, Boundary, Candle, EngineConfig, Family, RejectionCandidate, Side, TradePlan


class EasyChartStateEngine(BoundaryState):
    def _advance_rejections(self, current: Candle, index: int) -> list[TradePlan]:
        plans: list[TradePlan] = []
        remaining: list[RejectionCandidate] = []
        for candidate in self.rejections:
            source = candidate.source
            stop = (
                candidate.excursion - self.config.tick_size
                if candidate.side is Side.LONG
                else candidate.excursion + self.config.tick_size
            )
            invalidated = current.low <= stop if candidate.side is Side.LONG else current.high >= stop
            target_spent = (
                current.high >= candidate.target.level
                if candidate.side is Side.LONG
                else current.low <= candidate.target.level
            )
            if invalidated:
                self._inc("rejection_invalidated_before_entry")
                continue
            if target_spent:
                self._inc("rejection_target_spent_before_entry")
                continue

            if candidate.confirmed_index is None:
                if index > candidate.confirmation_deadline:
                    self._inc("rejection_failed_confirmation")
                    continue
                confirmed = (
                    current.close > max(source.level, candidate.sweep_close)
                    if candidate.side is Side.LONG
                    else current.close < min(source.level, candidate.sweep_close)
                )
                if confirmed and index > candidate.sweep_index:
                    candidate.confirmed_index = index
                    candidate.confirmed_time_ns = current.ts_close_ns
                    self._inc("rejection_confirmed")
                remaining.append(candidate)
                continue

            if index <= candidate.confirmed_index:
                remaining.append(candidate)
                continue
            retested = (
                current.low <= source.level and current.close > source.level
                if candidate.side is Side.LONG
                else current.high >= source.level and current.close < source.level
            )
            if not retested:
                remaining.append(candidate)
                continue

            plan = self._plan(
                family=Family.REJECTION_RETEST_CLOSE,
                side=candidate.side,
                current=current,
                source=source,
                entry=current.close,
                stop=stop,
                target=candidate.target,
                event_suffix=str(candidate.sweep_index),
                interaction_index=candidate.sweep_index,
                confirmation_index=candidate.confirmed_index,
                trigger_extreme=candidate.excursion,
            )
            self._inc("rejection_first_retest_spent")
            if plan is not None:
                plans.append(plan)
        self.rejections = remaining
        return plans

    def _advance_acceptance(self, current: Candle, index: int) -> list[TradePlan]:
        plans: list[TradePlan] = []
        remaining: list[AcceptanceCandidate] = []
        for candidate in self.acceptance:
            source = candidate.source
            stop = (
                candidate.origin.level - self.config.tick_size
                if candidate.side is Side.LONG
                else candidate.origin.level + self.config.tick_size
            )
            invalidated = current.low <= stop if candidate.side is Side.LONG else current.high >= stop
            target_spent = (
                current.high >= candidate.target.level
                if candidate.side is Side.LONG
                else current.low <= candidate.target.level
            )
            if invalidated:
                self._inc("acceptance_invalidated_before_entry")
                continue
            if target_spent:
                self._inc("acceptance_target_spent_before_entry")
                continue

            if candidate.confirmed_index is None:
                if index != candidate.break_index + 1:
                    if index <= candidate.break_index + 1:
                        remaining.append(candidate)
                    else:
                        self._inc("acceptance_failed_next_bar_hold")
                    continue
                held = (
                    current.open > source.level and current.close > source.level
                    if candidate.side is Side.LONG
                    else current.open < source.level and current.close < source.level
                )
                if not held:
                    self._inc("acceptance_failed_next_bar_hold")
                    continue
                retest_spent = (
                    current.low <= source.level
                    if candidate.side is Side.LONG
                    else current.high >= source.level
                )
                if retest_spent:
                    self._inc("acceptance_retest_spent_before_observable")
                    continue
                candidate.confirmed_index = index
                candidate.confirmed_time_ns = current.ts_close_ns
                self._inc("acceptance_confirmed")
                remaining.append(candidate)
                continue

            if index <= candidate.confirmed_index:
                remaining.append(candidate)
                continue
            retested = (
                current.low <= source.level and current.close > source.level
                if candidate.side is Side.LONG
                else current.high >= source.level and current.close < source.level
            )
            if not retested:
                closed_back_inside = (
                    current.close <= source.level
                    if candidate.side is Side.LONG
                    else current.close >= source.level
                )
                if closed_back_inside:
                    self._inc("acceptance_failed_retest")
                else:
                    remaining.append(candidate)
                continue

            plan = self._plan(
                family=Family.ACCEPTANCE_RETEST_CLOSE,
                side=candidate.side,
                current=current,
                source=source,
                entry=current.close,
                stop=stop,
                target=candidate.target,
                event_suffix=str(candidate.break_index),
                interaction_index=candidate.break_index,
                confirmation_index=candidate.confirmed_index,
                trigger_extreme=candidate.break_extreme,
                origin=candidate.origin,
            )
            self._inc("acceptance_first_retest_spent")
            if plan is not None:
                plans.append(plan)
        self.acceptance = remaining
        return plans

    def _interactions(self, current: Candle, previous: Candle, index: int) -> list[TradePlan]:
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
                if old is None:
                    selected[side] = item
                    continue
                if (boundary.span, boundary.prominence_atr, boundary.observed_time_ns) > (
                    old[0].span,
                    old[0].prominence_atr,
                    old[0].observed_time_ns,
                ):
                    self._inc("nested_interaction_collapsed")
                    selected[side] = item
                else:
                    self._inc("nested_interaction_collapsed")
            return list(selected.values())

        if self.config.enable_rejection:
            for source, side, excursion in strongest(rejection_candidates):
                target = self._nearest_target(side, current, source)
                source.consumed = True
                if target is None:
                    self._inc("no_preexisting_opposite_target")
                    continue
                self.rejections.append(
                    RejectionCandidate(
                        source=source,
                        target=target,
                        side=side,
                        sweep_index=index,
                        sweep_time_ns=current.ts_close_ns,
                        sweep_close=current.close,
                        excursion=excursion,
                        confirmation_deadline=index + self.config.rejection_confirmation_bars,
                    ),
                )
                self._inc("rejection_armed")

        if self.config.enable_acceptance:
            for source, side, extreme in strongest(break_candidates):
                target = self._nearest_target(side, current, source)
                origin = self._latest_origin(side, current.ts_close_ns, min_span=source.span)
                source.consumed = True
                if target is None:
                    self._inc("no_preexisting_opposite_target")
                    continue
                if origin is None:
                    self._inc("acceptance_no_causal_origin")
                    continue
                if side is Side.LONG and not origin.level < source.level:
                    self._inc("acceptance_bad_origin")
                    continue
                if side is Side.SHORT and not origin.level > source.level:
                    self._inc("acceptance_bad_origin")
                    continue
                self.acceptance.append(
                    AcceptanceCandidate(
                        source=source,
                        target=target,
                        side=side,
                        break_index=index,
                        break_time_ns=current.ts_close_ns,
                        break_extreme=extreme,
                        origin=origin,
                    ),
                )
                self._inc("acceptance_armed")
        return []

    def on_bar(self, bar: Candle) -> list[TradePlan]:
        self._update_true_range(bar)
        self.bars.append(bar)
        index = len(self.bars) - 1
        self._register_pivots(index)
        plans = self._advance_rejections(bar, index)
        plans.extend(self._advance_acceptance(bar, index))
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
    "RejectionCandidate",
    "Side",
    "TradePlan",
]
