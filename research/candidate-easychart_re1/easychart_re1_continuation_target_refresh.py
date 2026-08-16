"""Continuation target lifecycle repaired after a confirmed local impulse.

The local continuation engine already requires a causal 15m/5m control break,
a flow-validated 5m engulfing order block, the first later pullback to source OB
or anchored fair value, and the first subsequent 1m response.  Its remaining
failure was semantic: the initial impulse extreme was stored as a provisional
objective, then touching that extreme before any pullback retired the setup.

That treats healthy post-break expansion as if the future pullback trade had
already consumed its objective.  In an expansion -> return -> rebalancing ->
continuation auction, pre-pullback extension confirms initiative.  It is not a
trade outcome because no entry exists yet.  The actual immutable objective is
chosen at the response close by the existing causal target refresh, which uses
only pre-existing unspent 5m/15m structure beyond the entry.

This module changes only that lifecycle interpretation.  It does not relax the
impulse, flow, source-zone, first-pullback, first-response, stop, minimum-RR,
cost, risk, or single-account requirements.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Candle
from easychart_re1_displacement_confirmed_auction import (
    EasyChartRE1DisplacementConfirmedAuctionBundle,
)
from easychart_re1_local_continuation import (
    LocalAuctionContinuationEngine,
    LocalContinuationSetup,
)


CONTINUATION_TARGET_LIFECYCLE_RULE = (
    "RESEARCH_SYNTHESIS:A_PREENTRY_EXTENSION_BEYOND_THE_INITIAL_IMPULSE_EXTREME_"
    "CONFIRMS_INITIATIVE_AND_DOES_NOT_SPEND_A_FUTURE_PULLBACK_TRADE_OBJECTIVE_"
    "WHICH_IS_REFRESHED_CAUSALLY_AT_THE_FIRST_RESPONSE_CLOSE"
)
if CONTINUATION_TARGET_LIFECYCLE_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (CONTINUATION_TARGET_LIFECYCLE_RULE,)


class PostImpulseTargetRefreshContinuationEngine(LocalAuctionContinuationEngine):
    """Preserve a valid impulse until its first pullback or true invalidation."""

    def _target_touched(
        self,
        setup: LocalContinuationSetup,
        bar: Candle,
    ) -> bool:
        touched = super()._target_touched(setup, bar)
        if setup.state == "WAITING_PULLBACK" and touched:
            self._inc("pre_pullback_impulse_extension_preserved")
            self._record(
                "local_continuation_pre_pullback_impulse_extension_preserved",
                bar.ts_close_ns,
                setup_id=setup.setup_id,
                side=setup.side.name,
                provisional_impulse_extreme=setup.target_price,
                bar_high=bar.high,
                bar_low=bar.low,
                rule_provenance=CONTINUATION_TARGET_LIFECYCLE_RULE,
            )
            return False
        return touched


class EasyChartRE1ContinuationTargetRefreshBundle(
    EasyChartRE1DisplacementConfirmedAuctionBundle
):
    """Current displacement core plus the repaired continuation lifecycle."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        # The parent created an untouched continuation engine during
        # construction.  Replace it before the first bar so all state belongs
        # to the repaired engine and the parent router continues to own episode
        # arbitration unchanged.
        self.continuation = PostImpulseTargetRefreshContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["continuation_target_lifecycle"] = {
            "rule_provenance": CONTINUATION_TARGET_LIFECYCLE_RULE,
            "policy": (
                "PRESERVE_PRE_PULLBACK_EXTENSION_THEN_REFRESH_UNSPENT_"
                "STRUCTURE_OBJECTIVE_AT_FIRST_RESPONSE"
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ContinuationTargetRefreshBundle
