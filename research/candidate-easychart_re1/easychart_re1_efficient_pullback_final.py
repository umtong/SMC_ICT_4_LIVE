"""Final efficient-pullback lifecycle: current leg and latest accepted level only.

Two lifecycle facts complete the first-pullback translation:

* once an opposite fifteen-minute leg is accepted, pending pullbacks from the
  prior leg are no longer valid continuation scenarios;
* once a newer same-direction five-minute level is accepted, it owns the next
  first pullback and older untested levels become stale context rather than new
  independent trades.

This module adds only those ownership transitions to the frozen-target policy.
It does not change the entry evidence, stop, objective, risk, account or
execution rules.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Candle
from easychart_re1_efficient_pullback_v2 import (
    EFFICIENT_PULLBACK_FROZEN_TARGET_RULE,
    EasyChartRE1EfficientPullbackV2Bundle,
    FrozenTargetEfficientPullbackEngine,
)
from easychart_re1_efficient_pullback import (
    EFFICIENT_PULLBACK_IMPULSE_RULE,
    EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
    EFFICIENT_PULLBACK_OBJECTIVE_RULE,
    EFFICIENT_PULLBACK_RESPONSE_RULE,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy


EFFICIENT_PULLBACK_CURRENT_LEG_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "AN_ACCEPTED_OPPOSITE_FIFTEEN_MINUTE_LEG_ENDS_ALL_PENDING_PULLBACKS_FROM_THE_PRIOR_LEG"
)
EFFICIENT_PULLBACK_LATEST_LEVEL_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "THE_LATEST_ACCEPTED_SAME_DIRECTION_FIVE_MINUTE_BREAK_OWNS_THE_NEXT_FIRST_PULLBACK_AND_SUPERSEDES_OLDER_UNTESTED_LEVELS"
)
for _rule in (EFFICIENT_PULLBACK_CURRENT_LEG_RULE, EFFICIENT_PULLBACK_LATEST_LEVEL_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class CurrentLegEfficientPullbackEngine(FrozenTargetEfficientPullbackEngine):
    """Trade only the current local leg and its latest accepted pullback level."""

    def _confirm_or_fail_local_direction(self, bar: Candle) -> None:
        previous_side = self.local_side
        super()._confirm_or_fail_local_direction(bar)
        if self.local_side is None or self.local_side is previous_side:
            return
        for setup in list(self._active.values()):
            if setup.side is self.local_side:
                continue
            self._finish(
                setup,
                "efficient_pullback_prior_leg_superseded",
                bar.ts_close_ns,
                new_local_side=self.local_side.name,
                new_local_pivot_id=None if self.local_pivot is None else self.local_pivot.pivot_id,
                rule_provenance=EFFICIENT_PULLBACK_CURRENT_LEG_RULE,
            )
        self._inc("prior_leg_pullbacks_cleared")

    def _advance_holds(self, bar: Candle) -> None:
        before = {
            setup.setup_id: setup.state
            for setup in self._active.values()
        }
        super()._advance_holds(bar)
        newly_accepted = [
            setup
            for setup in self._active.values()
            if setup.state == "WAITING_RETEST"
            and before.get(setup.setup_id) == "WAITING_HOLD"
        ]
        for owner in newly_accepted:
            for setup in list(self._active.values()):
                if setup.setup_id == owner.setup_id:
                    continue
                if setup.side is not owner.side:
                    continue
                if setup.break_time_ns >= owner.break_time_ns:
                    continue
                if setup.state not in {"WAITING_RETEST", "WAITING_RESPONSE"}:
                    continue
                self._finish(
                    setup,
                    "efficient_pullback_superseded_by_newer_accepted_level",
                    bar.ts_close_ns,
                    newer_setup_id=owner.setup_id,
                    newer_break_pivot_id=owner.break_pivot.pivot_id,
                    newer_break_time_ns=owner.break_time_ns,
                    rule_provenance=EFFICIENT_PULLBACK_LATEST_LEVEL_RULE,
                )
            self._inc("latest_accepted_level_claimed_next_pullback")

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["current_leg_latest_level"] = {
            "rules": (
                EFFICIENT_PULLBACK_CURRENT_LEG_RULE,
                EFFICIENT_PULLBACK_LATEST_LEVEL_RULE,
            ),
        }
        return output


class EasyChartRE1EfficientPullbackFinalBundle(EasyChartRE1EfficientPullbackV2Bundle):
    """Integrated policy with a single current efficient-pullback opportunity."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.efficient_pullback = CurrentLegEfficientPullbackEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["efficient_pullback_final"] = {
            "rules": (
                EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
                EFFICIENT_PULLBACK_IMPULSE_RULE,
                EFFICIENT_PULLBACK_RESPONSE_RULE,
                EFFICIENT_PULLBACK_OBJECTIVE_RULE,
                EFFICIENT_PULLBACK_FROZEN_TARGET_RULE,
                EFFICIENT_PULLBACK_CURRENT_LEG_RULE,
                EFFICIENT_PULLBACK_LATEST_LEVEL_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1EfficientPullbackFinalBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
