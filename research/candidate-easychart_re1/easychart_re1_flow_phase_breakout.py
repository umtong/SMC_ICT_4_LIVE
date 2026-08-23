"""Ordered-channel phase plus volume-confirmed accepted-break execution.

The source distinguishes two valid entries after a structure breaks: the
conservative first retest and the direct strong-break continuation.  The latter
was previously left as a verbal word ("strong") and then either suppressed or
implemented as an unrestricted later chase.

Here a direct continuation can enter only when the exact decision-bar break and
its required next-bar hold form one causal auction with aligned active taker
flow and material price progress.  The plan is made at the completed hold bar,
uses the existing structural stop and first-obstacle target, and must still
clear 1R before costs.  Rejection flow and every visual OB/FVG/retest path remain
unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, SetupState, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow import FlowSignal, FlowTriggerKind
from easychart_re1_flow_phase import (
    EasyChartRE1PhaseFlowBundle,
    PhaseFocusedFlowMicroEngine,
)


DIRECT_HOLD_INITIATIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "STRONG_BREAKOUT_MEANS_ALIGNED_ACTIVE_TAKER_FLOW_AND_PRICE_PROGRESS_ACROSS_THE_BREAK_BAR_AND_REQUIRED_NEXT_BAR_HOLD"
)
if DIRECT_HOLD_INITIATIVE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (DIRECT_HOLD_INITIATIVE_RULE,)


class DirectHoldInitiativeMixin:
    """Create one immutable plan on the confirmed hold, never a later chase."""

    def _hold_initiative_signal(self, setup: Any, bar: Candle) -> FlowSignal | None:
        break_index = setup.acceptance_break_index
        if break_index is None or not 0 <= break_index < len(self.decision_bars):
            return None
        break_bar = self.decision_bars[break_index]
        start_ns = break_bar.ts_close_ns - self.decision_minutes * 60 * 1_000_000_000
        episode = [
            item
            for item in self.flow_analyzer.history
            if start_ns < item.ts_close_ns <= bar.ts_close_ns
        ]
        if not episode:
            return None
        aligned = [
            item
            for item in episode
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned_delta(setup.side, item.signed_taker_quote)
            and (item.body > 0.0 if setup.side is Side.LONG else item.body < 0.0)
        ]
        if not aligned:
            return None
        cumulative_delta = sum(item.signed_taker_quote for item in episode)
        if not self._aligned_delta(setup.side, cumulative_delta):
            return None
        net_progress = self._intended_progress(
            setup.side,
            episode[0].open,
            episode[-1].close,
        )
        if net_progress <= 0.0:
            return None
        strongest = max(
            aligned,
            key=lambda item: (
                item.activity_ratio * item.delta_ratio * item.body_ratio,
                item.ts_close_ns,
            ),
        )
        return FlowSignal(
            kind=(
                FlowTriggerKind.BUY_INITIATIVE
                if setup.side is Side.LONG
                else FlowTriggerKind.SELL_INITIATIVE
            ),
            mechanism="DECISION_BREAK_AND_REQUIRED_HOLD_INITIATIVE",
            strength=(
                strongest.activity_ratio
                * strongest.delta_ratio
                * strongest.body_ratio
            ),
            observation=strongest,
            episode_bars=len(episode),
            cumulative_signed_taker_quote=cumulative_delta,
            net_price_progress=net_progress,
        )

    def _direct_hold_plans(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for setup in list(self._active.values()):
            if setup.path is not ScenarioPath.ACCEPTANCE:
                continue
            if setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST:
                continue
            if setup.confirmation_time_ns != bar.ts_close_ns:
                continue
            signal = self._hold_initiative_signal(setup, bar)
            if signal is None:
                self._finc("confirmed_hold_without_coherent_initiative")
                continue
            if self._target_is_spent(setup, bar):
                continue
            prior = self._current_trigger_bar
            self._current_trigger_bar = bar
            try:
                stop = self._acceptance_stop(setup, bar.ts_close_ns)
                if stop is None:
                    continue
                proxy = self.structure.snapshot_for(setup.context, bar.ts_close_ns)
                self._audit(proxy)
                plan = self._make_plan(
                    setup,
                    bar,
                    entry=bar.close,
                    stop=stop,
                    trigger_zone=proxy,
                    trigger_kind=signal.kind,
                    trigger_strength=signal.strength,
                )
            finally:
                self._current_trigger_bar = prior
            if plan is None:
                self._finc("direct_hold_initiative_geometry_rejected")
                continue
            self._finc("direct_hold_initiative_plan_created")
            self._trace(
                "direct_hold_initiative_plan_created",
                bar.ts_close_ns,
                setup,
                plan_id=plan.plan_id,
                flow_kind=signal.kind.value,
                flow_mechanism=signal.mechanism,
                flow_episode_bars=signal.episode_bars,
                flow_episode_cumulative_delta=signal.cumulative_signed_taker_quote,
                flow_episode_net_price_progress=signal.net_price_progress,
                rule_provenance=DIRECT_HOLD_INITIATIVE_RULE,
            )
            output.append(plan)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        output = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes == self.decision_minutes:
            output.extend(self._direct_hold_plans(bar))
        return output


class PhaseBreakoutMicroEngine(
    DirectHoldInitiativeMixin,
    PhaseFocusedFlowMicroEngine,
):
    pass


class EasyChartRE1PhaseBreakoutFlowBundle(EasyChartRE1PhaseFlowBundle):
    """Phase-flow core with one source-supported direct breakout OR branch."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = PhaseBreakoutMicroEngine(
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
        output["direct_hold_initiative_policy"] = {
            "scope": "ORDERED_MICRO_ACCEPTED_BREAK_ONLY",
            "entry_time": "REQUIRED_NEXT_DECISION_BAR_HOLD_CLOSE",
            "rule_provenance": DIRECT_HOLD_INITIATIVE_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1PhaseBreakoutFlowBundle
