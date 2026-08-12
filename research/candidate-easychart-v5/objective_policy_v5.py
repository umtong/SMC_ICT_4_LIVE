"""Scenario-level target and target-lifecycle policy for the objective ladder."""
from __future__ import annotations

from contracts_v5 import ScenarioPath, ScenarioSetup, StructureZone
from domain import Candle, Side


class ObjectiveLadderScenarioMixin:
    """Override only target choice and target consumption lifecycle."""

    def _select_target(
        self,
        context: StructureZone,
        side: Side,
        path: ScenarioPath,
        bar: Candle,
    ) -> tuple[StructureZone, float, str | None, float | None] | None:
        # A channel rotation or fakeout already has a source-defined opposite
        # edge.  Other paths take the first opposing pivot across the complete
        # 15m/5m/1m stack rather than jumping over closer liquidity.
        if path in {ScenarioPath.REJECTION, ScenarioPath.ROTATION, ScenarioPath.BOUNCE}:
            channel = self._channel_target(context, side, bar.ts_close_ns)
            if channel is not None:
                zone, price, channel_id, mid = channel
                return zone, price, channel_id, mid
        target = self.objectives.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        if target is None:
            return None
        zone, price = target
        return zone, price, None, None

    def _target_is_spent(self, setup: ScenarioSetup, bar: Candle) -> bool:
        dynamic = self._channel_target_at(setup, bar.ts_close_ns)
        if dynamic is not None:
            _, target_price = dynamic
            return (
                bar.high >= target_price
                if setup.side is Side.LONG
                else bar.low <= target_price
            ) and bar.ts_close_ns > setup.interaction_time_ns
        if setup.target_zone is None or setup.target_price is None:
            return True
        touched = (
            bar.high >= setup.target_price
            if setup.side is Side.LONG
            else bar.low <= setup.target_price
        )
        if touched and bar.ts_close_ns > setup.interaction_time_ns:
            return True
        return self.objectives.target_spent_after(
            setup.target_zone,
            setup.interaction_time_ns,
        )
