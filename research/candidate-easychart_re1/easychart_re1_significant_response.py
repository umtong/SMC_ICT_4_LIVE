"""Response-confirmed auction entries with the first significant live objective.

The strongest reusable findings from the preceding diagnostics solve two
different parts of one trading decision.

Entry ownership
---------------
An accepted break is not complete merely because a decision bar closes outside.
When the confirming five-minute hold bar itself retests the old boundary, its
final completed one-minute bar records that retest and the first later completed
one-minute bar must prove renewed defense before entry.  Detached holds retain
the inherited first-later-retest path.  Rejection entries keep the existing
single-owner OB/FVG or current flow response logic.

Objective ownership
-------------------
The original setup target can become stale while a setup waits for a lower-frame
entry.  Immediately before submission this policy compares that target with:

* the nearest still-unspent opposing five- or fifteen-minute structure already
  confirmed at entry; and
* the nearest still-unspent causally confirmed span-6 one-minute opposing swing.

Only a *closer* obstacle replaces the existing objective.  A span-2 micro pivot
is deliberately excluded because the earlier diagnostic showed that ordinary
one-minute noise produced very small post-cost wins and removed useful trades.
Channel rotations retain their explicit channel objective.  If the true first
significant obstacle leaves less than the inherited 1.0 gross R minimum, the
trade is rejected rather than skipping over that obstacle to manufacture RR.

No fitted distance, R cap, session filter, trade limit, partial exit, stop move,
clock timeout, symbol exception or PnL-dependent choice is introduced.  The
four-symbol one-position account, current-NAV 3% risk, NautilusTrader execution,
fees and immutable full stop/full target remain unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_embedded_acceptance_response import (
    EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE,
    EasyChartRE1EmbeddedAcceptanceResponseBundle,
    ResponseEmbeddedNaturalHorizontalEngine,
    ResponseEmbeddedResponsibleFlowMajorSwingEngine,
    ResponseEmbeddedResponsibleFlowValidatedDecisionAreaEngine,
    ResponseEmbeddedResponsiblePhaseFlowMicroEngine,
)
from easychart_re1_preentry_objective import (
    PREENTRY_OBJECTIVE_REFRESH_RULE,
    PreEntryObjectiveRefreshMixin,
)
from easychart_re1_significant_micro_objective import (
    SIGNIFICANT_MICRO_SWING_OBJECTIVE_RULE,
    SignificantMicroObjectiveMixin,
)


SIGNIFICANT_RESPONSE_POLICY_RULE = (
    "RESEARCH_SYNTHESIS:"
    "RESPONSE_CONFIRMED_ACCEPTANCE_AND_SINGLE_OWNER_REJECTION_TARGET_THE_NEAREST_LIVE_FIVE_FIFTEEN_OR_SIGNIFICANT_SPAN6_ONE_MINUTE_OPPOSING_STRUCTURE"
)
if SIGNIFICANT_RESPONSE_POLICY_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (SIGNIFICANT_RESPONSE_POLICY_RULE,)


class SignificantResponseMicroEngine(
    SignificantMicroObjectiveMixin,
    PreEntryObjectiveRefreshMixin,
    ResponseEmbeddedResponsiblePhaseFlowMicroEngine,
):
    """Micro auction with refreshed 5m/15m and significant 1m objective."""


class SignificantResponseHorizontalEngine(
    SignificantMicroObjectiveMixin,
    PreEntryObjectiveRefreshMixin,
    ResponseEmbeddedNaturalHorizontalEngine,
):
    """Repeated horizontal defense/acceptance with first live objective."""


class SignificantResponseMajorSwingEngine(
    SignificantMicroObjectiveMixin,
    PreEntryObjectiveRefreshMixin,
    ResponseEmbeddedResponsibleFlowMajorSwingEngine,
):
    """Major-liquidity event with first live objective."""


class SignificantResponseDecisionOBEngine(
    SignificantMicroObjectiveMixin,
    PreEntryObjectiveRefreshMixin,
    ResponseEmbeddedResponsibleFlowValidatedDecisionAreaEngine,
):
    """Flow-validated decision OB with first live objective."""


class EasyChartRE1SignificantResponseBundle(
    EasyChartRE1EmbeddedAcceptanceResponseBundle
):
    """One coherent rejection/acceptance system with significant objectives."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = SignificantResponseMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = SignificantResponseHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = SignificantResponseMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = SignificantResponseDecisionOBEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "horizontal", "major_swing", "flow_decision_ob"):
            self._audit_offsets[key] = 0

    @staticmethod
    def _engine_diagnostics(engine: Any) -> dict[str, Any]:
        return {
            "significant_micro_objective": (
                engine.significant_micro_objective_diagnostics
            ),
            "preentry_objective_refresh": engine.objective_refresh_diagnostics,
            "embedded_acceptance_response": (
                engine.embedded_acceptance_response_diagnostics
            ),
        }

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["significant_response_policy"] = {
            "micro": self._engine_diagnostics(self.micro),
            "horizontal": self._engine_diagnostics(self.horizontal),
            "major_swing": self._engine_diagnostics(self.major_swing),
            "flow_decision_ob": self._engine_diagnostics(self.flow_decision_ob),
            "rules": (
                EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE,
                PREENTRY_OBJECTIVE_REFRESH_RULE,
                SIGNIFICANT_MICRO_SWING_OBJECTIVE_RULE,
                SIGNIFICANT_RESPONSE_POLICY_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1SignificantResponseBundle
