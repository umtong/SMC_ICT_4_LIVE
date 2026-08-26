"""Cross-asset laggard catch-up continuation for EasyChart RE1.

A three-of-four common crypto initiative leaves exactly one altcoin outside the
initial broad move because BTC and ETH are mandatory leaders.  Short-horizon
cross-impact research indicates that lagged cross-asset order flow can forecast
future returns, while contemporaneous cross-impact adds little once own-flow is
known.  The corresponding trading responsibility is therefore precise:

* the broad BTC/ETH-led direction supplies a causal information shock;
* the excluded SOL or XRP must later produce its own aligned, flow-validated
  five-minute OB/FVG with real price progress;
* the first later return only arms the setup, and a subsequent completed minute
  must prove control transfer before entry;
* the full five-minute footprint invalidates the plan, and the first eligible
  formation-wave or pre-existing structure objective must offer at least 1R.

This family does not trade an asset already participating in the initiating
common shock.  It is an independent catch-up scenario, not a volume threshold or
an extra AND filter on existing trades.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Side
from easychart_re1_persistent_confirmed_fixed import (
    EasyChartRE1FixedConfirmedPersistentBundle,
    FixedConfirmedPersistentContinuationEngine,
)
from easychart_re1_persistent_continuation import PersistentContinuationMarketStrategy
from execution_re1_factor_persistence import CommonAuctionRegime

LAGGARD_CROSS_IMPACT_RULE = (
    "EXTERNAL_METHOD:BTC_ETH_LED_THREE_OF_FOUR_COMMON_INITIATIVE_DEFINES_ONE_EXCLUDED_ALTCOIN_WITH_SHORT_HORIZON_CATCH_UP_POTENTIAL"
)
LAGGARD_OWN_FLOW_CONFIRMATION_RULE = (
    "RESEARCH_HYPOTHESIS:THE_EXCLUDED_ALTCOIN_MUST_FORM_ITS_OWN_ALIGNED_FLOW_VALIDATED_FIVE_MINUTE_OB_OR_FVG_BEFORE_FIRST_RETURN_ENTRY"
)
for _rule in (LAGGARD_CROSS_IMPACT_RULE, LAGGARD_OWN_FLOW_CONFIRMATION_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class LaggardConfirmedContinuationEngine(FixedConfirmedPersistentContinuationEngine):
    """Confirmed pullback only for the altcoin excluded from a 3/4 common shock."""

    def _formation_context(self, side: Side) -> bool:
        snapshot = self.common_snapshot
        return (
            snapshot.regime is CommonAuctionRegime.PERSISTENT
            and snapshot.active_matches_history
            and snapshot.side is side
            and snapshot.latest_event_time_ns is not None
            and len(snapshot.latest_agreeing_symbols) == 3
            and self.symbol not in snapshot.latest_agreeing_symbols
            and self.symbol in {"SOLUSDT", "XRPUSDT"}
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["laggard_policy"] = {
            "leaders": ("BTCUSDT", "ETHUSDT"),
            "eligible_laggards": ("SOLUSDT", "XRPUSDT"),
            "required_common_participants": 3,
            "rules": (LAGGARD_CROSS_IMPACT_RULE, LAGGARD_OWN_FLOW_CONFIRMATION_RULE),
        }
        return output


class EasyChartRE1LaggardContinuationBundle(EasyChartRE1FixedConfirmedPersistentBundle):
    """Selective visual core plus one cross-asset laggard catch-up family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.persistent_continuation = LaggardConfirmedContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["persistent_continuation"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["continuation_responsibility"] = "EXCLUDED_ALTCOIN_CATCH_UP_ONLY"
        return output


MultiScaleScenarioBundle = EasyChartRE1LaggardContinuationBundle
StrategyClass = PersistentContinuationMarketStrategy
