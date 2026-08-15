"""Require the first completed micro response after an embedded accepted retest.

An accepted break has two causal events: the completed decision-frame hold which
proves price can remain outside the old boundary, and the lower-frame response
which proves that the boundary is actually being defended.  The embedded
acceptance candidate recognized when the hold bar itself had already wicked
back into the boundary, but entered on the last one-minute close inside that
same hold bar.  That still admitted many bars whose next minute immediately
failed.

This module keeps the embedded-ret​est ownership and adds the same immediate
response semantics already used by detached accepted-break retests:

* the completed five-minute hold must itself touch the pre-existing boundary
  and close outside;
* the final completed one-minute bar at that timestamp records the retest and
  fixes the executable structural stop;
* the first later completed one-minute bar must close beyond the retest bar's
  favorable extreme before either stop or target trades;
* entry occurs at that response close and the original first structural target
  must still provide at least 1R.

A failed first response ends the causal episode.  No later rescue entry, score,
ATR rule, clock timeout, session filter, partial exit or fitted threshold is
introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState, V5TradePlan
from domain import Side
from easychart_re1_embedded_acceptance import (
    EMBEDDED_ACCEPTANCE_RETEST_RULE,
    SAME_TIMESTAMP_COMPLETED_ENTRY_RULE,
    EmbeddedAcceptanceRetestMixin,
)
from easychart_re1_flow_ob_responsibility import (
    EasyChartRE1ResponsibleFlowOBBundle,
    ResponsibleFlowValidatedDecisionAreaEngine,
)
from easychart_re1_flow_ob_sweep_responsibility import (
    ResponsibleFlowMajorSwingEngine,
    ResponsiblePhaseFlowMicroEngine,
)
from easychart_re1_natural_geometry import NaturalHorizontalEngine


EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "AN_ACCEPTANCE_HOLD_BAR_WHICH_ALREADY_RETESTED_THE_BOUNDARY_REQUIRES_THE_FIRST_LATER_COMPLETED_MICRO_CLOSE_BEYOND_ITS_FINAL_MICRO_RETEST_EXTREME"
)
if EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE,)


@dataclass(frozen=True, slots=True)
class PendingEmbeddedAcceptanceResponse:
    setup_id: str
    retest_time_ns: int
    retest_high: float
    retest_low: float
    retest_close: float
    stop: float
    trigger_zone: Any


class EmbeddedAcceptanceFirstResponseMixin(EmbeddedAcceptanceRetestMixin):
    """Delay an embedded accepted-break entry until its first micro response."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_embedded_acceptance_responses: dict[
            str,
            PendingEmbeddedAcceptanceResponse,
        ] = {}

    def _finish(
        self,
        setup: Any,
        state: SetupState,
        time_ns: int,
        reason: str,
        **values: Any,
    ) -> None:
        self._pending_embedded_acceptance_responses.pop(setup.setup_id, None)
        self._embedded_acceptance_retests.pop(setup.setup_id, None)
        super()._finish(setup, state, time_ns, reason, **values)

    @staticmethod
    def _response_confirms(
        setup: Any,
        pending: PendingEmbeddedAcceptanceResponse,
        bar: Any,
    ) -> bool:
        return (
            bar.close > pending.retest_high
            if setup.side is Side.LONG
            else bar.close < pending.retest_low
        )

    @staticmethod
    def _pending_stop_touched(
        setup: Any,
        pending: PendingEmbeddedAcceptanceResponse,
        bar: Any,
    ) -> bool:
        return (
            bar.low <= pending.stop
            if setup.side is Side.LONG
            else bar.high >= pending.stop
        )

    def _process_pending_embedded_response(
        self,
        setup: Any,
        pending: PendingEmbeddedAcceptanceResponse,
        bar: Any,
    ) -> V5TradePlan | None:
        if bar.ts_close_ns <= pending.retest_time_ns:
            return None
        if self._target_is_spent(setup, bar):
            self._eainc("embedded_target_spent_on_first_response")
            self._finish(
                setup,
                SetupState.TARGET_SPENT,
                bar.ts_close_ns,
                "embedded_acceptance_target_spent_on_first_response_before_entry",
                retest_time_ns=pending.retest_time_ns,
            )
            return None
        if self._pending_stop_touched(setup, pending, bar):
            self._eainc("embedded_stop_touched_on_first_response")
            self._finish(
                setup,
                SetupState.INVALIDATED,
                bar.ts_close_ns,
                "embedded_acceptance_stop_touched_on_first_response_before_entry",
                retest_time_ns=pending.retest_time_ns,
                stop=pending.stop,
                response_low=bar.low,
                response_high=bar.high,
            )
            return None
        if not self._response_confirms(setup, pending, bar):
            self._eainc("embedded_first_response_failed")
            self._finish(
                setup,
                SetupState.UNRESOLVED,
                bar.ts_close_ns,
                "embedded_acceptance_first_response_failed",
                retest_time_ns=pending.retest_time_ns,
                retest_high=pending.retest_high,
                retest_low=pending.retest_low,
                response_open=bar.open,
                response_high=bar.high,
                response_low=bar.low,
                response_close=bar.close,
                rule_provenance=EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE,
            )
            return None

        self._pending_embedded_acceptance_responses.pop(setup.setup_id, None)
        self._eainc("embedded_first_response_confirmed")
        self._trace(
            "embedded_acceptance_first_response_confirmed",
            bar.ts_close_ns,
            setup,
            retest_time_ns=pending.retest_time_ns,
            retest_high=pending.retest_high,
            retest_low=pending.retest_low,
            retest_close=pending.retest_close,
            response_close=bar.close,
            stop=pending.stop,
            trigger_zone_id=pending.trigger_zone.zone_id,
            rule_provenance=EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE,
        )
        plan = self._make_plan(
            setup,
            bar,
            entry=bar.close,
            stop=pending.stop,
            trigger_zone=pending.trigger_zone,
            trigger_kind=pending.trigger_zone.kind,
            trigger_strength=pending.trigger_zone.strength_ratio,
        )
        if plan is None:
            self._eainc("embedded_response_geometry_rejected")
            return None
        self._eainc("embedded_response_plan_created")
        return plan

    def _advance_embedded_acceptance_retests(self, bar: Any) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []

        for setup_id, pending in list(
            self._pending_embedded_acceptance_responses.items()
        ):
            setup = self._active.get(setup_id)
            if setup is None:
                self._pending_embedded_acceptance_responses.pop(setup_id, None)
                self._eainc("embedded_pending_setup_cleared")
                continue
            plan = self._process_pending_embedded_response(setup, pending, bar)
            if plan is not None:
                output.append(plan)

        for setup_id, embedded in list(self._embedded_acceptance_retests.items()):
            setup = self._active.get(setup_id)
            if setup is None:
                self._embedded_acceptance_retests.pop(setup_id, None)
                self._eainc("embedded_setup_cleared_before_retest_record")
                continue
            if bar.ts_close_ns < embedded.confirmation_time_ns:
                continue
            if bar.ts_close_ns > embedded.confirmation_time_ns:
                self._embedded_acceptance_retests.pop(setup_id, None)
                self._eainc("same_timestamp_retest_bar_missing_fell_back")
                continue
            self._embedded_acceptance_retests.pop(setup_id, None)
            if setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST:
                self._eainc("embedded_setup_state_changed_before_retest_record")
                continue
            if self._target_is_spent(setup, bar):
                self._eainc("embedded_target_spent")
                self._finish(
                    setup,
                    SetupState.TARGET_SPENT,
                    bar.ts_close_ns,
                    "target_spent_before_embedded_acceptance_response",
                )
                continue

            _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            closes_outside = (
                bar.close > upper if setup.side is Side.LONG else bar.close < lower
            )
            if not closes_outside:
                self._eainc("same_timestamp_close_not_outside")
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "embedded_acceptance_same_timestamp_close_not_outside",
                )
                continue

            stop = self._acceptance_stop(setup, bar.ts_close_ns)
            if stop is None:
                self._eainc("embedded_acceptance_missing_stop")
                self._finish(
                    setup,
                    SetupState.NO_TRADE_GEOMETRY,
                    bar.ts_close_ns,
                    "embedded_acceptance_missing_stop",
                )
                continue
            if setup.side is Side.LONG:
                stop = min(stop, embedded.hold_low - self.tick_size)
            else:
                stop = max(stop, embedded.hold_high + self.tick_size)

            proxy = self.structure.snapshot_for(setup.context, bar.ts_close_ns)
            self._audit(proxy)
            self._pending_embedded_acceptance_responses[setup_id] = (
                PendingEmbeddedAcceptanceResponse(
                    setup_id=setup_id,
                    retest_time_ns=bar.ts_close_ns,
                    retest_high=bar.high,
                    retest_low=bar.low,
                    retest_close=bar.close,
                    stop=stop,
                    trigger_zone=proxy,
                )
            )
            self._eainc("embedded_retest_waiting_first_response")
            self._trace(
                "embedded_acceptance_retest_waiting_first_response",
                bar.ts_close_ns,
                setup,
                retest_high=bar.high,
                retest_low=bar.low,
                retest_close=bar.close,
                stop=stop,
                hold_open=embedded.hold_open,
                hold_high=embedded.hold_high,
                hold_low=embedded.hold_low,
                hold_close=embedded.hold_close,
                projected_lower=lower,
                projected_upper=upper,
                rule_provenance=(
                    EMBEDDED_ACCEPTANCE_RETEST_RULE,
                    SAME_TIMESTAMP_COMPLETED_ENTRY_RULE,
                    EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE,
                ),
            )
        return output

    @property
    def embedded_acceptance_response_diagnostics(self) -> dict[str, Any]:
        output = dict(self.embedded_acceptance_diagnostics)
        output.update(
            {
                "pending_response": len(
                    self._pending_embedded_acceptance_responses
                ),
                "response_rule": EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE,
            }
        )
        return output


