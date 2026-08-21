"""Enter an accepted break when its confirming hold bar already performed the retest.

The accepted-break state machine first requires a completed five-minute body
outside a pre-existing boundary and then an immediately following completed
five-minute bar which opens and closes outside.  The prior implementation always
waited for another later one-minute boundary touch after that confirmation.
That is correct when the hold bar remained detached, but it is redundant when
the hold bar itself wicked back into the boundary and closed outside: the first
S/R-flip retest has already completed.

This policy changes only that ownership ambiguity:

* a confirming five-minute hold bar which also touches the projected boundary
  arms an embedded first retest;
* the final completed one-minute bar at the same close timestamp enters at its
  close, after the five-minute confirmation is known;
* the executable stop is beyond both the structural acceptance invalidation and
  the entire completed hold-bar extreme;
* a detached hold bar retains the inherited first-later-retest path.

Targets, minimum gross RR, context routing, one-position arbitration, costs and
continuous NAV accounting are unchanged.  No score, volatility threshold,
session rule, partial exit or fitted parameter is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState, V5TradePlan
from domain import Side
from easychart_re1_flow_ob_responsibility import (
    EasyChartRE1ResponsibleFlowOBBundle,
    ResponsibleFlowValidatedDecisionAreaEngine,
)
from easychart_re1_flow_ob_sweep_responsibility import (
    ResponsibleFlowMajorSwingEngine,
    ResponsiblePhaseFlowMicroEngine,
)
from easychart_re1_natural_geometry import NaturalHorizontalEngine


EMBEDDED_ACCEPTANCE_RETEST_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "WHEN_THE_CONFIRMING_ACCEPTANCE_HOLD_BAR_ITSELF_FIRST_RETESTS_THE_PREEXISTING_BOUNDARY_AND_CLOSES_OUTSIDE_THAT_COMPLETED_BAR_OWNS_THE_RETEST"
)
SAME_TIMESTAMP_COMPLETED_ENTRY_RULE = (
    "CAUSAL_IMPLEMENTATION:"
    "THE_COMPLETED_FIVE_MINUTE_HOLD_IS_PROCESSED_BEFORE_ITS_SAME_TIMESTAMP_FINAL_COMPLETED_ONE_MINUTE_BAR_SO_ENTRY_USES_ONLY_AVAILABLE_INFORMATION"
)
for _rule in (EMBEDDED_ACCEPTANCE_RETEST_RULE, SAME_TIMESTAMP_COMPLETED_ENTRY_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class EmbeddedAcceptanceRetest:
    confirmation_time_ns: int
    hold_open: float
    hold_high: float
    hold_low: float
    hold_close: float
    projected_lower: float
    projected_upper: float


class EmbeddedAcceptanceRetestMixin:
    """Give a completed acceptance hold bar ownership of its own first retest."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._embedded_acceptance_retests: dict[str, EmbeddedAcceptanceRetest] = {}
        self._embedded_acceptance_counts: dict[str, int] = {}

    def _eainc(self, key: str) -> None:
        self._embedded_acceptance_counts[key] = (
            self._embedded_acceptance_counts.get(key, 0) + 1
        )

    def _advance_decision_setups(self, bar: Any, index: int) -> None:
        touched_candidates: dict[str, EmbeddedAcceptanceRetest] = {}
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_ACCEPTANCE_HOLD:
                continue
            expected = (setup.acceptance_break_index or -1) + 1
            if index != expected:
                continue
            _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            touched = (
                bar.low <= upper and bar.high >= lower
            )
            if not touched:
                continue
            touched_candidates[setup.setup_id] = EmbeddedAcceptanceRetest(
                confirmation_time_ns=bar.ts_close_ns,
                hold_open=bar.open,
                hold_high=bar.high,
                hold_low=bar.low,
                hold_close=bar.close,
                projected_lower=lower,
                projected_upper=upper,
            )

        super()._advance_decision_setups(bar, index)

        for setup_id, embedded in touched_candidates.items():
            setup = self._active.get(setup_id)
            if (
                setup is None
                or setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST
                or setup.confirmation_time_ns != bar.ts_close_ns
            ):
                self._eainc("touch_did_not_become_confirmed_acceptance")
                continue
            self._embedded_acceptance_retests[setup_id] = embedded
            self._eainc("embedded_acceptance_retest_armed")
            self._trace(
                "embedded_acceptance_retest_armed",
                bar.ts_close_ns,
                setup,
                hold_open=embedded.hold_open,
                hold_high=embedded.hold_high,
                hold_low=embedded.hold_low,
                hold_close=embedded.hold_close,
                projected_lower=embedded.projected_lower,
                projected_upper=embedded.projected_upper,
                rule_provenance=(
                    EMBEDDED_ACCEPTANCE_RETEST_RULE,
                    SAME_TIMESTAMP_COMPLETED_ENTRY_RULE,
                ),
            )

    def _advance_embedded_acceptance_retests(
        self,
        bar: Any,
    ) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for setup_id, embedded in list(self._embedded_acceptance_retests.items()):
            setup = self._active.get(setup_id)
            if setup is None:
                self._embedded_acceptance_retests.pop(setup_id, None)
                self._eainc("embedded_setup_cleared_before_trigger")
                continue
            if bar.ts_close_ns < embedded.confirmation_time_ns:
                continue
            if bar.ts_close_ns > embedded.confirmation_time_ns:
                # The same-close one-minute bar was unavailable.  Do not invent
                # an entry later; hand ownership back to the inherited retest.
                self._embedded_acceptance_retests.pop(setup_id, None)
                self._eainc("same_timestamp_trigger_missing_fell_back_to_later_retest")
                continue
            self._embedded_acceptance_retests.pop(setup_id, None)
            if setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST:
                self._eainc("embedded_setup_state_changed_before_trigger")
                continue
            if self._target_is_spent(setup, bar):
                self._eainc("embedded_target_spent")
                self._finish(
                    setup,
                    SetupState.TARGET_SPENT,
                    bar.ts_close_ns,
                    "target_spent_before_embedded_acceptance_entry",
                )
                continue

            projected, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
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
            plan = self._make_plan(
                setup,
                bar,
                entry=bar.close,
                stop=stop,
                trigger_zone=proxy,
                trigger_kind=proxy.kind,
                trigger_strength=proxy.strength_ratio,
            )
            if plan is None:
                self._eainc("embedded_acceptance_geometry_rejected")
                continue
            output.append(plan)
            self._eainc("embedded_acceptance_plan_created")
            self._trace(
                "embedded_acceptance_plan_created",
                bar.ts_close_ns,
                setup,
                plan_id=plan.plan_id,
                entry=plan.entry,
                stop=plan.stop,
                target=plan.target,
                gross_rr=plan.gross_rr,
                hold_open=embedded.hold_open,
                hold_high=embedded.hold_high,
                hold_low=embedded.hold_low,
                hold_close=embedded.hold_close,
                projected_lower=lower,
                projected_upper=upper,
                projected_member_ids=[item.source_structure_id for item in projected],
                rule_provenance=(
                    EMBEDDED_ACCEPTANCE_RETEST_RULE,
                    SAME_TIMESTAMP_COMPLETED_ENTRY_RULE,
                ),
            )
        return output

    def _advance_acceptance_retests(
        self,
        bar: Any,
        index: int,
    ) -> list[V5TradePlan]:
        embedded = self._advance_embedded_acceptance_retests(bar)
        inherited = super()._advance_acceptance_retests(bar, index)
        unique = {plan.plan_id: plan for plan in embedded + inherited}
        return sorted(
            unique.values(),
            key=lambda plan: (
                plan.interaction_time_ns,
                plan.observed_time_ns,
                plan.plan_id,
            ),
        )

    @property
    def embedded_acceptance_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._embedded_acceptance_counts.items())),
            "armed": len(self._embedded_acceptance_retests),
            "rules": (
                EMBEDDED_ACCEPTANCE_RETEST_RULE,
                SAME_TIMESTAMP_COMPLETED_ENTRY_RULE,
            ),
        }


