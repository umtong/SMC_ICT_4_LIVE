"""Source-like first-pullback entry for liquidity-taking 15-minute OBs.

The live EasyChart case identifies a large pattern and a low sweep, waits for a
15-minute bullish engulfing candle to close, then buys the first lower-frame
pullback while the engulfing formation low remains intact.  It does not demand
that a second one-minute OB form, detach, retest and hold before the original
15-minute thesis can be acted on.

This family therefore gives the validated higher-frame footprint its own entry
responsibility.  A 15-minute engulfing OB must already satisfy the existing
pre-existing swing sweep/reclaim and aligned one-minute taker-flow validation.
Its first later 5-minute interaction may enter at the completed close only when
that close defends beyond the favorable body edge.  The stop includes the full
15-minute formation wick and the observed pullback; the target is the inherited
first pre-existing 5/15-minute obstacle.  The delayed lower-frame visual/flow
family remains available but loses duplicate arbitration when the direct thesis
has already entered.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, SetupState, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow_ob_sweep import LiquiditySweepFlowDecisionAreaEngine
from easychart_re1_flow_ob_sweep_responsibility import (
    EasyChartRE1ResponsibleSweepFlowOBBundle,
)


DIRECT_SWEEP_OB_PULLBACK_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "AFTER_A_LIQUIDITY_TAKING_FLOW_VALIDATED_FIFTEEN_MINUTE_ENGULFING_OB_CLOSE_THE_FIRST_LATER_FIVE_MINUTE_PULLBACK_MAY_ENTER_WHEN_IT_CLOSES_BEYOND_THE_FAVORABLE_OB_BODY_EDGE"
)
if DIRECT_SWEEP_OB_PULLBACK_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (DIRECT_SWEEP_OB_PULLBACK_RULE,)


class DirectSweepOBDecisionAreaEngine(LiquiditySweepFlowDecisionAreaEngine):
    """Convert the first defended 5m return to one immutable full-position plan."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._direct_plans: list[V5TradePlan] = []
        self._direct_counts: dict[str, int] = {}

    def _dinc(self, key: str) -> None:
        self._direct_counts[key] = self._direct_counts.get(key, 0) + 1

    def _discover_interactions(self, bar: Candle, previous: Candle, index: int) -> None:
        for context, members, _previous_zone in self._selected_clusters(bar, previous):
            side = self._side_for_zone(context)
            lower = min(item.lower for item in members)
            upper = max(item.upper for item in members)
            defended = bar.close > upper if side is Side.LONG else bar.close < lower
            if not defended:
                self._dinc("first_pullback_failed_to_close_beyond_favorable_ob_edge")
                self._trace(
                    "direct_sweep_ob_first_pullback_failed",
                    bar.ts_close_ns,
                    side=side.name,
                    context_zone_id=context.zone_id,
                    lower=lower,
                    upper=upper,
                    close=bar.close,
                    rule_provenance=DIRECT_SWEEP_OB_PULLBACK_RULE,
                )
                # The first interaction owns and retires the decision area even
                # when it fails.  Do not wait for a later, outcome-selected bar.
                for member in members:
                    self._claimed_structures.add(member.source_structure_id)
                continue

            breached = bar.low < lower if side is Side.LONG else bar.high > upper
            path = ScenarioPath.REJECTION if breached else ScenarioPath.BOUNCE
            setup = self._create_setup(
                path=path,
                context=context,
                members=members,
                bar=bar,
                decision_index=index,
                state=SetupState.WAITING_DISPLACEMENT,
            )
            if setup is None:
                self._dinc("direct_pullback_had_no_executable_objective")
                continue

            proxy = self.structure.snapshot_for(context, bar.ts_close_ns)
            self._audit(proxy)
            stop = (
                min(context.invalidation, bar.low - self.tick_size)
                if side is Side.LONG
                else max(context.invalidation, bar.high + self.tick_size)
            )
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
                self._dinc("direct_pullback_geometry_rejected")
                continue
            self._direct_plans.append(plan)
            self._dinc("direct_sweep_ob_plan_created")
            self._trace(
                "direct_sweep_ob_plan_created",
                bar.ts_close_ns,
                setup,
                plan_id=plan.plan_id,
                entry=plan.entry,
                stop=plan.stop,
                target=plan.target,
                gross_rr=plan.gross_rr,
                pullback_breached_body=breached,
                rule_provenance=DIRECT_SWEEP_OB_PULLBACK_RULE,
            )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes != self.decision_minutes:
            return super().on_bar(timeframe_minutes, bar)
        self._direct_plans = []
        existing = super().on_bar(timeframe_minutes, bar)
        unique = {plan.plan_id: plan for plan in existing + self._direct_plans}
        return sorted(
            unique.values(),
            key=lambda plan: (
                plan.interaction_time_ns,
                plan.observed_time_ns,
                plan.plan_id,
            ),
        )

    @property
    def direct_pullback_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._direct_counts.items())),
            "formation": self.formation_flow_diagnostics,
            "rule_provenance": DIRECT_SWEEP_OB_PULLBACK_RULE,
        }


class EasyChartRE1DirectSweepOBBundle(EasyChartRE1ResponsibleSweepFlowOBBundle):
    """Single-owner reversal core plus direct and delayed sweep-valid OB entries."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.direct_sweep_ob = DirectSweepOBDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="DIRECT_SWEEP_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["direct_sweep_ob"] = 0
        self._direct_bundle_counts: dict[str, int] = {}
        self._direct_bundle_trace: list[dict[str, Any]] = []

    def _dbinc(self, key: str) -> None:
        self._direct_bundle_counts[key] = self._direct_bundle_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.direct_sweep_ob.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.direct_sweep_ob.plans

    def _route_direct(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if plan.scenario_path not in {
                ScenarioPath.BOUNCE.value,
                ScenarioPath.REJECTION.value,
            }:
                self._dbinc("non_reversal_direct_ob_plan_suppressed")
                continue
            if self._duplicate_episode(plan):
                self._dbinc("direct_ob_overlapped_existing_episode")
                continue
            if not self._route_plan(plan):
                self._dbinc("direct_ob_rejected_by_macro_context")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._dbinc("direct_ob_plan_allowed")
            self._direct_bundle_trace.append(
                {
                    "scenario_kind": "direct_sweep_ob_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": DIRECT_SWEEP_OB_PULLBACK_RULE,
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        direct: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            raw = self.direct_sweep_ob.on_bar(timeframe_minutes, bar)
            self._sync_audit("direct_sweep_ob", self.direct_sweep_ob)
            direct = self._route_direct(raw)
        routed = super().on_bar(timeframe_minutes, bar)
        return sorted(
            direct + routed,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            super().drain_trace()
            + self.direct_sweep_ob.drain_trace()
            + self._direct_bundle_trace
        )
        self._direct_bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.direct_sweep_ob.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["direct_sweep_ob_policy"] = {
            "counts": dict(sorted(self._direct_bundle_counts.items())),
            "engine": self.direct_sweep_ob.direct_pullback_diagnostics,
            "rule_provenance": DIRECT_SWEEP_OB_PULLBACK_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DirectSweepOBBundle
