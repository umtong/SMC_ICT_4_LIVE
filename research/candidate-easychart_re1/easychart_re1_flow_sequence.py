"""Liquidity -> absorption -> response sequence for EasyChart RE1 flow entry.

One-bar taker imbalance is not a complete institutional footprint. A causal
trade episode should explain why the large flow appeared and what price did with
it. This module turns flow into a sequence rather than a scalar filter:

1. a pre-existing price boundary supplies potential stop/liquidity inventory;
2. aggressive flow reaches or crosses that boundary;
3. opposing aggression is absorbed, evidenced by failure to create adverse
   progress and a reclaim/hold;
4. entry may occur on the reclaiming absorption bar itself or on the first
   aligned initiative bar after that absorption;
5. accepted S/R flips additionally permit aligned initiative only after the
   flipped boundary has actually been retested.

Thus raw initiative far from the decision boundary cannot originate a reversal
or a breakout-retest entry. Existing visual OB/FVG/retest entries keep priority;
flow remains an independent OR path, not another global requirement.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup
from domain import Side
from easychart_re1_complete_policy import LocatedHorizontalFlipEngine
from easychart_re1_flow import (
    FlowEntryMixin,
    FlowObservation,
    FlowSignal,
    FlowTriggerKind,
)
from easychart_re1_flow_routed import EasyChartRE1FlowRoutedBundle
from easychart_re1_human_policy import (
    HumanDecisionAreaEngine,
    HumanHorizontalEngine,
    HumanMajorSwingEngine,
    HumanMicroEngine,
)
from easychart_re1_wedge import TerminalWedgeScenarioEngine


FLOW_SEQUENCE_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "REVERSAL_FLOW_REQUIRES_BOUNDARY_ABSORPTION_OR_FIRST_ALIGNED_INITIATIVE_AFTER_BOUNDARY_ABSORPTION"
)
ACCEPTANCE_RETEST_FLOW_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "ACCEPTED_BREAK_FLOW_REQUIRES_ACTUAL_FLIPPED_BOUNDARY_RETEST_THEN_ABSORPTION_OR_ALIGNED_INITIATIVE"
)
if FLOW_SEQUENCE_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (FLOW_SEQUENCE_RULE,)
if ACCEPTANCE_RETEST_FLOW_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (ACCEPTANCE_RETEST_FLOW_RULE,)


class SequenceFlowEntryMixin(FlowEntryMixin):
    """Classify flow only when its boundary-to-response sequence is coherent."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._sequence_counts: dict[str, int] = {}

    def _sinc(self, key: str) -> None:
        self._sequence_counts[key] = self._sequence_counts.get(key, 0) + 1

    def _flow_touches(self, setup: ScenarioSetup, item: FlowObservation) -> bool:
        """Return whether one completed flow bar interacted with the setup boundary.

        The distinct name is intentional: scenario-context engines already expose
        ``_touches(bar, zone)`` for price-cluster discovery.
        """
        _, lower, upper = self._projected_bounds(setup, item.ts_close_ns)
        return item.low <= upper if setup.side is Side.LONG else item.high >= lower

    def _flow_outside(self, setup: ScenarioSetup, item: FlowObservation) -> bool:
        """Return whether the completed flow bar finished on the intended side."""
        _, lower, upper = self._projected_bounds(setup, item.ts_close_ns)
        return item.close > upper if setup.side is Side.LONG else item.close < lower

    def _flow_signal(
        self,
        setup: ScenarioSetup,
        bar: Any,
        observation: FlowObservation | None,
    ) -> FlowSignal | None:
        if observation is None or not self._flow_outside(setup, observation):
            return None
        event_start = setup.confirmation_time_ns or setup.interaction_time_ns
        episode = self.flow_analyzer.since(event_start)
        if not episode:
            return None

        cumulative_delta = sum(item.signed_taker_quote for item in episode)
        net_progress = self._intended_progress(
            setup.side,
            episode[0].open,
            episode[-1].close,
        )
        prior = episode[:-1]
        prior_absorption = [
            item
            for item in prior
            if item.active
            and item.directed
            and self._opposite_delta(setup.side, item.signed_taker_quote)
            and self._flow_touches(setup, item)
        ]
        touch_episode = [
            item for item in episode if self._flow_touches(setup, item)
        ]

        current_absorption = (
            observation.active
            and observation.directed
            and self._opposite_delta(setup.side, observation.signed_taker_quote)
            and self._flow_touches(setup, observation)
        )
        repeated_absorption = (
            bool(touch_episode)
            and any(
                item.active
                and item.directed
                and self._opposite_delta(setup.side, item.signed_taker_quote)
                for item in episode
            )
            and self._opposite_delta(setup.side, cumulative_delta)
            and net_progress >= 0.0
        )

        aligned_current = (
            observation.active
            and observation.directed
            and observation.material_progress
            and self._aligned_delta(setup.side, observation.signed_taker_quote)
            and (
                observation.close_location >= 0.5
                if setup.side is Side.LONG
                else observation.close_location <= 0.5
            )
        )
        response_after_absorption = False
        response_progress = 0.0
        if aligned_current and prior_absorption:
            # The most recent absorption is the causal parent of the first
            # response, not an arbitrarily old absorption earlier in the episode.
            latest_absorption = prior_absorption[-1]
            response_progress = self._intended_progress(
                setup.side,
                latest_absorption.open,
                observation.close,
            )
            response_after_absorption = response_progress > 0.0

        if setup.path is ScenarioPath.ACCEPTANCE:
            if current_absorption or repeated_absorption:
                kind = (
                    FlowTriggerKind.SELL_ABSORPTION
                    if setup.side is Side.LONG
                    else FlowTriggerKind.BUY_ABSORPTION
                )
                if repeated_absorption and not current_absorption:
                    kind = (
                        FlowTriggerKind.REPEATED_SELL_ABSORPTION
                        if setup.side is Side.LONG
                        else FlowTriggerKind.REPEATED_BUY_ABSORPTION
                    )
                self._sinc("acceptance_retest_absorption_signal")
                return FlowSignal(
                    kind=kind,
                    mechanism="ACCEPTANCE_RETEST_ABSORPTION",
                    strength=observation.activity_ratio * observation.delta_ratio,
                    observation=observation,
                    episode_bars=len(episode),
                    cumulative_signed_taker_quote=cumulative_delta,
                    net_price_progress=net_progress,
                )

            retest_before_current = any(
                item.ts_close_ns < observation.ts_close_ns
                for item in touch_episode
            )
            retest_initiative = aligned_current and (
                response_after_absorption
                or retest_before_current
                or self._flow_touches(setup, observation)
            )
            if retest_initiative:
                kind = (
                    FlowTriggerKind.BUY_INITIATIVE
                    if setup.side is Side.LONG
                    else FlowTriggerKind.SELL_INITIATIVE
                )
                mechanism = (
                    "ACCEPTANCE_RESPONSE_INITIATIVE_AFTER_ABSORPTION"
                    if response_after_absorption
                    else "ACCEPTANCE_RETEST_INITIATIVE"
                )
                self._sinc("acceptance_retest_initiative_signal")
                return FlowSignal(
                    kind=kind,
                    mechanism=mechanism,
                    strength=(
                        observation.activity_ratio
                        * observation.delta_ratio
                        * observation.body_ratio
                    ),
                    observation=observation,
                    episode_bars=len(episode),
                    cumulative_signed_taker_quote=cumulative_delta,
                    net_price_progress=(
                        response_progress if response_after_absorption else net_progress
                    ),
                )
            self._sinc("acceptance_raw_initiative_deferred_without_retest")
            return None

        if setup.path not in {
            ScenarioPath.REJECTION,
            ScenarioPath.BOUNCE,
            ScenarioPath.ROTATION,
        }:
            self._sinc("unknown_path_deferred")
            return None

        if current_absorption or repeated_absorption:
            kind = (
                FlowTriggerKind.SELL_ABSORPTION
                if setup.side is Side.LONG
                else FlowTriggerKind.BUY_ABSORPTION
            )
            if repeated_absorption and not current_absorption:
                kind = (
                    FlowTriggerKind.REPEATED_SELL_ABSORPTION
                    if setup.side is Side.LONG
                    else FlowTriggerKind.REPEATED_BUY_ABSORPTION
                )
            self._sinc("reversal_absorption_signal")
            return FlowSignal(
                kind=kind,
                mechanism=(
                    "CURRENT_BOUNDARY_ABSORPTION"
                    if current_absorption
                    else "REPEATED_BOUNDARY_ABSORPTION"
                ),
                strength=observation.activity_ratio * observation.delta_ratio,
                observation=observation,
                episode_bars=len(episode),
                cumulative_signed_taker_quote=cumulative_delta,
                net_price_progress=net_progress,
            )

        if response_after_absorption:
            kind = (
                FlowTriggerKind.BUY_INITIATIVE
                if setup.side is Side.LONG
                else FlowTriggerKind.SELL_INITIATIVE
            )
            self._sinc("reversal_response_initiative_after_absorption")
            return FlowSignal(
                kind=kind,
                mechanism="RESPONSE_INITIATIVE_AFTER_ABSORPTION",
                strength=(
                    observation.activity_ratio
                    * observation.delta_ratio
                    * observation.body_ratio
                ),
                observation=observation,
                episode_bars=len(episode),
                cumulative_signed_taker_quote=cumulative_delta,
                net_price_progress=response_progress,
            )

        if aligned_current:
            self._sinc("reversal_raw_initiative_deferred_without_absorption")
        return None

    @property
    def flow_sequence_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._sequence_counts.items())),
            "rules": (FLOW_SEQUENCE_RULE, ACCEPTANCE_RETEST_FLOW_RULE),
        }