class EmbeddedResponsiblePhaseFlowMicroEngine(
    EmbeddedAcceptanceRetestMixin,
    ResponsiblePhaseFlowMicroEngine,
):
    pass


class EmbeddedNaturalHorizontalEngine(
    EmbeddedAcceptanceRetestMixin,
    NaturalHorizontalEngine,
):
    pass


class EmbeddedResponsibleFlowMajorSwingEngine(
    EmbeddedAcceptanceRetestMixin,
    ResponsibleFlowMajorSwingEngine,
):
    pass


class EmbeddedResponsibleFlowValidatedDecisionAreaEngine(
    EmbeddedAcceptanceRetestMixin,
    ResponsibleFlowValidatedDecisionAreaEngine,
):
    pass


class EasyChartRE1EmbeddedAcceptanceBundle(EasyChartRE1ResponsibleFlowOBBundle):
    """Responsible rejection core plus causally completed embedded acceptance."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = EmbeddedResponsiblePhaseFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = EmbeddedNaturalHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = EmbeddedResponsibleFlowMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = EmbeddedResponsibleFlowValidatedDecisionAreaEngine(
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

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["embedded_acceptance_retest"] = {
            "micro": self.micro.embedded_acceptance_diagnostics,
            "horizontal": self.horizontal.embedded_acceptance_diagnostics,
            "major_swing": self.major_swing.embedded_acceptance_diagnostics,
            "flow_decision_ob": self.flow_decision_ob.embedded_acceptance_diagnostics,
            "rules": (
                EMBEDDED_ACCEPTANCE_RETEST_RULE,
                SAME_TIMESTAMP_COMPLETED_ENTRY_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1EmbeddedAcceptanceBundle
