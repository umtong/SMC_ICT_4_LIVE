"""Nearest pre-interaction liquidity objectives across the decision stack.

EasyChart examples select the first opposing high/low or structure in front of
the trade, while the previous implementation searched only the context
(timeframe) structure book.  A valid 60m setup could therefore jump over a
confirmed 15m or 5m obstacle and hold for days toward a distant target.

This module reuses the existing causal pivot/lifecycle implementation at every
trading scale.  It adds no matching, portfolio or account simulation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import StructureZone
from domain import Candle, Side


OBJECTIVE_LADDER_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIRST_OPPOSING_OBJECTIVE_IS_SEARCHED_ACROSS_HIGHER_DECISION_AND_TRIGGER_TIMEFRAMES"
)


class HorizontalObjectiveBook(LifecycleAwareStructureBook):
    """Reuse causal pivots and first-touch lifecycle without drawing diagonals."""

    def _build_trend_line(self, pivot):  # type: ignore[no-untyped-def]
        return None


@dataclass(frozen=True, slots=True)
class ObjectiveSelection:
    zone: StructureZone
    price: float
    owner_timeframe_minutes: int


class CausalObjectiveLadder:
    """One nearest-objective policy shared by all scenario families.

    Channel rotations keep their explicit opposite-edge objective in the
    scenario policy.  Every other family asks this ladder for the first
    pre-existing, still-unspent opposing pivot across context, decision and
    trigger timeframes.  A closer lower-timeframe obstacle is not ignored just
    because the setup originated from a larger structure.
    """

    def __init__(
        self,
        primary: LifecycleAwareStructureBook,
        *,
        symbol: str,
        decision_minutes: int,
        trigger_minutes: int,
        tick_size: float,
    ) -> None:
        if not primary.timeframe_minutes > decision_minutes > trigger_minutes:
            raise ValueError("objective timeframes must descend")
        self.primary = primary
        self.decision = HorizontalObjectiveBook(
            symbol,
            decision_minutes,
            tick_size,
        )
        self.trigger = HorizontalObjectiveBook(
            symbol,
            trigger_minutes,
            tick_size,
        )
        self.books = (
            self.primary,
            self.decision,
            self.trigger,
        )
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def on_decision_bar(self, bar: Candle) -> None:
        self.decision.on_bar(bar)

    def observe_decision_bar(self, bar: Candle) -> None:
        self.decision.observe_price(bar)

    def on_trigger_bar(self, bar: Candle) -> None:
        self.trigger.on_bar(bar)

    def observe_trigger_bar(self, bar: Candle) -> None:
        self.trigger.observe_price(bar)

    @staticmethod
    def _candidate_pivots(
        book: LifecycleAwareStructureBook,
        side: Side,
        *,
        interaction_time_ns: int,
        current_high: float,
        current_low: float,
    ) -> list[Any]:
        wanted = "HIGH" if side is Side.LONG else "LOW"
        return [
            pivot
            for pivot in book.pivots
            if pivot.side == wanted
            and pivot.observed_time_ns < interaction_time_ns
            and not (
                pivot.consumed_time_ns is not None
                and pivot.consumed_time_ns < interaction_time_ns
            )
            and (
                (side is Side.LONG and pivot.price > current_high)
                or (side is Side.SHORT and pivot.price < current_low)
            )
        ]

    def target_for(
        self,
        side: Side,
        *,
        interaction_time_ns: int,
        source_span: int,
        current_high: float,
        current_low: float,
    ) -> tuple[StructureZone, float] | None:
        candidates: list[ObjectiveSelection] = []
        for book in self.books:
            for pivot in self._candidate_pivots(
                book,
                side,
                interaction_time_ns=interaction_time_ns,
                current_high=current_high,
                current_low=current_low,
            ):
                candidates.append(
                    ObjectiveSelection(
                        zone=book._horizontal_snapshot(pivot, interaction_time_ns),
                        price=pivot.price,
                        owner_timeframe_minutes=book.timeframe_minutes,
                    ),
                )
        if not candidates:
            return None
        selected = (
            min(
                candidates,
                key=lambda item: (
                    item.price,
                    -item.owner_timeframe_minutes,
                    -item.zone.source_pivot_span,
                    item.zone.zone_id,
                ),
            )
            if side is Side.LONG
            else max(
                candidates,
                key=lambda item: (
                    item.price,
                    item.owner_timeframe_minutes,
                    item.zone.source_pivot_span,
                    item.zone.zone_id,
                ),
            )
        )
        self._inc("nearest_cross_timeframe_objective_selected")
        if selected.owner_timeframe_minutes < self.primary.timeframe_minutes:
            self._inc("lower_timeframe_objective_preceded_context_objective")
        if selected.zone.source_pivot_span < source_span:
            self._inc("objective_uses_smaller_confirmed_span")
        self._inc(f"objective_selected_{selected.owner_timeframe_minutes}m")
        return selected.zone, selected.price

    def owner_for(self, zone: StructureZone) -> LifecycleAwareStructureBook | None:
        for book in self.books:
            if (
                book.timeframe_minutes == zone.timeframe_minutes
                and book.pivot_for_structure(zone.source_structure_id) is not None
            ):
                return book
        return None

    def target_spent_after(self, zone: StructureZone, interaction_time_ns: int) -> bool:
        owner = self.owner_for(zone)
        if owner is None:
            raise RuntimeError(f"objective owner unavailable: {zone.zone_id}")
        return owner.target_spent_after(zone, interaction_time_ns)

    def diagnostics_snapshot(self) -> dict[str, Any]:
        return {
            "ladder": dict(sorted(self.diagnostics.items())),
            "primary": dict(sorted(self.primary.diagnostics.items())),
            "decision": dict(sorted(self.decision.diagnostics.items())),
            "trigger": dict(sorted(self.trigger.diagnostics.items())),
            "primary_pivots": len(self.primary.pivots),
            "decision_pivots": len(self.decision.pivots),
            "trigger_pivots": len(self.trigger.pivots),
        }
