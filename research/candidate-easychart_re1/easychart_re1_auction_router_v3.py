"""Add mature diagonal/channel acceptance as an independent auction family.

Frequency should come from distinct strong mechanisms, not from weakening a
single rejection setup.  The integrated policy already owns horizontal flips
and local OB/FVG pullback continuation.  It still omits the source's other
complete acceptance scenario: a mature projected trend-line or channel boundary
which breaks by body, holds outside on the next decision bar, returns for the
first time and resumes.

This family uses the existing ordered channel-phase engine directly, before any
macro router.  Only acceptance plans are retained.  Their first following
completed minute must both close beyond the return extreme and show aligned
initiative or adverse-flow absorption.  The objective is refined to the nearer
pre-existing significant one-minute swing when one exists.  Active opposite
BTC/ETH-led common initiative vetoes the plan; a stale slower macro label does
not override a complete local acceptance auction.

No score, session, volatility threshold, fitted timeout, partial exit, stop
movement or additional account slot is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_auction_router_v2 import EasyChartRE1AuctionRouterV2Bundle
from easychart_re1_efficient_objective import PivotOnlyObjectiveBook
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_re1_human_policy import HumanMicroEngine
from easychart_re1_local_auction_continuation import (
    COMMON_FACTOR_VETO_ONLY_RULE,
    EasyChartRE1LocalAuctionStrategy,
)


MATURE_DIAGONAL_ACCEPTANCE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ORDERED_MATURE_DIAGONAL_OR_CHANNEL_BODY_BREAK_NEXT_BAR_HOLD_FIRST_RETURN_AND_FIRST_FLOW_CONFIRMED_RESPONSE_DEFINE_ONE_ACCEPTANCE_CONTINUATION"
)
MATURE_DIAGONAL_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "MATURE_DIAGONAL_ACCEPTANCE_USES_THE_NEARER_OF_ITS_EXISTING_OBJECTIVE_AND_A_PREEXISTING_UNSPENT_SPAN6_ONE_MINUTE_OPPOSING_SWING"
)
for _rule in (MATURE_DIAGONAL_ACCEPTANCE_RULE, MATURE_DIAGONAL_OBJECTIVE_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class PendingDiagonalResponse:
    plan: V5TradePlan
    retest_time_ns: int
    retest_high: float
    retest_low: float
    retest_close: float


class MatureDiagonalResponseFamily:
    """Ordered diagonal acceptance followed by one causal flow response."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.minimum_gross_rr = minimum_gross_rr
        self.source = HumanMicroEngine(
            symbol,
            tick_size,
            scale_name="MATURE_DIAGONAL_ACCEPTANCE",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.flow = CausalFlowAnalyzer(tick_size)
        self.micro_objectives = PivotOnlyObjectiveBook(
            symbol,
            1,
            tick_size,
            pivot_spans=(6,),
        )
        self.pending: dict[str, PendingDiagonalResponse] = {}
        self.final_plans: list[V5TradePlan] = []
        self.trace_events: list[dict[str, Any]] = []
        self._zones: dict[str, Any] = {}
        self._counts: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    @staticmethod
    def _stop_touched(plan: V5TradePlan, bar: Candle) -> bool:
        return bar.low <= plan.stop if plan.side is Side.LONG else bar.high >= plan.stop

    @staticmethod
    def _target_touched(plan: V5TradePlan, bar: Candle) -> bool:
        return bar.high >= plan.target if plan.side is Side.LONG else bar.low <= plan.target

    @staticmethod
    def _responded(pending: PendingDiagonalResponse, bar: Candle) -> bool:
        return (
            bar.close > pending.retest_high
            if pending.plan.side is Side.LONG
            else bar.close < pending.retest_low
        )

    @staticmethod
    def _closer(side: Side, candidate: float, existing: float) -> bool:
        return candidate < existing if side is Side.LONG else candidate > existing

    @staticmethod
    def _flow_mechanism(
        side: Side,
        observation: FlowObservation | None,
    ) -> str | None:
        if observation is None or not observation.active or not observation.directed:
            return None
        intended_body = observation.body > 0.0 if side is Side.LONG else observation.body < 0.0
        aligned_delta = (
            observation.signed_taker_quote > 0.0
            if side is Side.LONG
            else observation.signed_taker_quote < 0.0
        )
        opposite_delta = (
            observation.signed_taker_quote < 0.0
            if side is Side.LONG
            else observation.signed_taker_quote > 0.0
        )
        if aligned_delta and intended_body and observation.material_progress:
            return "FIRST_RESPONSE_ALIGNED_INITIATIVE"
        if opposite_delta and intended_body:
            return "FIRST_RESPONSE_ADVERSE_FLOW_ABSORBED"
        return None

    def _target(self, plan: V5TradePlan, bar: Candle) -> tuple[Any | None, float]:
        value = self.micro_objectives.target_for(
            plan.side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=6,
            current_high=bar.high,
            current_low=bar.low,
        )
        if value is None or not self._closer(plan.side, value[1], plan.target):
            return None, plan.target
        zone, price = value
        self._zones[zone.zone_id] = zone
        return zone, price

    def _complete_pending(
        self,
        bar: Candle,
        observation: FlowObservation | None,
    ) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for event_id, pending in list(self.pending.items()):
            if bar.ts_close_ns <= pending.retest_time_ns:
                continue
            self.pending.pop(event_id, None)
            plan = pending.plan
            if self._stop_touched(plan, bar):
                self._inc("response_bar_touched_stop_before_entry")
                continue
            if self._target_touched(plan, bar):
                self._inc("response_bar_spent_target_before_entry")
                continue
            if not self._responded(pending, bar):
                self._inc("first_response_failed_price_extension")
                continue
            mechanism = self._flow_mechanism(plan.side, observation)
            if mechanism is None:
                self._inc("first_response_failed_flow_transfer")
                continue

            target_zone, target = self._target(plan, bar)
            entry = bar.close
            risk = entry - plan.stop if plan.side is Side.LONG else plan.stop - entry
            reward = target - entry if plan.side is Side.LONG else entry - target
            if risk <= 0.0 or reward <= 0.0:
                self._inc("response_nonpositive_geometry")
                continue
            gross_rr = reward / risk
            if gross_rr + 1e-12 < self.minimum_gross_rr:
                self._inc("response_below_minimum_gross_rr")
                continue
            final = replace(
                plan,
                plan_id=f"{plan.plan_id}:FLOW_RESPONSE:{bar.ts_close_ns}",
                observed_time_ns=bar.ts_close_ns,
                trigger_time_ns=bar.ts_close_ns,
                entry=entry,
                target=target,
                gross_rr=gross_rr,
                target_zone_id=plan.target_zone_id if target_zone is None else target_zone.zone_id,
                target_zone_kind=plan.target_zone_kind if target_zone is None else target_zone.kind,
                trigger_zone_kind=f"MATURE_DIAGONAL_{mechanism}",
                rule_provenance=plan.rule_provenance + (
                    MATURE_DIAGONAL_ACCEPTANCE_RULE,
                    MATURE_DIAGONAL_OBJECTIVE_RULE,
                ),
            )
            self.final_plans.append(final)
            output.append(final)
            self._inc("mature_diagonal_response_plan_created")
            self.trace_events.append(
                {
                    "scenario_kind": "mature_diagonal_acceptance_response_confirmed",
                    "event_time_ns": bar.ts_close_ns,
                    "symbol": self.symbol,
                    "plan_id": final.plan_id,
                    "side": final.side.name,
                    "mechanism": mechanism,
                    "signed_taker_quote": None if observation is None else observation.signed_taker_quote,
                    "activity_ratio": None if observation is None else observation.activity_ratio,
                    "delta_ratio": None if observation is None else observation.delta_ratio,
                    "entry": final.entry,
                    "stop": final.stop,
                    "target": final.target,
                    "gross_rr": final.gross_rr,
                    "rule_provenance": (
                        MATURE_DIAGONAL_ACCEPTANCE_RULE,
                        MATURE_DIAGONAL_OBJECTIVE_RULE,
                    ),
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        observation: FlowObservation | None = None
        if timeframe_minutes == 1:
            self.micro_objectives.on_bar(bar)
            observation = self.flow.observe(bar)
            output.extend(self._complete_pending(bar, observation))

        raw = self.source.on_bar(timeframe_minutes, bar)
        for plan in raw:
            if plan.scenario_path != ScenarioPath.ACCEPTANCE.value:
                continue
            if plan.causal_event_id in self.pending:
                self._inc("duplicate_pending_diagonal_acceptance")
                continue
            self.pending[plan.causal_event_id] = PendingDiagonalResponse(
                plan=plan,
                retest_time_ns=bar.ts_close_ns,
                retest_high=bar.high,
                retest_low=bar.low,
                retest_close=bar.close,
            )
            self._inc("mature_diagonal_waiting_first_response")

        if timeframe_minutes == 1:
            self.micro_objectives.observe_price(bar)
        return output

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return self.source.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return self.final_plans

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.source.drain_trace() + self.trace_events
        self.trace_events = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self._zones.get(zone_id) or self.source.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._counts.items())),
            "pending": len(self.pending),
            "source": self.source.diagnostics,
            "flow": self.flow.diagnostics,
            "micro_objectives": dict(self.micro_objectives.diagnostics),
            "rules": (
                MATURE_DIAGONAL_ACCEPTANCE_RULE,
                MATURE_DIAGONAL_OBJECTIVE_RULE,
            ),
        }