class SequenceFlowMicroEngine(SequenceFlowEntryMixin, HumanMicroEngine):
    pass


class SequenceFlowHorizontalEngine(SequenceFlowEntryMixin, HumanHorizontalEngine):
    pass


class SequenceFlowMajorSwingEngine(SequenceFlowEntryMixin, HumanMajorSwingEngine):
    pass


class SequenceFlowDecisionAreaEngine(
    SequenceFlowEntryMixin,
    HumanDecisionAreaEngine,
):
    pass


class SequenceFlowHorizontalFlipEngine(
    SequenceFlowEntryMixin,
    LocatedHorizontalFlipEngine,
):
    pass


class SequenceFlowTerminalWedgeEngine(
    SequenceFlowEntryMixin,
    TerminalWedgeScenarioEngine,
):
    pass


class EasyChartRE1SequenceFlowBundle(EasyChartRE1FlowRoutedBundle):
    """Routed flow system using liquidity-absorption-response sequences."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = SequenceFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = SequenceFlowHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = SequenceFlowMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.decision_area = SequenceFlowDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="DECISION_AREA_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal_flip = SequenceFlowHorizontalFlipEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL_SR_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.wedge = SequenceFlowTerminalWedgeEngine(
            symbol,
            tick_size,
            scale_name="TERMINAL_WEDGE",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in (
            "micro",
            "horizontal",
            "major_swing",
            "decision_area",
            "horizontal_flip",
            "wedge",
        ):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        engines = {
            "micro": self.micro,
            "horizontal": self.horizontal,
            "major_swing": self.major_swing,
            "decision_area": self.decision_area,
            "horizontal_flip": self.horizontal_flip,
            "terminal_wedge": self.wedge,
        }
        output["flow_sequence_policy"] = {
            name: engine.flow_sequence_diagnostics
            for name, engine in engines.items()
        }
        output["flow_sequence_policy"]["rules"] = (
            FLOW_SEQUENCE_RULE,
            ACCEPTANCE_RETEST_FLOW_RULE,
        )
        return output


MultiScaleScenarioBundle = EasyChartRE1SequenceFlowBundle
