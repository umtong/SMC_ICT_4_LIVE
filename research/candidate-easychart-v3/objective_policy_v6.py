"""Resolve source-supported target ambiguity with a first-obstacle policy.

EasyChart names several valid objectives: a prior swing, an opposing structure,
or the opposite side of a channel.  A discretionary trader naturally sees the
nearer obstacle before the farther one.  Code must not silently jump over that
obstacle merely because the farther target manufactures a better reward/risk
ratio or a larger backtest winner.

This mixin keeps every candidate causal and immutable at setup creation, then
chooses the nearest unspent objective in the trade direction.  It adds no
performance score, angle threshold, holding-time cap, or learned parameter.
"""
from __future__ import annotations

from domain import Candle, Side
from contracts_v5 import ScenarioPath, StructureZone
from scenario_context_v5 import ScenarioContextMixin


TargetSelection = tuple[StructureZone, float, str | None, float | None]


class FirstObstacleScenarioContextMixin(ScenarioContextMixin):
    """Choose the first causal obstacle rather than the most ambitious target."""

    @staticmethod
    def _target_is_ahead(side: Side, price: float, bar: Candle) -> bool:
        return price > bar.high if side is Side.LONG else price < bar.low

    @staticmethod
    def _target_rank(side: Side, item: TargetSelection) -> tuple[float, str]:
        zone, price, _, _ = item
        # The nearest price in the trade direction is the first auction
        # obstacle.  The zone ID is only a deterministic tie-break.
        return (price, zone.zone_id) if side is Side.LONG else (-price, zone.zone_id)

    def _select_target(
        self,
        context: StructureZone,
        side: Side,
        path: ScenarioPath,
        bar: Candle,
    ) -> TargetSelection | None:
        candidates: list[TargetSelection] = []

        horizontal = self.structure.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        if horizontal is not None:
            zone, price = horizontal
            candidates.append((zone, price, None, None))

        if path in {ScenarioPath.REJECTION, ScenarioPath.ROTATION, ScenarioPath.BOUNCE}:
            channel = self._channel_target(context, side, bar.ts_close_ns)
            if channel is not None:
                zone, price, channel_id, midline = channel
                if self._target_is_ahead(side, price, bar):
                    candidates.append((zone, price, channel_id, midline))

        if not candidates:
            return None
        return min(candidates, key=lambda item: self._target_rank(side, item))
