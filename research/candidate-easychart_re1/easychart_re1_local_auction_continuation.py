"""Local structure continuation plus the response-confirmed rejection core.

This candidate adds one missing EasyChart auction without weakening the proven
rejection family.  A continuation is not a generic pullback touch:

* a causally confirmed 15-minute close break supplies local direction;
* a high-quality five-minute engulfing order block must be born with aligned
  constituent one-minute aggressor flow and net price progress;
* the first later touch is consumed once, and the first completed response must
  demonstrate either aligned initiative or adverse-flow absorption;
* the five-minute formation invalidation is the fixed stop;
* the nearest still-unspent significant one-minute, five-minute or fifteen-
  minute opposing swing is the immutable objective;
* an active market-wide BTC/ETH-led impulse may veto an opposing first touch,
  but is not required to create every local opportunity.

The important distinction is responsibility.  The local 15-minute auction
creates the opportunity; cross-asset flow only prevents fighting a live common
shock.  This avoids the sparse all-symbol-AND continuation policy while keeping
countertrend local pullbacks out of an active market-wide impulse.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle, Side
from easychart_re1_efficient_objective import PivotOnlyObjectiveBook
from easychart_re1_factor_continuation import FactorContinuationEngine
from easychart_re1_significant_response import MultiScaleScenarioBundle as SignificantResponseBundle
from execution_re1_market_factor import (
    CommonFactorState,
    EasyChartRE1MarketFactorStrategy,
)


LOCAL_AUCTION_CONTINUATION_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "FIFTEEN_MINUTE_CAUSAL_BOS_PLUS_FLOW_VALIDATED_FIVE_MINUTE_ENGULFING_OB_DEFINES_A_LOCAL_CONTINUATION_AUCTION"
)
COMMON_FACTOR_VETO_ONLY_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "BTC_ETH_LED_COMMON_INITIATIVE_VETOES_ONLY_AN_OPPOSING_FIRST_TOUCH_AND_IS_NOT_AN_AND_GATE_FOR_LOCAL_CONTINUATION"
)
SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "LOCAL_CONTINUATION_TARGETS_THE_NEAREST_PREEXISTING_UNSPENT_SPAN6_ONE_MINUTE_OR_FIVE_FIFTEEN_MINUTE_OPPOSING_SWING"
)
for _rule in (
    LOCAL_AUCTION_CONTINUATION_RULE,
    COMMON_FACTOR_VETO_ONLY_RULE,
    SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class LocalAuctionContinuationEngine(FactorContinuationEngine):
    """Reuse the proven continuation state machine with local direction ownership."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.actual_common_factor: CommonFactorState | None = None
        self.local_factor_sequence = 0
        self.micro_objectives = PivotOnlyObjectiveBook(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
            pivot_spans=(6,),
        )
        self._local_counts: dict[str, int] = {}

    def _linc(self, key: str) -> None:
        self._local_counts[key] = self._local_counts.get(key, 0) + 1

    def set_market_factor_state(self, state: CommonFactorState | None) -> None:
        # Keep the real market factor separate.  The inherited ``factor_state``
        # is deliberately repurposed as a synthetic local-auction state so the
        # existing first-return state machine can be reused without duplicating
        # execution logic.
        self.actual_common_factor = state

    def _on_fifteen(self, bar: Candle) -> None:
        previous_side = self.local_side
        super()._on_fifteen(bar)
        if self.local_side is None or self.last_direction_pivot is None:
            self.factor_state = None
            return
        self.local_factor_sequence = (
            self.local_factor_sequence + 1
            if previous_side is self.local_side
            else 1
        )
        self.factor_state = CommonFactorState(
            side=self.local_side,
            event_time_ns=bar.ts_close_ns,
            event_midpoints={self.symbol: (bar.open + bar.close) / 2.0},
            agreeing_symbols=(self.symbol,),
            sequence=self.local_factor_sequence,
        )
        self._linc("local_direction_state_refreshed")

    def _opposed_by_common_factor(self, side: Side | None = None) -> bool:
        state = self.actual_common_factor
        intended = self.local_side if side is None else side
        return state is not None and intended is not None and state.side is not intended

    def _on_five(self, bar: Candle) -> None:
        # The inherited formation engine still updates every structure and zone.
        # Temporarily remove its synthetic formation context only when a live
        # market-wide impulse is opposite the local direction.
        saved = self.factor_state
        if self._opposed_by_common_factor():
            self.factor_state = None
            self._linc("five_minute_formation_vetoed_by_common_factor")
        try:
            super()._on_five(bar)
        finally:
            self.factor_state = saved

    def _finalize_pending_formations(self, bar: Candle) -> None:
        saved = self.factor_state
        if self._opposed_by_common_factor():
            self.factor_state = None
            self._linc("pending_formation_vetoed_by_common_factor")
        try:
            super()._finalize_pending_formations(bar)
        finally:
            self.factor_state = saved

    def _consume_first_touch_against_common_factor(self, bar: Candle) -> None:
        state = self.actual_common_factor
        if state is None:
            return
        for setup in list(self._active.values()):
            if state.side is setup.side:
                continue
            zone = setup.source_zone
            touched = bar.low <= zone.upper and bar.high >= zone.lower
            if not touched:
                continue
            self._finish(
                setup,
                "local_continuation_first_touch_vetoed_by_common_factor",
                bar.ts_close_ns,
                factor_side=state.side.name,
                factor_event_time_ns=state.event_time_ns,
                factor_sequence=state.sequence,
                rule_provenance=COMMON_FACTOR_VETO_ONLY_RULE,
            )
            self._linc("first_touch_vetoed_by_common_factor")

    def _nearest_target(
        self,
        side: Side,
        *,
        time_ns: int,
        high: float,
        low: float,
    ):
        choices: list[tuple[str, Any, float]] = []
        inherited = super()._nearest_target(
            side,
            time_ns=time_ns,
            high=high,
            low=low,
        )
        if inherited is not None:
            choices.append(("5M_OR_15M", inherited[0], inherited[1]))
        micro = self.micro_objectives.target_for(
            side,
            interaction_time_ns=time_ns,
            source_span=6,
            current_high=high,
            current_low=low,
        )
        if micro is not None:
            choices.append(("1M_SPAN6", micro[0], micro[1]))
        if not choices:
            return None
        selected = (
            min(choices, key=lambda item: (item[2], item[0]))
            if side is Side.LONG
            else max(choices, key=lambda item: (item[2], item[0]))
        )
        self._audit(selected[1])
        self._linc(f"objective_{selected[0].lower()}")
        return selected[1], selected[2]

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes != self.trigger_minutes:
            return super().on_bar(timeframe_minutes, bar)
        # A pivot observed on this completed minute is causal, but target_for
        # still requires it to predate the entry close.  Updating before the
        # inherited state machine therefore cannot create same-bar lookahead.
        self.micro_objectives.on_bar(bar)
        self._consume_first_touch_against_common_factor(bar)
        plans = super().on_bar(timeframe_minutes, bar)
        self.micro_objectives.observe_price(bar)
        return plans

    @property
    def local_continuation_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._local_counts.items())),
            "actual_common_factor": None
            if self.actual_common_factor is None
            else {
                "side": self.actual_common_factor.side.name,
                "event_time_ns": self.actual_common_factor.event_time_ns,
                "sequence": self.actual_common_factor.sequence,
            },
            "micro_objectives": dict(self.micro_objectives.diagnostics),
            "rules": (
                LOCAL_AUCTION_CONTINUATION_RULE,
                COMMON_FACTOR_VETO_ONLY_RULE,
                SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
            ),
        }