class ResponseEmbeddedResponsiblePhaseFlowMicroEngine(
    EmbeddedAcceptanceFirstResponseMixin,
    ResponsiblePhaseFlowMicroEngine,
):
    pass


class ResponseEmbeddedNaturalHorizontalEngine(
    EmbeddedAcceptanceFirstResponseMixin,
    NaturalHorizontalEngine,
):
    pass


class ResponseEmbeddedResponsibleFlowMajorSwingEngine(
    EmbeddedAcceptanceFirstResponseMixin,
    ResponsibleFlowMajorSwingEngine,
):
    pass


class ResponseEmbeddedResponsibleFlowValidatedDecisionAreaEngine(
    EmbeddedAcceptanceFirstResponseMixin,
    ResponsibleFlowValidatedDecisionAreaEngine,
):
    pass


class EasyChartRE1EmbeddedAcceptanceResponseBundle(
    EasyChartRE1ResponsibleFlowOBBundle
):
    """Responsible core plus response-confirmed embedded accepted breaks."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = ResponseEmbeddedResponsiblePhaseFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = ResponseEmbeddedNaturalHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = ResponseEmbeddedResponsibleFlowMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = (
            ResponseEmbeddedResponsibleFlowValidatedDecisionAreaEngine(
                symbol,
                tick_size,
                scale_name="FLOW_DECISION_OB",
                higher_minutes=15,
                decision_minutes=5,
                trigger_minutes=1,
                **kwargs,
            )
        )
        for key in ("micro", "horizontal", "major_swing", "flow_decision_ob"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["embedded_acceptance_first_response"] = {
            "micro": self.micro.embedded_acceptance_response_diagnostics,
            "horizontal": self.horizontal.embedded_acceptance_response_diagnostics,
            "major_swing": self.major_swing.embedded_acceptance_response_diagnostics,
            "flow_decision_ob": (
                self.flow_decision_ob.embedded_acceptance_response_diagnostics
            ),
            "rules": (
                EMBEDDED_ACCEPTANCE_RETEST_RULE,
                SAME_TIMESTAMP_COMPLETED_ENTRY_RULE,
                EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1EmbeddedAcceptanceResponseBundle
