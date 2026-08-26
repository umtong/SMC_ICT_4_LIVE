"""Source-consistent first-touch lifecycle for EasyChart RE1 HTF footprints.

RE1's first top-down router correctly made the 60-minute chart part of the
trade decision, but its countertrend exception treated every non-invalidated
60-minute OB/FVG as a live reversal area.  The supplied FVG material explicitly
says that old FVGs lose their function and are normally used only through the
retracement of the creation wave.  The live trading cases likewise use an
HTF/decision-area footprint during the current interaction, not an arbitrary
historical zone which happened to remain unbroken.

This module changes only that lifecycle translation.  A 60-minute OB/FVG can
justify a plan against the current 60-minute structure side when it is either:

* still completely untouched at decision time; or
* first touched by the most recently completed 60-minute bar, so the lower-
  timeframe reclaim/retest still belongs to that same causal interaction.

There is no bar-count or hour-count expiry parameter.  A footprint stops being
fresh because price has already interacted with it, not because a fitted timer
expired.  Continuation routing, local EasyChart scenarios, entry, stop, target,
risk, costs and NautilusTrader execution remain unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1 import ContextEvidence, EasyChartRE1Bundle


FRESH_HTF_FOOTPRINT_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "COUNTERTREND_HTF_OB_FVG_MUST_BE_UNTOUCHED_OR_IN_ITS_FIRST_COMPLETED_TOUCH_EPISODE"
)
if FRESH_HTF_FOOTPRINT_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (FRESH_HTF_FOOTPRINT_RULE,)


class EasyChartRE1FreshBundle(EasyChartRE1Bundle):
    """RE1 router with first-touch 60-minute OB/FVG semantics."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._recent_macro_footprint_touches: list[Any] = []

    def _update_macro_context(self, bar: Candle) -> None:
        super()._update_macro_context(bar)

        # EasyChartZoneDetector records first-touch on the completed HTF bar.
        # Preserve only that just-completed interaction until the next HTF
        # close, allowing the lower-timeframe confirmation to finish without
        # making the footprint tradable on later unrelated revisits.
        self._recent_macro_footprint_touches = [
            zone
            for zone in self.macro_footprints.active_zones()
            if zone.first_touch_time_ns == bar.ts_close_ns
        ]
        if self._recent_macro_footprint_touches:
            self._router_inc("htf_footprint_first_touch_bars")
            self._router_inc(
                "htf_footprints_first_touched",
            )
            self._bundle_trace.append(
                {
                    "scenario_kind": "htf_footprint_first_touch_episode",
                    "event_time_ns": bar.ts_close_ns,
                    "symbol": self.symbol,
                    "zone_ids": [zone.zone_id for zone in self._recent_macro_footprint_touches],
                    "zone_kinds": [
                        self._kind_value(zone.kind)
                        for zone in self._recent_macro_footprint_touches
                    ],
                    "rule_provenance": FRESH_HTF_FOOTPRINT_RULE,
                },
            )

    def _fresh_macro_footprints(self) -> list[Any]:
        output: list[Any] = []
        seen: set[str] = set()
        for zone in self.macro_footprints.active_zones():
            if zone.first_touch_time_ns is not None:
                continue
            if zone.zone_id not in seen:
                seen.add(zone.zone_id)
                output.append(zone)
        for zone in self._recent_macro_footprint_touches:
            if zone.active and zone.zone_id not in seen:
                seen.add(zone.zone_id)
                output.append(zone)
        return output

    def _footprint_evidence(self, plan: V5TradePlan) -> list[ContextEvidence]:
        wanted = self._zone_side_for_plan(plan)
        output: list[ContextEvidence] = []
        for zone in self._fresh_macro_footprints():
            if zone.side is not wanted:
                continue
            if zone.observed_time_ns > plan.observed_time_ns:
                continue
            if not self._interval_touches_plan(zone.lower, zone.upper, plan):
                continue
            first_touch_state = (
                "UNTOUCHED"
                if zone.first_touch_time_ns is None
                else "FIRST_COMPLETED_TOUCH_EPISODE"
            )
            output.append(
                ContextEvidence(
                    evidence_id=zone.zone_id,
                    evidence_kind=self._kind_value(zone.kind),
                    lower=zone.lower,
                    upper=zone.upper,
                    observed_time_ns=zone.observed_time_ns,
                    source=f"HTF_FOOTPRINT:{first_touch_state}",
                ),
            )
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["top_down_context_router"] = dict(output["top_down_context_router"])
        output["top_down_context_router"].update(
            {
                "countertrend_footprint_lifecycle": (
                    "UNTOUCHED_OR_FIRST_COMPLETED_60M_TOUCH_EPISODE"
                ),
                "fresh_macro_footprints_at_end": len(self._fresh_macro_footprints()),
                "fresh_footprint_rule": FRESH_HTF_FOOTPRINT_RULE,
            },
        )
        return output


MultiScaleScenarioBundle = EasyChartRE1FreshBundle
