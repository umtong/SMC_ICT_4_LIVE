"""Absorption-confirmed revision of structural auction control.

The state machine remains identical to v1.  The decision evidence is narrowed
mechanistically: the completed first-return entry bar must absorb meaningful
aggressive flow against the intended trade while closing in structural control.
Pure aligned aggression is not accepted because it usually confirms after
price has already departed the source and therefore behaves as late initiative.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, V5TradePlan
from domain import Candle, Side
from structural_auction_control_v1 import (
    EpisodeFlowControl,
    StructuralAuctionControlV1Bundle,
    StructuralHorizontalEngine,
    StructuralMajorSwingEngine,
    StructuralMicroEngine,
)


ENTRY_RETEST_ABSORPTION_RULE = (
    "RESEARCH_HYPOTHESIS:THE_COMPLETED_FIRST_RETURN_MUST_CLOSE_IN_THE_PLANNED_"
    "DIRECTION_WHILE_ACTIVE_DIRECTED_TAKER_FLOW_ATTACKS_AGAINST_THAT_DIRECTION"
)
if ENTRY_RETEST_ABSORPTION_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (ENTRY_RETEST_ABSORPTION_RULE,)


class EntryRetestAbsorptionMixin:
    def _flow_control(
        self,
        setup: ScenarioSetup,
        bar: Candle,
    ) -> EpisodeFlowControl | None:
        observations = self._episode_observations(setup, bar.ts_close_ns)
        if not observations:
            return None
        current = observations[-1]
        if current.ts_close_ns != bar.ts_close_ns:
            return None

        sign = 1.0 if setup.side is Side.LONG else -1.0
        _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
        boundary = upper if setup.side is Side.LONG else lower
        final_progress = sign * (bar.close - boundary)
        directional_body = sign * (bar.close - bar.open) > 0.0
        current_opposes_trade = sign * current.signed_taker_quote < 0.0
        if not (
            final_progress > 0.0
            and directional_body
            and current.active
            and current.directed
            and current_opposes_trade
        ):
            return None

        total_quote = sum(item.quote_volume for item in observations)
        if total_quote <= 0.0:
            return None
        signed_for_side = [sign * item.signed_taker_quote for item in observations]
        aligned_quote = sum(max(0.0, value) for value in signed_for_side)
        adverse_quote = sum(max(0.0, -value) for value in signed_for_side)
        cumulative = sum(signed_for_side)

        episode_low = min(item.low for item in observations)
        episode_high = max(item.high for item in observations)
        if setup.side is Side.LONG:
            adverse_penetration = max(0.0, lower - episode_low)
            recovery = bar.close - episode_low
        else:
            adverse_penetration = max(0.0, episode_high - upper)
            recovery = episode_high - bar.close
        if recovery <= adverse_penetration:
            return None

        confirmation = setup.confirmation_time_ns or setup.interaction_time_ns
        event = [item for item in observations if item.ts_close_ns <= confirmation]
        response = [item for item in observations if item.ts_close_ns > confirmation]
        meaningful_event = [item for item in event if item.active and item.directed]
        meaningful_response = [item for item in response if item.active and item.directed]

        return EpisodeFlowControl(
            mechanism="ENTRY_RETEST_OPPOSING_AGGRESSION_ABSORBED",
            episode_bars=len(observations),
            event_bars=len(event),
            response_bars=len(response),
            total_quote=total_quote,
            aligned_taker_quote=aligned_quote,
            adverse_taker_quote=adverse_quote,
            cumulative_signed_for_side=cumulative,
            adverse_penetration=adverse_penetration,
            recovery_from_extreme=recovery,
            final_control_progress=final_progress,
            event_active_directed_bars=len(meaningful_event),
            response_active_directed_bars=len(meaningful_response),
            current_activity_ratio=current.activity_ratio,
            current_delta_ratio=current.delta_ratio,
            current_impact_per_activity=current.impact_per_activity,
        )

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
    ) -> V5TradePlan | None:
        plan = super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=stop,
            trigger_zone=trigger_zone,
            trigger_kind=trigger_kind,
            trigger_strength=trigger_strength,
        )
        if plan is None:
            return None
        refined = replace(
            plan,
            family=plan.family.replace("SAC_V2_", "SAC_V2A_ABSORPTION_", 1),
            rule_provenance=plan.rule_provenance + (ENTRY_RETEST_ABSORPTION_RULE,),
        )
        if self.plans and self.plans[-1].plan_id == plan.plan_id:
            self.plans[-1] = refined
        self._trace(
            "entry_retest_absorption_confirmed",
            bar.ts_close_ns,
            setup,
            plan_id=refined.plan_id,
            signed_taker_quote=current.signed_taker_quote
            if (current := self.auction_flow.last_observation) is not None
            else None,
            activity_ratio=current.activity_ratio if current is not None else None,
            delta_ratio=current.delta_ratio if current is not None else None,
            rule_provenance=ENTRY_RETEST_ABSORPTION_RULE,
        )
        return refined


class AbsorptionStructuralMicroEngine(EntryRetestAbsorptionMixin, StructuralMicroEngine):
    pass


class AbsorptionStructuralHorizontalEngine(
    EntryRetestAbsorptionMixin,
    StructuralHorizontalEngine,
):
    pass


class AbsorptionStructuralMajorSwingEngine(
    EntryRetestAbsorptionMixin,
    StructuralMajorSwingEngine,
):
    pass


class StructuralAuctionControlV2Bundle(StructuralAuctionControlV1Bundle):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = AbsorptionStructuralMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = AbsorptionStructuralHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = AbsorptionStructuralMajorSwingEngine(
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
        output["entry_retest_absorption_policy"] = {
            "mechanism": (
                "active directed taker flow attacks against the intended side, "
                "but the completed first-return bar closes in structural control"
            ),
            "rule_provenance": ENTRY_RETEST_ABSORPTION_RULE,
        }
        return output


MultiScaleScenarioBundle = StructuralAuctionControlV2Bundle
