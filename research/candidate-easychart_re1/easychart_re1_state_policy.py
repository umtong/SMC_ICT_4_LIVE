"""Integrated market-state policy for EasyChart RE1.

The system assigns one causal mechanism to each broad auction state:

* persistent common initiative: flow-validated five-minute OB/FVG, first return,
  later completed control-transfer response, natural fixed invalidation and the
  first eligible wave/structure objective;
* turbulent common flow: only a mature same-scale repeated-defense box may
  reverse after a five-minute sweep, close back inside and adverse constituent
  taker absorption;
* transitional or unknown state: the selective visual decision-OB and major
  liquidity core remains available, while single-bar flow proxies do not own a
  trade.

This module only composes the already auditable state-specific engines.  It does
not add a score, fitted threshold, session rule, daily limit, partial exit or
post-entry stop movement.
"""
from __future__ import annotations

from typing import Any

from easychart_re1_persistent_confirmed_fixed import (
    FixedConfirmedPersistentContinuationEngine,
    PERSISTENT_CONFIRMED_RESPONSE_RULE,
    PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
)
from easychart_re1_turbulent_box import (
    COMPLETE_CONTRACTION_BOX_RULE,
    LOCAL_BOX_SELECTION_RULE,
    EasyChartRE1BoxAuctionBundle,
)
from easychart_re1_turbulent_contraction import (
    CONTRACTION_OBJECTIVE_RULE,
    TURBULENT_ADVERSE_FLOW_RULE,
    FullAuctionStateStrategy,
)


class EasyChartRE1StatePolicyBundle(EasyChartRE1BoxAuctionBundle):
    """Confirmed persistent continuation plus complete-box turbulent reversal."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.persistent_continuation = FixedConfirmedPersistentContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["persistent_continuation"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["integrated_state_policy"] = {
            "persistent": "CONFIRMED_5M_OB_FVG_FIRST_RETURN",
            "turbulent": "COMPLETE_PAIRED_DEFENSE_BOX_SWEEP_RECLAIM",
            "transitional": "VISUAL_DECISION_OB_OR_MAJOR_LIQUIDITY_ONLY",
            "rules": (
                PERSISTENT_CONFIRMED_RESPONSE_RULE,
                PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
                COMPLETE_CONTRACTION_BOX_RULE,
                LOCAL_BOX_SELECTION_RULE,
                TURBULENT_ADVERSE_FLOW_RULE,
                CONTRACTION_OBJECTIVE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1StatePolicyBundle
StrategyClass = FullAuctionStateStrategy
