"""Complete-episode invalidation geometry for EasyChart RE1.

A one-minute confirmation candle refines entry; it must not redefine the entire
trade's risk to one tiny wick while the target still belongs to a fifteen-minute
structure.  The supplied trades keep the stop beyond the causal event which
would falsify the thesis:

* sweep/reclaim and bounce/rotation: beyond the sweep/interaction extreme and
  the relevant decision-area invalidation;
* accepted break: beyond the retest footprint and the latest confirmed decision
  swing, not beyond an unrelated old channel extreme.

This module changes the pre-entry stop only.  Quantity is recomputed from the
same 3% current-NAV risk budget, and plans whose first meaningful target is now
below 1R are rejected instead of repairing them with a remote extension.
"""
from __future__ import annotations

from numbers import Real
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup
from domain import Candle, Side
from easychart_re1_decision_area_v2 import OrderBlockDecisionScenarioEngineV2
from easychart_re1_decision_area_v3 import EasyChartRE1DecisionAreaV3Bundle
from easychart_re1_geometry_v2 import (
    GeometryV2HorizontalScenarioEngine,
    GeometryV2MajorLiquidityScenarioEngine,
    GeometryV2NaturalScenarioEngine,
)
from easychart_re1_geometry_v3 import (
    EasyChartRE1GeometryV3Bundle,
    GeometryV3HorizontalFlipScenarioEngine,
)


COMPLETE_EPISODE_INVALIDATION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "MICRO_CONFIRMATION_REFINES_ENTRY_BUT_INITIAL_STOP_REMAINS_BEYOND_THE_COMPLETE_CAUSAL_INTERACTION_OR_RETEST_INVALIDATION"
)
if COMPLETE_EPISODE_INVALIDATION_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (COMPLETE_EPISODE_INVALIDATION_RULE,)


def numeric(value: Any) -> float | None:
    if isinstance(value, Real):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CompleteEpisodeInvalidationMixin:
    """Widen a micro stop only to already-known scenario invalidation facts."""

    @staticmethod
    def _path_text(setup: ScenarioSetup) -> str:
        value = getattr(setup, "path", None)
        if value is None:
            value = getattr(setup, "scenario_path", "")
        return str(getattr(value, "value", value)).upper()

    def _episode_stop(
        self,
        setup: ScenarioSetup,
        trigger_zone: Any,
        micro_stop: float,
    ) -> tuple[float, tuple[tuple[str, float], ...]]:
        candidates: list[tuple[str, float]] = [("MICRO_EXECUTION", float(micro_stop))]
        trigger_invalidation = numeric(getattr(trigger_zone, "invalidation", None))
        if trigger_invalidation is not None:
            candidates.append(("TRIGGER_FOOTPRINT_INVALIDATION", trigger_invalidation))

        path = self._path_text(setup)
        if "REJECTION" in path or "ROTATION" in path:
            interaction = numeric(getattr(setup, "interaction_extreme", None))
            if interaction is not None:
                interaction_stop = (
                    interaction - self.tick_size
                    if setup.side is Side.LONG
                    else interaction + self.tick_size
                )
                candidates.append(("INTERACTION_EXTREME", interaction_stop))
            context = getattr(setup, "context", None)
            context_invalidation = numeric(getattr(context, "invalidation", None))
            if context_invalidation is not None:
                candidates.append(("DECISION_AREA_INVALIDATION", context_invalidation))

        if setup.side is Side.LONG:
            selected = min(value for _, value in candidates)
        else:
            selected = max(value for _, value in candidates)
        return selected, tuple(candidates)

    def _make_plan(
        self,
        setup: ScenarioSetup,
        bar: Candle,
        *,
        entry: float,
        stop: float,
        trigger_zone: Any,
        trigger_kind: Any,
        trigger_strength: float,
    ):
        episode_stop, candidates = self._episode_stop(setup, trigger_zone, stop)
        if abs(episode_stop - stop) > self.tick_size * 0.5:
            self._inc("initial_stop_expanded_to_complete_episode")
            self._trace(
                "complete_episode_invalidation_selected",
                bar.ts_close_ns,
                setup,
                micro_stop=stop,
                selected_stop=episode_stop,
                candidates=[{"source": source, "price": price} for source, price in candidates],
                rule_provenance=COMPLETE_EPISODE_INVALIDATION_RULE,
            )
        else:
            self._inc("micro_stop_already_beyond_complete_episode")
        return super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=episode_stop,
            trigger_zone=trigger_zone,
            trigger_kind=trigger_kind,
            trigger_strength=trigger_strength,
        )


class EpisodeNaturalScenarioEngine(
    CompleteEpisodeInvalidationMixin,
    GeometryV2NaturalScenarioEngine,
):
    pass


class EpisodeHorizontalScenarioEngine(
    CompleteEpisodeInvalidationMixin,
    GeometryV2HorizontalScenarioEngine,
):
    pass


class EpisodeMajorLiquidityScenarioEngine(
    CompleteEpisodeInvalidationMixin,
    GeometryV2MajorLiquidityScenarioEngine,
):
    pass


class EpisodeHorizontalFlipScenarioEngine(
    CompleteEpisodeInvalidationMixin,
    GeometryV3HorizontalFlipScenarioEngine,
):
    pass


class EpisodeDecisionAreaScenarioEngine(
    CompleteEpisodeInvalidationMixin,
    OrderBlockDecisionScenarioEngineV2,
):
    pass


class EasyChartRE1EpisodeGeometryBundle(EasyChartRE1GeometryV3Bundle):
    """Final non-duplicated structures with complete-episode initial risk."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = EpisodeNaturalScenarioEngine(
            symbol, tick_size, scale_name="MICRO", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = EpisodeHorizontalScenarioEngine(
            symbol, tick_size, scale_name="HORIZONTAL", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr,
        )
        self.liquidity = EpisodeMajorLiquidityScenarioEngine(
            symbol, tick_size, scale_name="MAJOR_LIQUIDITY", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_flip = EpisodeHorizontalFlipScenarioEngine(
            symbol, tick_size, scale_name="HORIZONTAL_SR_FLIP", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr,
        )
        for key in ("micro", "horizontal", "liquidity", "horizontal_flip"):
            self._audit_offsets[key] = 0


class EasyChartRE1EpisodeDecisionAreaBundle(EasyChartRE1DecisionAreaV3Bundle):
    """Complete-episode geometry plus the independent strong-OB family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = EpisodeNaturalScenarioEngine(
            symbol, tick_size, scale_name="MICRO", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = EpisodeHorizontalScenarioEngine(
            symbol, tick_size, scale_name="HORIZONTAL", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr,
        )
        self.liquidity = EpisodeMajorLiquidityScenarioEngine(
            symbol, tick_size, scale_name="MAJOR_LIQUIDITY", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal_flip = EpisodeHorizontalFlipScenarioEngine(
            symbol, tick_size, scale_name="HORIZONTAL_SR_FLIP", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr,
        )
        self.decision_area = EpisodeDecisionAreaScenarioEngine(
            symbol, tick_size, scale_name="DECISION_AREA_OB_V2", higher_minutes=15,
            decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr,
        )
        for key in ("micro", "horizontal", "liquidity", "horizontal_flip", "decision_area"):
            self._audit_offsets[key] = 0


MultiScaleScenarioBundle = EasyChartRE1EpisodeDecisionAreaBundle
