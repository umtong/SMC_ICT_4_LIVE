"""Immediate response confirmation for every footprint-retest entry in RE1.

The mechanism-selective candidate removed weak countertrend, rotation and
standalone-FVG responsibilities.  Its remaining losses exposed one final
translation gap in rejection entries: the first detached retest candle was
allowed to submit an order even when the very next completed micro candle
closed back inside the OB/FVG.  A human chart trader sees that as failure of the
claimed support/resistance response, not as a reason to keep the original
order alive.

For a footprint-retest setup this module therefore separates two observable
events:

1. the first detached return touches the pre-existing event-local footprint and
   reacts on the valid side; this fixes the original structural stop;
2. the first later completed 1-minute candle must still close beyond the
   footprint's favorable edge.  Only then is the full position entered at that
   response close.

A stop or target touch before the response close means no executable trade.  A
first response which closes back into the footprint ends the causal episode.
This is the footprint analogue of the accepted-break first-response policy; it
uses no clock timeout, score, volatility threshold, fitted R level or future
outcome.  Target geometry and minimum gross RR are recomputed at the actual
response entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, SetupState, V5TradePlan
from domain import Candle, Side
from easychart_re1_selective import (
    EasyChartRE1SelectiveBundle,
    SelectiveFirstResponseScenarioEngine,
    SelectiveRepeatedDefenseScenarioEngine,
)


FOOTPRINT_FIRST_RESPONSE_HOLD_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIRST_DETACHED_OB_FVG_RETEST_REQUIRES_FIRST_LATER_MICRO_CLOSE_TO_HOLD_BEYOND_THE_FAVORABLE_ZONE_EDGE"
)
if FOOTPRINT_FIRST_RESPONSE_HOLD_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (FOOTPRINT_FIRST_RESPONSE_HOLD_RULE,)


@dataclass(frozen=True, slots=True)
class PendingFootprintResponse:
    setup_id: str
    retest_time_ns: int
    retest_high: float
    retest_low: float
    retest_close: float
    stop: float
    trigger_zone: Any


class FirstResponseFootprintMixin:
    """Delay a footprint-retest plan until its first completed response holds."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_footprint_responses: dict[str, PendingFootprintResponse] = {}

    def _finish(
        self,
        setup: ScenarioSetup,
        state: SetupState,
        time_ns: int,
        reason: str,
        **values: Any,
    ) -> None:
        self._pending_footprint_responses.pop(setup.setup_id, None)
        super()._finish(setup, state, time_ns, reason, **values)

    @staticmethod
    def _response_holds(
        setup: ScenarioSetup,
        pending: PendingFootprintResponse,
        bar: Candle,
    ) -> bool:
        trigger = pending.trigger_zone
        return (
            bar.close > trigger.upper
            if setup.side is Side.LONG
            else bar.close < trigger.lower
        )

    @staticmethod
    def _pending_stop_touched(
        setup: ScenarioSetup,
        pending: PendingFootprintResponse,
        bar: Candle,
    ) -> bool:
        return (
            bar.low <= pending.stop
            if setup.side is Side.LONG
            else bar.high >= pending.stop
        )

    def _process_footprint_response(
        self,
        setup: ScenarioSetup,
        pending: PendingFootprintResponse,
        bar: Candle,
    ) -> V5TradePlan | None:
        if bar.ts_close_ns <= pending.retest_time_ns:
            return None
        if self._target_is_spent(setup, bar):
            self._finish(
                setup,
                SetupState.TARGET_SPENT,
                bar.ts_close_ns,
                "footprint_target_spent_on_first_response_before_entry",
                retest_time_ns=pending.retest_time_ns,
            )
            return None
        if self._pending_stop_touched(setup, pending, bar):
            self._finish(
                setup,
                SetupState.INVALIDATED,
                bar.ts_close_ns,
                "footprint_stop_touched_on_first_response_before_entry",
                retest_time_ns=pending.retest_time_ns,
                stop=pending.stop,
                response_low=bar.low,
                response_high=bar.high,
            )
            return None
        if not self._response_holds(setup, pending, bar):
            self._finish(
                setup,
                SetupState.UNRESOLVED,
                bar.ts_close_ns,
                "footprint_first_response_failed_to_hold_zone",
                retest_time_ns=pending.retest_time_ns,
                trigger_zone_id=pending.trigger_zone.zone_id,
                trigger_lower=pending.trigger_zone.lower,
                trigger_upper=pending.trigger_zone.upper,
                response_open=bar.open,
                response_high=bar.high,
                response_low=bar.low,
                response_close=bar.close,
                rule_provenance=FOOTPRINT_FIRST_RESPONSE_HOLD_RULE,
            )
            return None

        self._pending_footprint_responses.pop(setup.setup_id, None)
        self._inc("footprint_first_response_confirmed")
        self._trace(
            "footprint_first_response_confirmed",
            bar.ts_close_ns,
            setup,
            retest_time_ns=pending.retest_time_ns,
            retest_high=pending.retest_high,
            retest_low=pending.retest_low,
            retest_close=pending.retest_close,
            response_close=bar.close,
            stop=pending.stop,
            trigger_zone_id=pending.trigger_zone.zone_id,
            rule_provenance=FOOTPRINT_FIRST_RESPONSE_HOLD_RULE,
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

    def _advance_footprint_retests(self, bar: Candle, index: int) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_FOOTPRINT_RETEST:
                continue
            trigger = setup.trigger_zone
            if trigger is None or setup.trigger_index is None:
                raise RuntimeError("footprint setup lost trigger")

            pending = self._pending_footprint_responses.get(setup.setup_id)
            if pending is not None:
                plan = self._process_footprint_response(setup, pending, bar)
                if plan is not None:
                    output.append(plan)
                continue

            if self._target_is_spent(setup, bar):
                self._finish(setup, SetupState.TARGET_SPENT, bar.ts_close_ns, "target_spent_before_entry")
                continue
            trigger_invalidated = (
                bar.low <= trigger.invalidation
                if setup.side is Side.LONG
                else bar.high >= trigger.invalidation
            )
            if index > setup.trigger_index and trigger_invalidated:
                trigger.invalidated_index = index
                trigger.invalidated_time_ns = bar.ts_close_ns
                self._finish(
                    setup,
                    SetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "trigger_footprint_invalidated_before_detached_retest",
                )
                continue
            if self._extreme_breached(setup, bar):
                self._finish(
                    setup,
                    SetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "interaction_extreme_breached_before_detached_retest",
                )
                continue
            if index <= setup.trigger_index:
                continue

            if setup.setup_id not in self._detached_setup_ids:
                detached = (
                    bar.close > trigger.upper
                    if setup.side is Side.LONG
                    else bar.close < trigger.lower
                )
                if detached:
                    self._detached_setup_ids.add(setup.setup_id)
                    self._inc("footprint_close_detached")
                    self._trace(
                        "footprint_close_detached",
                        bar.ts_close_ns,
                        setup,
                        trigger_zone_id=trigger.zone_id,
                        detached_bar_low=bar.low,
                        detached_bar_high=bar.high,
                        detached_bar_close=bar.close,
                    )
                # A departure candle cannot also be its own return.
                continue

            touched = bar.low <= trigger.upper and bar.high >= trigger.lower
            if not touched:
                continue
            if setup.first_retest_consumed:
                raise RuntimeError("first detached footprint retest processed twice")
            setup.first_retest_consumed = True
            trigger.first_touch_index = index
            trigger.first_touch_time_ns = bar.ts_close_ns
            if setup.side is Side.LONG:
                reacted = bar.close > trigger.upper and bar.close > bar.open
                stop = min(setup.interaction_extreme - self.tick_size, trigger.invalidation)
            else:
                reacted = bar.close < trigger.lower and bar.close < bar.open
                stop = max(setup.interaction_extreme + self.tick_size, trigger.invalidation)
            if not reacted:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "first_detached_footprint_retest_failed",
                )
                continue

            self._pending_footprint_responses[setup.setup_id] = PendingFootprintResponse(
                setup_id=setup.setup_id,
                retest_time_ns=bar.ts_close_ns,
                retest_high=bar.high,
                retest_low=bar.low,
                retest_close=bar.close,
                stop=stop,
                trigger_zone=trigger,
            )
            self._inc("footprint_retest_waiting_first_response")
            self._trace(
                "footprint_retest_waiting_first_response",
                bar.ts_close_ns,
                setup,
                trigger_zone_id=trigger.zone_id,
                retest_high=bar.high,
                retest_low=bar.low,
                retest_close=bar.close,
                stop=stop,
                rule_provenance=FOOTPRINT_FIRST_RESPONSE_HOLD_RULE,
            )
        return output

    @property
    def footprint_response_diagnostics(self) -> dict[str, Any]:
        return {
            "pending_at_end": len(self._pending_footprint_responses),
            "rule_provenance": FOOTPRINT_FIRST_RESPONSE_HOLD_RULE,
        }


class ConfirmedSelectiveScenarioEngine(
    FirstResponseFootprintMixin,
    SelectiveFirstResponseScenarioEngine,
):
    """Diagonal accepted breaks and rejections both require immediate response."""


class ConfirmedRepeatedDefenseScenarioEngine(
    FirstResponseFootprintMixin,
    SelectiveRepeatedDefenseScenarioEngine,
):
    """Repeated-defense sweeps require the same immediate footprint hold."""


class EasyChartRE1ConfirmedBundle(EasyChartRE1SelectiveBundle):
    """Mechanism-selective plan stream with response-confirmed entries."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = ConfirmedSelectiveScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = ConfirmedRepeatedDefenseScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0
        self._audit_offsets["horizontal"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["footprint_first_response_policy"] = {
            "micro": self.micro.footprint_response_diagnostics,
            "horizontal": self.horizontal.footprint_response_diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ConfirmedBundle
