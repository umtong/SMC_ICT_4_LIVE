"""First-later response for one-bar boundary absorption entries.

The current responsible-flow policy correctly gives a newly formed visual
OB/FVG ownership of its future mitigation.  When no visual footprint exists,
however, a single completed minute with opposite aggressor flow can still both
define absorption and become the entry bar.  The archived continuous-account
runs show that this one-bar substitution is regime-unstable and cannot be
repaired by a stable amount threshold.

This module changes the causal decision, not a fitted cutoff:

* the first completed minute which closes on the intended side of a pre-existing
  boundary while absorbing opposite aggressor flow records the event;
* the first later completed minute must close beyond that absorption bar's
  favorable extreme and remain beyond the current projected boundary;
* target spend, structural invalidation, or a failed first response terminates
  the episode; there is no rescue entry;
* repeated multi-bar absorption, visual first-return ownership and accepted-break
  response paths remain unchanged.

The response close becomes the immutable entry.  Existing stop, target,
NautilusTrader execution, one-account slot and current-NAV 3% risk remain the
execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState, V5TradePlan
from domain import Side
from easychart_re1_flow import FlowSignal
from easychart_re1_significant_response import (
    EasyChartRE1SignificantResponseBundle,
    SignificantResponseDecisionOBEngine,
    SignificantResponseMajorSwingEngine,
    SignificantResponseMicroEngine,
)


CURRENT_ABSORPTION_FIRST_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_SINGLE_COMPLETED_BOUNDARY_ABSORPTION_BAR_"
    "RECORDS_THE_EVENT_AND_THE_FIRST_LATER_COMPLETED_MINUTE_MUST_CLOSE_BEYOND_"
    "ITS_FAVORABLE_EXTREME_WHILE_REMAINING_BEYOND_THE_PROJECTED_BOUNDARY"
)
if CURRENT_ABSORPTION_FIRST_RESPONSE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,)


@dataclass(frozen=True, slots=True)
class PendingCurrentAbsorptionResponse:
    setup_id: str
    signal_time_ns: int
    absorption_high: float
    absorption_low: float
    absorption_close: float
    signal: FlowSignal


class CurrentAbsorptionFirstResponseMixin:
    """Turn one-bar current absorption into event -> first-response entry."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_current_absorption: dict[
            str,
            PendingCurrentAbsorptionResponse,
        ] = {}
        self._absorption_response_plans: list[V5TradePlan] = []
        self._absorption_response_counts: dict[str, int] = {}

    def _arinc(self, key: str) -> None:
        self._absorption_response_counts[key] = (
            self._absorption_response_counts.get(key, 0) + 1
        )

    def _finish(
        self,
        setup: Any,
        state: SetupState,
        time_ns: int,
        reason: str,
        **values: Any,
    ) -> None:
        self._pending_current_absorption.pop(setup.setup_id, None)
        super()._finish(setup, state, time_ns, reason, **values)

    def _flow_signal(
        self,
        setup: Any,
        bar: Any,
        observation: Any,
    ) -> FlowSignal | None:
        # The first later minute owns the unresolved absorption event.  Do not
        # let that same setup re-label the response bar as a fresh flow entry.
        if setup.setup_id in self._pending_current_absorption:
            return None
        return super()._flow_signal(setup, bar, observation)

    def _create_flow_plan(
        self,
        setup: Any,
        bar: Any,
        signal: FlowSignal,
        *,
        acceptance: bool,
    ) -> V5TradePlan | None:
        current_absorption = signal.mechanism.endswith("CURRENT_ABSORPTION")
        if acceptance or not current_absorption:
            return super()._create_flow_plan(
                setup,
                bar,
                signal,
                acceptance=acceptance,
            )

        if self._target_is_spent(setup, bar):
            self._finish(
                setup,
                SetupState.TARGET_SPENT,
                bar.ts_close_ns,
                "target_spent_on_current_absorption_before_response",
            )
            return None
        if self._extreme_breached(setup, bar):
            self._finish(
                setup,
                SetupState.INVALIDATED,
                bar.ts_close_ns,
                "interaction_extreme_breached_on_current_absorption",
            )
            return None

        pending = PendingCurrentAbsorptionResponse(
            setup_id=setup.setup_id,
            signal_time_ns=int(bar.ts_close_ns),
            absorption_high=float(bar.high),
            absorption_low=float(bar.low),
            absorption_close=float(bar.close),
            signal=signal,
        )
        self._pending_current_absorption[setup.setup_id] = pending
        self._arinc("current_absorption_waiting_first_response")
        self._finc("current_absorption_waiting_first_response")
        self._trace(
            "current_absorption_waiting_first_response",
            bar.ts_close_ns,
            setup,
            absorption_high=pending.absorption_high,
            absorption_low=pending.absorption_low,
            absorption_close=pending.absorption_close,
            rule_provenance=CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,
            **self._signal_trace(signal),
        )
        return None

    def _process_current_absorption_responses(
        self,
        bar: Any,
    ) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for setup_id, pending in list(self._pending_current_absorption.items()):
            if int(bar.ts_close_ns) <= pending.signal_time_ns:
                continue
            setup = self._active.get(setup_id)
            if setup is None:
                self._pending_current_absorption.pop(setup_id, None)
                self._arinc("pending_setup_cleared_before_response")
                continue

            if self._target_is_spent(setup, bar):
                self._arinc("target_spent_on_first_response")
                self._finish(
                    setup,
                    SetupState.TARGET_SPENT,
                    bar.ts_close_ns,
                    "target_spent_on_first_absorption_response_before_entry",
                    absorption_time_ns=pending.signal_time_ns,
                )
                continue
            if self._extreme_breached(setup, bar):
                self._arinc("structural_invalidation_on_first_response")
                self._finish(
                    setup,
                    SetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "interaction_extreme_breached_on_first_absorption_response",
                    absorption_time_ns=pending.signal_time_ns,
                    response_high=float(bar.high),
                    response_low=float(bar.low),
                )
                continue

            _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            if setup.side is Side.LONG:
                response_confirms = float(bar.close) > pending.absorption_high
                boundary_holds = float(bar.close) > upper
            else:
                response_confirms = float(bar.close) < pending.absorption_low
                boundary_holds = float(bar.close) < lower

            if not response_confirms or not boundary_holds:
                self._arinc("first_response_failed")
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "current_absorption_first_response_failed",
                    absorption_time_ns=pending.signal_time_ns,
                    absorption_high=pending.absorption_high,
                    absorption_low=pending.absorption_low,
                    absorption_close=pending.absorption_close,
                    response_open=float(bar.open),
                    response_high=float(bar.high),
                    response_low=float(bar.low),
                    response_close=float(bar.close),
                    projected_lower=lower,
                    projected_upper=upper,
                    boundary_holds=boundary_holds,
                    rule_provenance=CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,
                )
                continue

            self._pending_current_absorption.pop(setup_id, None)
            confirmed_signal = replace(
                pending.signal,
                mechanism=f"{pending.signal.mechanism}_FIRST_RESPONSE",
                episode_bars=max(2, pending.signal.episode_bars + 1),
            )
            self._trace(
                "current_absorption_first_response_confirmed",
                bar.ts_close_ns,
                setup,
                absorption_time_ns=pending.signal_time_ns,
                absorption_high=pending.absorption_high,
                absorption_low=pending.absorption_low,
                absorption_close=pending.absorption_close,
                response_close=float(bar.close),
                projected_lower=lower,
                projected_upper=upper,
                rule_provenance=CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,
            )
            plan = super()._create_flow_plan(
                setup,
                bar,
                confirmed_signal,
                acceptance=False,
            )
            if plan is None:
                self._arinc("confirmed_response_geometry_rejected")
                continue
            self._arinc("confirmed_response_plan_created")
            self._finc("current_absorption_first_response_plan_created")
            self._absorption_response_plans.append(plan)
            output.append(plan)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Any) -> list[V5TradePlan]:
        if timeframe_minutes != self.trigger_minutes:
            return super().on_bar(timeframe_minutes, bar)
        self._absorption_response_plans = []
        existing = super().on_bar(timeframe_minutes, bar)
        responses = self._process_current_absorption_responses(bar)
        unique = {
            plan.plan_id: plan
            for plan in existing + responses + self._absorption_response_plans
        }
        return sorted(
            unique.values(),
            key=lambda plan: (
                plan.interaction_time_ns,
                plan.observed_time_ns,
                plan.plan_id,
            ),
        )

    @property
    def current_absorption_response_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._absorption_response_counts.items())),
            "pending": len(self._pending_current_absorption),
            "rule_provenance": CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,
        }


