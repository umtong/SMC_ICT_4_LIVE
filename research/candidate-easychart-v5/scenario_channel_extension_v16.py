"""Channel-breakout objective correction for the canonical EasyChart policy.

The channel material gives a different objective for an accepted channel break
than for an in-channel rotation: the projected expansion channel. The prior
machine policy instead searched arbitrary old horizontal pivots, occasionally
creating 30R-50R targets whose account result depended on one exceptional move.

For an accepted channel breakout, this module compares the first equal-width
channel extension with the existing pre-observed opposing structure and selects
whichever price would be encountered first. Rejections and rotations retain the
opposite channel edge; non-channel setups retain their existing objective
policy. No risk, stop, entry, exit-management, time, daily, or trade-count rule
changes.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

import contracts_v5 as _contracts
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
    "ACCEPTED_CHANNEL_BREAK_TARGETS_FIRST_OF_EQUAL_WIDTH_EXTENSION_OR_OPPOSING_STRUCTURE"
)
if CHANNEL_EXTENSION_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CHANNEL_EXTENSION_RULE,)


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

    @staticmethod
    def _extension_is_first(
        side: Side,
        extension_price: float,
        existing_price: float,
    ) -> bool:
        return (
            extension_price < existing_price
            if side is Side.LONG
            else extension_price > existing_price
        )

    def _select_target(
        self,
        context: StructureZone,
        side: Side,
        path: ScenarioPath,
        bar: Any,
    ) -> tuple[StructureZone, float, str | None, float | None] | None:
        existing = super()._select_target(context, side, path, bar)
        if path is not ScenarioPath.ACCEPTANCE or context.family is not StructureFamily.CHANNEL:
            return existing

        channel = self.structure.channel_for_boundary(context.source_structure_id)
        if channel is None:
            return existing
        extension_zone, extension_price = self._channel_extension_at(
            channel,
            side,
            bar.ts_close_ns,
        )
        if existing is not None:
            _, existing_price, _, _ = existing
            if not self._extension_is_first(side, extension_price, existing_price):
                return existing
        return (
            extension_zone,
            extension_price,
            channel.channel_id,
            channel.mid_at(bar.ts_close_ns),
        )

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
    """Micro policy with fixed execution and first channel-break objectives."""

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
            "name": "FIRST_OF_EQUAL_WIDTH_EXTENSION_OR_OPPOSING_STRUCTURE",
            "rule_provenance": CHANNEL_EXTENSION_RULE,
        }
        return output
