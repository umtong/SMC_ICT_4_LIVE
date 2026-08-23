"""Retest-bound causal-flow core for EasyChart RE1.

Initiative is useful only after an accepted boundary has actually been left and
returned to. The first flow candidate entered on any later high-volume aligned
bar while the setup was waiting for a retest, which converted continuation
proof into breakout chasing. This version lets initiative replace the next
visual response candle only on the first completed boundary retest itself.

Rejection/bounce/rotation paths still use opposing-aggression absorption at the
meaningful boundary. The system remains the small MICRO/HORIZONTAL/LIQUIDITY
core with ordinary OB/FVG and exact-retest entries as an OR branch.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, SetupState, V5TradePlan
from domain import Candle
from easychart_re1_flow import FlowEntryMixin
from easychart_re1_flow_mechanism import MechanismFlowEntryMixin
from easychart_re1_natural_geometry import (
    EasyChartRE1NaturalGeometryBundle,
    NaturalHorizontalEngine,
    NaturalMajorSwingEngine,
    NaturalMicroEngine,
)


FLOW_RETEST_RESPONSIBILITY_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "INITIATIVE_SUBSTITUTES_FOR_THE_NEXT_VISUAL_RESPONSE_ONLY_ON_THE_FIRST_COMPLETED_ACCEPTED_BOUNDARY_RETEST"
)
if FLOW_RETEST_RESPONSIBILITY_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (FLOW_RETEST_RESPONSIBILITY_RULE,)


class RetestMechanismFlowEntryMixin(MechanismFlowEntryMixin):
    """Bind initiative to an observed accepted-break retest, never a chase bar."""

    def _advance_acceptance_retests(
        self,
        bar: Candle,
        index: int,
    ) -> list[V5TradePlan]:
        # Skip FlowEntryMixin's unrestricted waiting-state initiative branch and
        # run the original accepted-break retest/response state machine first.
        output = super(FlowEntryMixin, self)._advance_acceptance_retests(bar, index)
        observation = self._flow_current
        if observation is None:
            self._finc("acceptance_retest_flow_missing_bar_data")
            return output

        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST:
                continue
            if setup.path is not ScenarioPath.ACCEPTANCE:
                continue
            if not setup.first_retest_consumed:
                # The current bar may be the detached departure, but it is not
                # yet the promised S/R-flip return.
                continue
            if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
                continue

            _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            touched = bar.low <= upper and bar.high >= lower
            if not touched:
                continue
            closes_outside = bar.close > upper if setup.side.name == "LONG" else bar.close < lower
            if not closes_outside:
                continue

            signal = self._flow_signal(setup, bar, observation)
            if signal is None:
                self._finc("acceptance_first_retest_without_initiative")
                continue
            plan = self._flow_plan(setup, bar, signal, observation)
            if plan is not None:
                pending = getattr(self, "_pending_acceptance_responses", None)
                if pending is not None:
                    pending.pop(setup.setup_id, None)
                self._finc("acceptance_first_retest_initiative_entry")
                output.append(plan)
        return output

    @property
    def retest_flow_diagnostics(self) -> dict[str, Any]:
        return {
            "policy": "FIRST_COMPLETED_ACCEPTED_BOUNDARY_RETEST_ONLY",
            "rule_provenance": FLOW_RETEST_RESPONSIBILITY_RULE,
        }


class RetestFlowCoreMicroEngine(RetestMechanismFlowEntryMixin, NaturalMicroEngine):
    pass


class RetestFlowCoreHorizontalEngine(
    RetestMechanismFlowEntryMixin,
    NaturalHorizontalEngine,
):
    pass


class RetestFlowCoreMajorSwingEngine(
    RetestMechanismFlowEntryMixin,
    NaturalMajorSwingEngine,
):
    pass


class EasyChartRE1RetestFlowCoreBundle(EasyChartRE1NaturalGeometryBundle):
    """Three natural structure families with retest-bound flow substitution."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = RetestFlowCoreMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = RetestFlowCoreHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = RetestFlowCoreMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "horizontal", "major_swing"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["retest_bound_causal_flow_core"] = {
            "families": ("MICRO", "HORIZONTAL", "LIQUIDITY"),
            "micro": self.micro.retest_flow_diagnostics,
            "horizontal": self.horizontal.retest_flow_diagnostics,
            "major_swing": self.major_swing.retest_flow_diagnostics,
            "rule_provenance": FLOW_RETEST_RESPONSIBILITY_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1RetestFlowCoreBundle