class PersistentAbsorptionResponseMicroEngine(
    CurrentAbsorptionFirstResponseMixin,
    SignificantResponseMicroEngine,
):
    pass


class PersistentAbsorptionResponseMajorSwingEngine(
    CurrentAbsorptionFirstResponseMixin,
    SignificantResponseMajorSwingEngine,
):
    pass


class PersistentAbsorptionResponseDecisionOBEngine(
    CurrentAbsorptionFirstResponseMixin,
    SignificantResponseDecisionOBEngine,
):
    pass


class EasyChartRE1PersistentAbsorptionResponseBundle(
    EasyChartRE1SignificantResponseBundle,
):
    """Significant-response system with causal persistence for one-bar absorption."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = PersistentAbsorptionResponseMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = PersistentAbsorptionResponseMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = PersistentAbsorptionResponseDecisionOBEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "major_swing", "flow_decision_ob"):
            self._audit_offsets[key] = 0

    @staticmethod
    def _response_diagnostics(engine: Any) -> dict[str, Any]:
        return engine.current_absorption_response_diagnostics

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["current_absorption_first_response"] = {
            "micro": self._response_diagnostics(self.micro),
            "major_swing": self._response_diagnostics(self.major_swing),
            "flow_decision_ob": self._response_diagnostics(self.flow_decision_ob),
            "rule": CURRENT_ABSORPTION_FIRST_RESPONSE_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1PersistentAbsorptionResponseBundle
