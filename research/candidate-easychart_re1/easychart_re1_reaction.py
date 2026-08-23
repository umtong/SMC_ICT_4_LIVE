"""First-response confirmation for EasyChart RE1 accepted breaks.

A close outside a broken line followed by a touch of that line is not yet proof
that the S/R flip is defending.  The first RE1 diagnostics entered on that
retest close and produced many one- or two-minute losses with extremely distant
10R+ objectives.  A chart trader naturally watches the immediate response: if
the very next completed micro candle cannot extend beyond the retest candle in
the breakout direction, the claimed flip has not demonstrated demand/supply.

This module makes that implicit decision causal and auditable:

1. the existing accepted-break, hold, close-detachment and first-retest rules
   must all complete unchanged;
2. the retest candle fixes the structural stop and the retested boundary;
3. the first later completed 1-minute candle is the response candle;
4. it must close beyond the retest candle's favorable extreme before the stop
   or target is touched;
5. entry is the response close, with geometry and minimum gross RR recalculated
   there.  A failed first response ends the episode rather than waiting through
   unrelated chop for an eventual breakout.

The "first response" is an auction event, not a fitted clock timeout.  There is
no score, volatility multiplier, fixed-R target, daily rule or outcome data.
Rejection/rotation footprints and the independent horizontal sweep family are
unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, SetupState, V5TradePlan
from diagonal_core_v20 import DiagonalCoreScenarioEngine
from domain import Candle, Side
from easychart_re1_natural import EasyChartRE1NaturalBundle
from scenario_close_detached_v14 import CLOSE_DETACHED_RETEST_RULE, close_detached


ACCEPTANCE_FIRST_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ACCEPTED_BREAK_FIRST_RETEST_REQUIRES_NEXT_COMPLETED_MICRO_CLOSE_BEYOND_RETEST_EXTREME"
)
if ACCEPTANCE_FIRST_RESPONSE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (ACCEPTANCE_FIRST_RESPONSE_RULE,)


@dataclass(frozen=True, slots=True)
class PendingAcceptanceResponse:
    setup_id: str
    retest_time_ns: int
    retest_high: float
    retest_low: float
    retest_close: float
    stop: float
    trigger_zone: Any


class FirstResponseAcceptanceScenarioEngine(DiagonalCoreScenarioEngine):
    """Diagonal core whose accepted breaks enter only after immediate response."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_acceptance_responses: dict[str, PendingAcceptanceResponse] = {}

    def _finish(
        self,
        setup: ScenarioSetup,
        state: SetupState,
        time_ns: int,
        reason: str,
        **values: Any,
    ) -> None:
        self._pending_acceptance_responses.pop(setup.setup_id, None)
        super()._finish(setup, state, time_ns, reason, **values)

    @staticmethod
    def _response_confirms(
        setup: ScenarioSetup,
        pending: PendingAcceptanceResponse,
        bar: Candle,
    ) -> bool:
        return (
            bar.close > pending.retest_high
            if setup.side is Side.LONG
            else bar.close < pending.retest_low
        )

    @staticmethod
    def _stop_touched(
        setup: ScenarioSetup,
        pending: PendingAcceptanceResponse,
        bar: Candle,
    ) -> bool:
        return (
            bar.low <= pending.stop
            if setup.side is Side.LONG
            else bar.high >= pending.stop
        )

    def _process_pending_response(
        self,
        setup: ScenarioSetup,
        pending: PendingAcceptanceResponse,
        bar: Candle,
    ) -> V5TradePlan | None:
        if bar.ts_close_ns <= pending.retest_time_ns:
            return None

        # The first later completed micro bar is the complete response event.
        # Stop/target touches are evaluated before its close decision because a
        # live order would have traded intrabar.
        if self._target_is_spent(setup, bar):
            self._finish(
                setup,
                SetupState.TARGET_SPENT,
                bar.ts_close_ns,
                "acceptance_target_spent_on_first_response_before_entry",
                retest_time_ns=pending.retest_time_ns,
            )
            return None
        if self._stop_touched(setup, pending, bar):
            self._finish(
                setup,
                SetupState.INVALIDATED,
                bar.ts_close_ns,
                "acceptance_stop_touched_on_first_response_before_entry",
                retest_time_ns=pending.retest_time_ns,
                stop=pending.stop,
                response_low=bar.low,
                response_high=bar.high,
            )
            return None
        if not self._response_confirms(setup, pending, bar):
            self._finish(
                setup,
                SetupState.UNRESOLVED,
                bar.ts_close_ns,
                "acceptance_first_response_failed",
                retest_time_ns=pending.retest_time_ns,
                retest_high=pending.retest_high,
                retest_low=pending.retest_low,
                response_open=bar.open,
                response_high=bar.high,
                response_low=bar.low,
                response_close=bar.close,
                rule_provenance=ACCEPTANCE_FIRST_RESPONSE_RULE,
            )
            return None

        self._pending_acceptance_responses.pop(setup.setup_id, None)
        self._inc("acceptance_first_response_confirmed")
        self._trace(
            "acceptance_first_response_confirmed",
            bar.ts_close_ns,
            setup,
            retest_time_ns=pending.retest_time_ns,
            retest_high=pending.retest_high,
            retest_low=pending.retest_low,
            response_close=bar.close,
            stop=pending.stop,
            rule_provenance=ACCEPTANCE_FIRST_RESPONSE_RULE,
        )
        return self._make_plan(
            setup,
            bar,
            entry=bar.close,
            stop=pending.stop,
            trigger_zone=pending.trigger_zone,
            trigger_kind=pending.trigger_zone.kind,
            trigger_strength=pending.trigger_zone.strength_ratio,
        )

    def _advance_acceptance_retests(self, bar: Candle, index: int) -> list[V5TradePlan]:
        del index
        output: list[V5TradePlan] = []
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST:
                continue
            if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
                continue

            pending = self._pending_acceptance_responses.get(setup.setup_id)
            if pending is not None:
                plan = self._process_pending_response(setup, pending, bar)
                if plan is not None:
                    output.append(plan)
                continue

            if self._target_is_spent(setup, bar):
                self._finish(setup, SetupState.TARGET_SPENT, bar.ts_close_ns, "target_spent_before_entry")
                continue

            _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            if setup.setup_id not in self._detached_setup_ids:
                if close_detached(setup.side, lower, upper, bar):
                    self._detached_setup_ids.add(setup.setup_id)
                    self._inc("acceptance_boundary_close_detached")
                    self._trace(
                        "acceptance_boundary_close_detached",
                        bar.ts_close_ns,
                        setup,
                        detached_bar_low=bar.low,
                        detached_bar_high=bar.high,
                        detached_bar_close=bar.close,
                        projected_lower=lower,
                        projected_upper=upper,
                        provenance=CLOSE_DETACHED_RETEST_RULE,
                    )
                # A departure bar cannot also be its own retest.
                continue

            touched = bar.low <= upper and bar.high >= lower
            if not touched:
                continue
            closes_outside = bar.close > upper if setup.side is Side.LONG else bar.close < lower
            if not closes_outside:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "acceptance_first_detached_retest_failed",
                )
                continue
            stop = self._acceptance_stop(setup, bar.ts_close_ns)
            if stop is None:
                self._finish(setup, SetupState.NO_TRADE_GEOMETRY, bar.ts_close_ns, "acceptance_missing_stop")
                continue
            proxy = self.structure.snapshot_for(setup.context, bar.ts_close_ns)
            self._audit(proxy)
            setup.first_retest_consumed = True
            pending = PendingAcceptanceResponse(
                setup_id=setup.setup_id,
                retest_time_ns=bar.ts_close_ns,
                retest_high=bar.high,
                retest_low=bar.low,
                retest_close=bar.close,
                stop=stop,
                trigger_zone=proxy,
            )
            self._pending_acceptance_responses[setup.setup_id] = pending
            self._inc("acceptance_retest_waiting_first_response")
            self._trace(
                "acceptance_retest_waiting_first_response",
                bar.ts_close_ns,
                setup,
                retest_high=bar.high,
                retest_low=bar.low,
                retest_close=bar.close,
                stop=stop,
                rule_provenance=ACCEPTANCE_FIRST_RESPONSE_RULE,
            )
        return output

    @property
    def reaction_diagnostics(self) -> dict[str, Any]:
        return {
            "pending_at_end": len(self._pending_acceptance_responses),
            "rule_provenance": ACCEPTANCE_FIRST_RESPONSE_RULE,
        }


class EasyChartRE1ReactionBundle(EasyChartRE1NaturalBundle):
    """Natural independent families with first-response accepted-break entry."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = FirstResponseAcceptanceScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["accepted_break_reaction_policy"] = self.micro.reaction_diagnostics
        return output


MultiScaleScenarioBundle = EasyChartRE1ReactionBundle
