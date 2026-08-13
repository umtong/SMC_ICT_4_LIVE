"""Mechanism-corrected continuation core for EasyChart RE1.

The disclosed random intervals showed that three machine conveniences were not
valid translations of the source material:

1. a channel's main line could be traded before the ordered opposite fourth
   point, including under the duplicate trend-line label;
2. every two-pivot trend line was treated as a complete decision area even when
   no pre-existing 15-minute institutional footprint occupied the retest;
3. a loose two-touch horizontal construction was allowed to stand in for the
   source's visible contraction/liquidity-pool sequence.

The channel error is corrected by ``EasyChartRE1PhaseBundle``.  This module then
keeps a deliberately small continuation core:

* phased channel interactions retain their existing rejection and accepted-
  break mechanisms;
* a trend-line-only plan is executable only when its entry/overlap area also
  intersects a pre-existing active 15-minute OB or FVG of the same side;
* the old repeated-defense horizontal family is diagnostic only until it is
  replaced by a complete box/contraction -> sweep -> reclaim scenario.

The 15-minute footprint is location evidence, not an extra trigger.  The local
1-minute event response remains the entry confirmation.  No outcome score,
clock, volatility threshold, R cap, fitted distance or risk multiplier is
introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_fresh import EasyChartRE1FreshBundle
from easychart_re1_phase import EasyChartRE1PhaseBundle


TRENDLINE_DECISION_AREA_RULE = (
    "SOURCE_EXPLICIT:"
    "TRENDLINE_ENTRY_REQUIRES_LOWER_TIMEFRAME_REVERSAL_AT_A_MEANINGFUL_STRUCTURE_OR_FOOTPRINT_AREA"
)
HORIZONTAL_CONTRACTION_DEFER_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "TWO_TOUCH_HORIZONTAL_LEVEL_IS_DIAGNOSTIC_UNTIL_COMPLETE_CONTRACTION_SWEEP_RECLAIM_IS_ENCODED"
)
if TRENDLINE_DECISION_AREA_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (TRENDLINE_DECISION_AREA_RULE,)
if HORIZONTAL_CONTRACTION_DEFER_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (HORIZONTAL_CONTRACTION_DEFER_RULE,)


class EasyChartRE1CoreBundle(EasyChartRE1PhaseBundle):
    """Phased channels plus footprint-located trend-line continuation."""

    @staticmethod
    def _is_trendline_kind(kind: Any) -> bool:
        return str(getattr(kind, "value", kind)) in {"UPTREND_LINE", "DOWNTREND_LINE"}

    def _route_plan(self, plan: V5TradePlan) -> bool:
        if not super()._route_plan(plan):
            return False
        if not self._is_trendline_kind(plan.higher_zone_kind):
            return True

        footprint_ids = self._decision_footprint_ids(plan)
        allowed = bool(footprint_ids)
        reason = (
            "trendline_allowed_preexisting_15m_footprint_area"
            if allowed
            else "trendline_rejected_without_preexisting_15m_footprint_area"
        )
        self._router_inc(reason)
        self._bundle_trace.append(
            {
                "scenario_kind": reason,
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "setup_id": plan.setup_id,
                "side": plan.side.name,
                "higher_zone_kind": self._kind_value(plan.higher_zone_kind),
                "decision_footprint_ids": footprint_ids,
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target,
                "gross_rr": plan.gross_rr,
                "rule_provenance": TRENDLINE_DECISION_AREA_RULE,
            },
        )
        return allowed

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        # Bypass EasyChartRE1Integrated/Natural horizontal routing.  The
        # horizontal engine remains instantiated for diagnostics/provenance but
        # receives no bars and cannot emit an account candidate.
        return EasyChartRE1FreshBundle.on_bar(self, timeframe_minutes, bar)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["executable_families"] = {
            "diagonal": "PHASED_CHANNEL_AND_FOOTPRINT_LOCATED_TRENDLINE",
            "horizontal": "DIAGNOSTIC_ONLY_PENDING_COMPLETE_BOX_CONTRACTION",
            "trendline_rule": TRENDLINE_DECISION_AREA_RULE,
            "horizontal_defer_rule": HORIZONTAL_CONTRACTION_DEFER_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1CoreBundle