class EasyChartRE1LocalAuctionContinuationBundle(SignificantResponseBundle):
    """One policy: response-confirmed rejection OR local BOS continuation."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.local_continuation = LocalAuctionContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["local_continuation"] = 0
        self._local_bundle_counts: dict[str, int] = {}
        self._local_bundle_trace: list[dict[str, Any]] = []
        self._market_factor_state: CommonFactorState | None = None

    def _binc(self, key: str) -> None:
        self._local_bundle_counts[key] = self._local_bundle_counts.get(key, 0) + 1

    def set_market_factor_state(self, state: CommonFactorState | None) -> None:
        self._market_factor_state = state
        self.local_continuation.set_market_factor_state(state)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.local_continuation.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.local_continuation.plans

    def _route_local_continuation(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.symbol,
                item.plan_id,
            ),
        ):
            if plan.scenario_path != ScenarioPath.ACCEPTANCE.value:
                self._binc("non_acceptance_local_continuation_suppressed")
                continue
            if self._duplicate_episode(plan):
                self._binc("local_continuation_duplicate_episode")
                continue

            macro_side = getattr(self, "_macro_side", None)
            factor = self._market_factor_state
            if macro_side is None or plan.side is macro_side:
                if not self._route_plan(plan):
                    self._binc("local_continuation_rejected_by_macro_router")
                    continue
            elif factor is None or factor.side is not plan.side:
                self._binc("counter_macro_local_continuation_without_common_support")
                continue
            else:
                self._binc("counter_macro_local_continuation_allowed_by_common_factor")

            self._claim_episode(plan)
            output.append(plan)
            self._binc("local_continuation_plan_allowed")
            self._local_bundle_trace.append(
                {
                    "scenario_kind": "local_auction_continuation_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "macro_side": None if macro_side is None else macro_side.name,
                    "factor_side": None if factor is None else factor.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": (
                        LOCAL_AUCTION_CONTINUATION_RULE,
                        COMMON_FACTOR_VETO_ONLY_RULE,
                        SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
                    ),
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        local: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            raw = self.local_continuation.on_bar(timeframe_minutes, bar)
            self._sync_audit("local_continuation", self.local_continuation)
            local = self._route_local_continuation(raw)

        core = super().on_bar(timeframe_minutes, bar)
        # The significant-response parent is the rejection owner.  Any legacy
        # acceptance plan is suppressed so the local continuation state machine
        # is the only continuation owner.
        rejection = [
            plan
            for plan in core
            if plan.scenario_path != ScenarioPath.ACCEPTANCE.value
        ]
        self._binc("legacy_acceptance_suppressed" if len(rejection) != len(core) else "core_rejection_bar")
        return sorted(
            rejection + local,
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
            + self.local_continuation.drain_trace()
            + self._local_bundle_trace
        )
        self._local_bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.local_continuation.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["local_auction_continuation"] = {
            "bundle_counts": dict(sorted(self._local_bundle_counts.items())),
            "engine": self.local_continuation.diagnostics,
            "local": self.local_continuation.local_continuation_diagnostics,
            "rules": (
                LOCAL_AUCTION_CONTINUATION_RULE,
                COMMON_FACTOR_VETO_ONLY_RULE,
                SIGNIFICANT_CONTINUATION_OBJECTIVE_RULE,
            ),
        }
        return output


class EasyChartRE1LocalAuctionStrategy(EasyChartRE1MarketFactorStrategy):
    """Propagate the completed four-symbol factor state before each bucket."""

    def _observe_common_factor(self) -> None:
        super()._observe_common_factor()
        for bundle in self.scenario_engines.values():
            setter = getattr(bundle, "set_market_factor_state", None)
            if setter is not None:
                setter(self.factor_state)


MultiScaleScenarioBundle = EasyChartRE1LocalAuctionContinuationBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
