"""First exact retest entry for a held multi-structure S/R flip.

The five-minute breakout bar and next five-minute hold already provide the
required departure from the cleared resistance/support stack.  Requiring a new
one-minute close-detach, return and second response after that hold repeats the
same responsibility and often lets the first opposing objective be spent.

After the held same-body structure-stack break, this policy gives the first
later one-minute touch one decision.  It enters at that completed close only
when the whole stack still holds and either:

* a high-quality event-local engulfing OB is completed at the retest; or
* current causal aggressor flow confirms initiative/absorption at the retest.

A first touch which closes back through the stack or has neither evidence ends
the episode.  Stop remains beyond the retest extreme and causal breakout origin;
target remains the first pre-entry obstacle.  No timeout, score or fitted price
tolerance is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, SetupState, V5TradePlan
from domain import Candle, Side
from easychart_re1_confluence_flip import CONFLUENCE_FLIP_RULE
from easychart_re1_confluence_flip_stack import (
    BreakoutStackConfluenceAcceptanceEngine,
    EasyChartRE1BreakoutStackConfluenceBundle,
)
from easychart_zones import PriceZone, ZoneKind, ZoneSide


CONFLUENCE_FIRST_EXACT_RETEST_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "AFTER_A_BODY_BREAK_AND_NEXT_BAR_HOLD_OF_A_MULTI_STRUCTURE_STACK_THE_FIRST_LATER_EXACT_RETEST_MAY_ENTER_ON_A_COMPLETED_STRONG_OB_OR_CAUSAL_FLOW_RESPONSE"
)
if CONFLUENCE_FIRST_EXACT_RETEST_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CONFLUENCE_FIRST_EXACT_RETEST_RULE,)


class DirectRetestConfluenceAcceptanceEngine(BreakoutStackConfluenceAcceptanceEngine):
    """Consume the first held-stack retest without a redundant detach cycle."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._direct_retest_counts: dict[str, int] = {}

    def _rinc(self, key: str) -> None:
        self._direct_retest_counts[key] = self._direct_retest_counts.get(key, 0) + 1

    @staticmethod
    def _wanted_zone_side(setup: ScenarioSetup) -> ZoneSide:
        return ZoneSide.SUPPORT if setup.side is Side.LONG else ZoneSide.RESISTANCE

    def _current_strong_ob(
        self,
        setup: ScenarioSetup,
        bar: Candle,
    ) -> PriceZone | None:
        wanted = self._wanted_zone_side(setup)
        candidates = [
            zone
            for zone in self.trigger_detector.zones
            if zone.observed_time_ns == bar.ts_close_ns
            and zone.kind is ZoneKind.ORDER_BLOCK
            and zone.side is wanted
            and zone.high_quality_by_size
            and self._formation_touches_context(zone, setup)
        ]
        return self._select_footprint(candidates, setup)

    def _advance_acceptance_retests(self, bar: Candle, index: int) -> list[V5TradePlan]:
        del index
        output: list[V5TradePlan] = []
        observation = self._flow_current
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST:
                continue
            if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
                continue
            if self._target_is_spent(setup, bar):
                self._finish(
                    setup,
                    SetupState.TARGET_SPENT,
                    bar.ts_close_ns,
                    "confluence_target_spent_before_first_exact_retest",
                )
                continue

            _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            touched = bar.low <= upper and bar.high >= lower
            if not touched:
                continue
            if setup.first_retest_consumed:
                raise RuntimeError("confluence first exact retest processed twice")
            setup.first_retest_consumed = True
            holds = bar.close > upper if setup.side is Side.LONG else bar.close < lower
            if not holds:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "confluence_first_exact_retest_closed_through_stack",
                    retest_open=bar.open,
                    retest_high=bar.high,
                    retest_low=bar.low,
                    retest_close=bar.close,
                    stack_lower=lower,
                    stack_upper=upper,
                    rule_provenance=CONFLUENCE_FIRST_EXACT_RETEST_RULE,
                )
                continue

            trigger = self._current_strong_ob(setup, bar)
            signal = self._flow_signal(setup, bar, observation)
            if trigger is None and signal is None:
                self._finish(
                    setup,
                    SetupState.UNRESOLVED,
                    bar.ts_close_ns,
                    "confluence_first_exact_retest_lacked_response_evidence",
                    retest_close=bar.close,
                    stack_lower=lower,
                    stack_upper=upper,
                    rule_provenance=CONFLUENCE_FIRST_EXACT_RETEST_RULE,
                )
                continue

            stop = self._acceptance_stop(setup, bar.ts_close_ns)
            if stop is None:
                self._finish(
                    setup,
                    SetupState.NO_TRADE_GEOMETRY,
                    bar.ts_close_ns,
                    "confluence_first_exact_retest_missing_stop",
                )
                continue
            if trigger is None:
                proxy = self._flow_proxy(setup, bar.ts_close_ns)
                trigger_zone = proxy
                trigger_kind = signal.kind
                trigger_strength = signal.strength
                evidence = "FLOW"
            else:
                trigger_zone = trigger
                trigger_kind = trigger.kind
                trigger_strength = trigger.strength_ratio
                evidence = "STRONG_ORDER_BLOCK"
            self._audit(trigger_zone)
            plan = self._make_plan(
                setup,
                bar,
                entry=bar.close,
                stop=stop,
                trigger_zone=trigger_zone,
                trigger_kind=trigger_kind,
                trigger_strength=trigger_strength,
            )
            if plan is None:
                self._rinc("confluence_direct_retest_geometry_rejected")
                continue
            output.append(plan)
            self._rinc("confluence_direct_retest_plan_created")
            self._trace(
                "confluence_direct_retest_plan_created",
                bar.ts_close_ns,
                setup,
                plan_id=plan.plan_id,
                evidence=evidence,
                entry=plan.entry,
                stop=plan.stop,
                target=plan.target,
                gross_rr=plan.gross_rr,
                rule_provenance=(
                    CONFLUENCE_FLIP_RULE,
                    CONFLUENCE_FIRST_EXACT_RETEST_RULE,
                ),
            )
        return output

    @property
    def direct_retest_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._direct_retest_counts.items())),
            "rule_provenance": CONFLUENCE_FIRST_EXACT_RETEST_RULE,
        }


class EasyChartRE1DirectConfluenceBundle(EasyChartRE1BreakoutStackConfluenceBundle):
    """Reversal/OB account plus direct first-retest multi-structure flips."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.confluence_flip = DirectRetestConfluenceAcceptanceEngine(
            symbol,
            tick_size,
            scale_name="CONFLUENCE_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["confluence_flip"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["confluence_direct_first_retest"] = {
            "engine": self.confluence_flip.direct_retest_diagnostics,
            "rule_provenance": CONFLUENCE_FIRST_EXACT_RETEST_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DirectConfluenceBundle
