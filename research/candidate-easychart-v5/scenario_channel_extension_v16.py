"""Channel-breakout objective correction for the canonical EasyChart policy.

The channel material gives a different objective for an accepted channel break
than for an in-channel rotation: the projected expansion channel.  The prior
machine policy instead searched arbitrary old horizontal pivots, occasionally
creating 30R-50R targets whose account result depended on one exceptional move.

For an accepted channel breakout, this module uses one current channel width
beyond the broken edge as the single pre-entry full-position target.  That is
the mechanically observable end of the first equal-width channel expansion.
Rejections and rotations retain the opposite channel edge; non-channel setups
retain their existing objective policy.  No risk, stop, entry, exit-management,
time, daily, or trade-count rule changes.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from contracts_v5 import ScenarioPath, ScenarioSetup, StructureFamily, StructureZone
from domain import Side
from easychart_zones import ZoneSide
from scenario_close_detached_v14 import (
    CloseDetachedRetestScenarioEngine,
    MicroCloseDetachedRetestBundleV14,
)


class ChannelObjectiveKind(str, Enum):
    CHANNEL_EXTENSION_TARGET = "CHANNEL_EXTENSION_TARGET"


CHANNEL_EXTENSION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ACCEPTED_CHANNEL_BREAK_TARGETS_FIRST_EQUAL_WIDTH_CHANNEL_EXTENSION"
)


class ChannelExtensionTargetScenarioEngine(CloseDetachedRetestScenarioEngine):
    """Close-detached engine with a channel-specific accepted-break objective."""

    def _channel_extension_at(
        self,
        channel: Any,
        side: Side,
        time_ns: int,
    ) -> tuple[StructureZone, float]:
        lower_edge = channel.lower_at(time_ns)
        upper_edge = channel.upper_at(time_ns)
        width = upper_edge - lower_edge
        if width <= self.tick_size:
            raise RuntimeError("channel width is not positive")

        if side is Side.LONG:
            price = upper_edge + width
            lower = price
            upper = price + self.tick_size
            zone_side = ZoneSide.RESISTANCE
            invalidation = upper + self.tick_size
        else:
            price = lower_edge - width
            lower = price - self.tick_size
            upper = price
            zone_side = ZoneSide.SUPPORT
            invalidation = lower - self.tick_size

        source_id = f"{channel.channel_id}:FIRST_EXTENSION:{side.name}"
        zone = StructureZone(
            zone_id=f"{source_id}:SNAP:{time_ns}",
            kind=ChannelObjectiveKind.CHANNEL_EXTENSION_TARGET,
            family=StructureFamily.CHANNEL,
            side=zone_side,
            timeframe_minutes=channel.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=price,
            formed_index=0,
            formed_time_ns=channel.second_time_ns,
            observed_time_ns=channel.observed_time_ns,
            formation_indices=(),
            strength_ratio=channel.strength_ratio,
            source_structure_id=source_id,
            source_pivot_span=channel.pivot_span,
        )
        return zone, price

    def _select_target(
        self,
        context: StructureZone,
        side: Side,
        path: ScenarioPath,
        bar: Any,
    ) -> tuple[StructureZone, float, str | None, float | None] | None:
        if path is ScenarioPath.ACCEPTANCE and context.family is StructureFamily.CHANNEL:
            channel = self.structure.channel_for_boundary(context.source_structure_id)
            if channel is not None:
                zone, price = self._channel_extension_at(channel, side, bar.ts_close_ns)
                return zone, price, channel.channel_id, channel.mid_at(bar.ts_close_ns)
        return super()._select_target(context, side, path, bar)

    def _channel_target_at(
        self,
        setup: ScenarioSetup,
        time_ns: int,
    ) -> tuple[StructureZone, float] | None:
        if setup.path is ScenarioPath.ACCEPTANCE and setup.channel_id is not None:
            channel = self.structure.channel_by_id(setup.channel_id)
            if channel is None:
                return None
            return self._channel_extension_at(channel, setup.side, time_ns)
        return super()._channel_target_at(setup, time_ns)


class MicroChannelExtensionBundleV16(MicroCloseDetachedRetestBundleV14):
    """Micro policy with fixed execution and first-width channel objectives."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = ChannelExtensionTargetScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["channel_acceptance_target_policy"] = {
            "name": "FIRST_EQUAL_WIDTH_CHANNEL_EXTENSION",
            "rule_provenance": CHANNEL_EXTENSION_RULE,
        }
        return output
