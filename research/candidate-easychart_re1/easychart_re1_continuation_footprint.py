"""Local continuation whose event footprint is OB first, FVG when OB is absent.

A causal 5m initiative may leave either of the two source-defined institutional
footprints.  The existing continuation engine recognizes only an engulfing
order block, so a valid displacement FVG without an engulfing predecessor is
silently discarded.  This module keeps one owner per impulse:

* an event-local high-quality order block keeps first ownership;
* only when no such OB exists, an event-local high-quality same-side FVG owns
  the first return;
* the same aligned 15m/5m break, complete aggressor flow, first reacted return,
  structural invalidation, unspent target, >=1R geometry, costs, 3% NAV risk and
  one global account slot remain unchanged.

No OB/FVG AND gate and no duplicate plans are introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Side
from easychart_re1_continuation_first_return import (
    EasyChartRE1ContinuationFirstReturnBundle,
    FirstReturnContinuationEngine,
)
from easychart_zones import PriceZone, ZoneKind, ZoneSide


CONTINUATION_FOOTPRINT_OWNERSHIP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_EVENT_LOCAL_HIGH_QUALITY_OB_OWNS_THE_"
    "CONTINUATION_RETURN_AND_A_HIGH_QUALITY_SAME_SIDE_FVG_OWNS_ONLY_WHEN_"
    "NO_EVENT_LOCAL_OB_EXISTS"
)
if CONTINUATION_FOOTPRINT_OWNERSHIP_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CONTINUATION_FOOTPRINT_OWNERSHIP_RULE,)


class OBOrFVGFallbackContinuationEngine(FirstReturnContinuationEngine):
    """Use one event-local visual footprint without multiplying the episode."""

    def _select_zone(
        self,
        created: list[PriceZone],
        side: Side,
        time_ns: int,
    ) -> PriceZone | None:
        order_block = super()._select_zone(created, side, time_ns)
        if order_block is not None:
            self._inc("continuation_source_order_block_owned")
            return order_block

        wanted = ZoneSide.SUPPORT if side is Side.LONG else ZoneSide.RESISTANCE
        gaps = [
            zone
            for zone in created
            if zone.kind is ZoneKind.FVG
            and zone.side is wanted
            and zone.high_quality_by_size
            and zone.observed_time_ns == time_ns
        ]
        if not gaps:
            return None
        chosen = max(
            gaps,
            key=lambda zone: (
                zone.strength_ratio,
                zone.upper if side is Side.LONG else -zone.lower,
                zone.zone_id,
            ),
        )
        self._inc("continuation_source_fvg_fallback_owned")
        self._record(
            "local_continuation_fvg_fallback_owned",
            time_ns,
            side=side.name,
            zone_id=chosen.zone_id,
            lower=chosen.lower,
            upper=chosen.upper,
            invalidation=chosen.invalidation,
            strength_ratio=chosen.strength_ratio,
            rule_provenance=CONTINUATION_FOOTPRINT_OWNERSHIP_RULE,
        )
        return chosen


class EasyChartRE1ContinuationFootprintBundle(
    EasyChartRE1ContinuationFirstReturnBundle
):
    """Current displacement core plus one responsible OB-or-FVG continuation."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.continuation = OBOrFVGFallbackContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["continuation_footprint_ownership"] = {
            "priority": ("EVENT_LOCAL_ORDER_BLOCK", "EVENT_LOCAL_FVG_FALLBACK"),
            "rule_provenance": CONTINUATION_FOOTPRINT_OWNERSHIP_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ContinuationFootprintBundle
