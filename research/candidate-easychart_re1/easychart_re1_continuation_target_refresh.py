"""Continuation target lifecycle repaired after a confirmed local impulse.

The local continuation engine already requires a causal 15m/5m control break,
a flow-validated 5m engulfing order block, the first later pullback to source OB
or anchored fair value, and the first subsequent 1m response.  Its remaining
failure was semantic: the initial impulse extreme was stored as a provisional
objective, then touching that extreme before any pullback retired the setup.

That treats healthy post-break expansion as if the future pullback trade had
already consumed its objective.  In an expansion -> return -> rebalancing ->
continuation auction, pre-pullback extension confirms initiative.  It is not a
trade outcome because no entry exists yet.  At the response close, a spent
provisional extreme is replaced by the nearest then-unspent, already observed
5m/15m opposing structure.  If no such structure exists, or its geometry is
below 1R, no trade is created.

This module changes only that lifecycle interpretation.  It does not relax the
impulse, flow, source-zone, first-pullback, first-response, stop, minimum-RR,
cost, risk, or single-account requirements.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Candle, Side
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
    "WHICH_IS_REPLACED_AT_RESPONSE_BY_THE_NEAREST_PREEXISTING_UNSPENT_STRUCTURE"
)
if CONTINUATION_TARGET_LIFECYCLE_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (CONTINUATION_TARGET_LIFECYCLE_RULE,)


class PostImpulseTargetRefreshContinuationEngine(LocalAuctionContinuationEngine):
    """Preserve a valid impulse until its first pullback or true invalidation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._spent_provisional_targets: set[str] = set()

    def _target_touched(
        self,
        setup: LocalContinuationSetup,
        bar: Candle,
    ) -> bool:
        touched = super()._target_touched(setup, bar)
        if setup.state == "WAITING_PULLBACK" and touched:
            self._spent_provisional_targets.add(setup.setup_id)
            self._inc("pre_pullback_impulse_extension_preserved")
            self._record(
                "local_continuation_pre_pullback_impulse_extension_preserved",
                bar.ts_close_ns,
                setup_id=setup.setup_id,
                side=setup.side.name,
                state=setup.state,
                provisional_impulse_extreme=setup.target_price,
                bar_high=bar.high,
                bar_low=bar.low,
                rule_provenance=CONTINUATION_TARGET_LIFECYCLE_RULE,
            )
            return False
        if (
            setup.state == "WAITING_RESPONSE"
            and setup.setup_id in self._spent_provisional_targets
            and touched
        ):
            self._inc("spent_provisional_target_preserved_for_response_refresh")
            self._record(
                "local_continuation_spent_provisional_target_preserved_for_response_refresh",
                bar.ts_close_ns,
                setup_id=setup.setup_id,
                side=setup.side.name,
                state=setup.state,
                provisional_impulse_extreme=setup.target_price,
                retest_high=setup.retest_high,
                retest_low=setup.retest_low,
                bar_high=bar.high,
                bar_low=bar.low,
                rule_provenance=CONTINUATION_TARGET_LIFECYCLE_RULE,
            )
            return False
        return touched

    def _refresh_target(
        self,
        setup: LocalContinuationSetup,
        bar: Candle,
    ) -> None:
        """Replace a spent provisional extreme with the nearest unspent target."""
        frozen_valid = (
            setup.setup_id not in self._spent_provisional_targets
            and (
                setup.target_price > bar.high
                if setup.side is Side.LONG
                else setup.target_price < bar.low
            )
        )
        choices = self._structure_targets(setup.side, bar)
        valid = [
            item
            for item in choices
            if (
                item[2] > bar.high
                if setup.side is Side.LONG
                else item[2] < bar.low
            )
        ]
        if frozen_valid:
            valid.append(("FROZEN", setup.target_zone, setup.target_price))
        if not valid:
            self._inc("local_continuation_no_unspent_target_at_response")
            return

        source, zone, price = self._nearest(setup.side, valid)
        changed = zone.zone_id != setup.target_zone.zone_id or price != setup.target_price
        if not changed:
            return
        previous_zone_id = setup.target_zone.zone_id
        previous_price = setup.target_price
        setup.target_zone = zone
        setup.target_price = price
        self._audit(zone)
        self._inc("local_continuation_target_replaced_after_extension")
        self._record(
            "local_continuation_target_replaced_after_extension",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            side=setup.side.name,
            previous_target_zone_id=previous_zone_id,
            previous_target_price=previous_price,
            target_source=source,
            target_zone_id=zone.zone_id,
            target_price=price,
            rule_provenance=CONTINUATION_TARGET_LIFECYCLE_RULE,
        )


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
                "TRACK_PREENTRY_IMPULSE_EXTENSION_THEN_REPLACE_SPENT_"
                "PROVISIONAL_EXTREME_WITH_NEAREST_UNSPENT_STRUCTURE"
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ContinuationTargetRefreshBundle