class EasyChartRE1AuctionRouterV3Bundle(EasyChartRE1AuctionRouterV2Bundle):
    """Integrated four-mechanism EasyChart auction policy."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.mature_diagonal_acceptance = MatureDiagonalResponseFamily(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self._diagonal_counts: dict[str, int] = {}
        self._diagonal_trace: list[dict[str, Any]] = []

    def _dinc(self, key: str) -> None:
        self._diagonal_counts[key] = self._diagonal_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.mature_diagonal_acceptance.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.mature_diagonal_acceptance.plans

    def _route_diagonal(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._dinc("mature_diagonal_duplicate_episode")
                continue
            factor = self._market_factor_state
            if factor is not None and factor.side is not plan.side:
                self._dinc("mature_diagonal_rejected_by_opposing_common_factor")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._dinc("mature_diagonal_plan_allowed")
            self._diagonal_trace.append(
                {
                    "scenario_kind": "mature_diagonal_acceptance_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "macro_side": None if self._macro_side is None else self._macro_side.name,
                    "factor_side": None if factor is None else factor.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": (
                        MATURE_DIAGONAL_ACCEPTANCE_RULE,
                        MATURE_DIAGONAL_OBJECTIVE_RULE,
                        COMMON_FACTOR_VETO_ONLY_RULE,
                    ),
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        diagonal = self._route_diagonal(
            self.mature_diagonal_acceptance.on_bar(timeframe_minutes, bar)
        )
        core = super().on_bar(timeframe_minutes, bar)
        return sorted(
            diagonal + core,
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
            + self.mature_diagonal_acceptance.drain_trace()
            + self._diagonal_trace
        )
        self._diagonal_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.mature_diagonal_acceptance.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["mature_diagonal_acceptance"] = {
            "bundle_counts": dict(sorted(self._diagonal_counts.items())),
            "family": self.mature_diagonal_acceptance.diagnostics,
            "rules": (
                MATURE_DIAGONAL_ACCEPTANCE_RULE,
                MATURE_DIAGONAL_OBJECTIVE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1AuctionRouterV3Bundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
