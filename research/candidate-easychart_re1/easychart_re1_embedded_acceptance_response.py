"""Response-confirmed accepted breaks with the first transfer-wave objective.

An accepted break has two causal events: the completed decision-frame hold which
proves price can remain outside the old boundary, and the lower-frame response
which proves that boundary is defended.  The hold may itself contain the first
retest; otherwise the inherited detached first-return path remains available.

The first objective is also part of this same auction.  Before an accepted
transfer can seek a distant channel extension or coarse pivot it must first
retake the extreme produced by its completed break-and-hold wave.  That extreme
is already visible before entry, is the source material's "wave end", and is a
natural nearby liquidity objective.  If the response candle already trades it,
the objective is spent; if it leaves less than 1R, the plan is rejected rather
than manufacturing a distant target.

No fitted R cap, score, ATR rule, clock timeout, session filter, partial exit or
post-entry target movement is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState, StructureFamily, StructureZone, V5TradePlan
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
from easychart_zones import ZoneSide


EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "AN_ACCEPTANCE_HOLD_BAR_WHICH_ALREADY_RETESTED_THE_BOUNDARY_REQUIRES_THE_FIRST_LATER_COMPLETED_MICRO_CLOSE_BEYOND_ITS_FINAL_MICRO_RETEST_EXTREME"
)
ACCEPTANCE_TRANSFER_WAVE_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_FIRST_ACCEPTED_TRANSFER_OBJECTIVE_IS_THE_"
    "UNRETAKEN_EXTREME_OF_THE_COMPLETED_BREAK_AND_REQUIRED_HOLD_WAVE_BEFORE_A_"
    "MORE_DISTANT_CHANNEL_EXTENSION_OR_OPPOSING_STRUCTURE"
)
for _rule in (
    EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE,
    ACCEPTANCE_TRANSFER_WAVE_OBJECTIVE_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class AcceptanceTransferObjectiveKind(str, Enum):
    BREAK_HOLD_WAVE_HIGH = "BREAK_HOLD_WAVE_HIGH"
    BREAK_HOLD_WAVE_LOW = "BREAK_HOLD_WAVE_LOW"


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
    """Delay embedded entry until response and use its first wave objective."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_embedded_acceptance_responses: dict[
            str,
            PendingEmbeddedAcceptanceResponse,
        ] = {}
        self._acceptance_wave_objective_counts: dict[str, int] = {}

    def _awinc(self, key: str) -> None:
        self._acceptance_wave_objective_counts[key] = (
            self._acceptance_wave_objective_counts.get(key, 0) + 1
        )

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

    def _accepted_wave_bars(self, setup: Any) -> list[Any]:
        index = setup.acceptance_break_index
        if index is None or not 0 <= index < len(self.decision_bars):
            return []
        output = [self.decision_bars[index]]
        confirmation = setup.confirmation_time_ns
        if confirmation is not None:
            hold = next(
                (
                    item
                    for item in self.decision_bars[index + 1 :]
                    if item.ts_close_ns == confirmation
                ),
                None,
            )
            if hold is not None:
                output.append(hold)
        return output

    def _refine_acceptance_wave_objective(self, setup: Any, bar: Any) -> bool:
        wave = self._accepted_wave_bars(setup)
        if not wave or setup.target_price is None:
            self._awinc("accepted_wave_geometry_missing")
            return True
        if setup.side is Side.LONG:
            price = max(item.high for item in wave)
            unspent = price > bar.high
            closer = price < setup.target_price
            kind = AcceptanceTransferObjectiveKind.BREAK_HOLD_WAVE_HIGH
            zone_side = ZoneSide.RESISTANCE
        else:
            price = min(item.low for item in wave)
            unspent = price < bar.low
            closer = price > setup.target_price
            kind = AcceptanceTransferObjectiveKind.BREAK_HOLD_WAVE_LOW
            zone_side = ZoneSide.SUPPORT
        if not unspent:
            self._awinc("accepted_transfer_wave_spent_before_entry")
            self._finish(
                setup,
                SetupState.TARGET_SPENT,
                bar.ts_close_ns,
                "accepted_transfer_wave_spent_before_entry",
                transfer_wave_price=price,
                response_high=bar.high,
                response_low=bar.low,
                rule_provenance=ACCEPTANCE_TRANSFER_WAVE_OBJECTIVE_RULE,
            )
            return False
        if not closer:
            self._awinc("existing_objective_already_before_transfer_wave")
            return True

        source = f"ACCEPTED_TRANSFER_WAVE:{setup.setup_id}:{kind.value}"
        zone = StructureZone(
            zone_id=f"{source}:SNAP:{bar.ts_close_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=zone_side,
            timeframe_minutes=self.decision_minutes,
            lower=price - self.tick_size * 0.5,
            upper=price + self.tick_size * 0.5,
            invalidation=(
                price + self.tick_size
                if setup.side is Side.LONG
                else price - self.tick_size
            ),
            impulse_extreme=price,
            formed_index=setup.acceptance_break_index or 0,
            formed_time_ns=wave[0].ts_close_ns,
            observed_time_ns=setup.confirmation_time_ns or wave[-1].ts_close_ns,
            formation_indices=(),
            strength_ratio=setup.context.strength_ratio,
            source_structure_id=source,
            source_pivot_span=max(1, setup.context.source_pivot_span),
        )
        previous_zone = None if setup.target_zone is None else setup.target_zone.zone_id
        previous_price = setup.target_price
        setup.target_zone = zone
        setup.target_price = price
        self._audit(zone)
        self._awinc("objective_replaced_by_accepted_transfer_wave")
        self._trace(
            "accepted_transfer_wave_objective_selected",
            bar.ts_close_ns,
            setup,
            previous_target_zone_id=previous_zone,
            previous_target_price=previous_price,
            selected_target_zone_id=zone.zone_id,
            selected_target_price=price,
            break_time_ns=wave[0].ts_close_ns,
            hold_time_ns=wave[-1].ts_close_ns,
            rule_provenance=ACCEPTANCE_TRANSFER_WAVE_OBJECTIVE_RULE,
        )
        return True

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
        if not self._refine_acceptance_wave_objective(setup, bar):
            return None
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
            rule_provenance=(
                EMBEDDED_ACCEPTANCE_FIRST_RESPONSE_RULE,
                ACCEPTANCE_TRANSFER_WAVE_OBJECTIVE_RULE,
            ),
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
                    ACCEPTANCE_TRANSFER_WAVE_OBJECTIVE_RULE,
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
                "transfer_wave_objective_counts": dict(
                    sorted(self._acceptance_wave_objective_counts.items())
                ),
                "transfer_wave_objective_rule": (
                    ACCEPTANCE_TRANSFER_WAVE_OBJECTIVE_RULE
                ),
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
                ACCEPTANCE_TRANSFER_WAVE_OBJECTIVE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1EmbeddedAcceptanceResponseBundle
