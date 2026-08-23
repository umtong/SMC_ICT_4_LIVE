"""Turbulent common-flow contraction sweep and reclaim family.

Persistent broad initiative owns continuation.  When the latest common-flow
sequence repeatedly changes direction, the account does not fade arbitrary
trend lines or one-minute absorption.  It waits for the complete Fakeout/Trap
auction described by the source:

1. a five-minute repeated-defense area already exists from two overlapping
   wick bands with an intervening opposite pivot;
2. a later completed five-minute candle sweeps beyond that boundary and closes
   back through the inside edge;
3. constituent one-minute taker flow is adverse to the intended reversal, so
   the reclaim occurred despite the stop-flow aggression rather than because
   the tape was already moving in the trade direction;
4. entry is the completed reclaim close, stop is beyond the sweep extreme, and
   target is the opposite pivot of the pre-existing contraction.

This is a dedicated turbulent-state mechanism, not a volume filter added to all
setups.  No time-of-day, ATR, fitted percentile, score, partial exit or moving
stop is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_re1_horizontal import RepeatedDefenseLevel, RepeatedDefenseStructureBook
from easychart_re1_persistent_continuation import (
    EasyChartRE1PersistentContinuationBundle,
    PersistentContinuationMarketStrategy,
)
from easychart_zones import ZoneSide
from execution_re1_factor_persistence import (
    CommonAuctionRegime,
    CommonAuctionSnapshot,
    PERSISTENT_COMMON_AUCTION_RULE,
)

TURBULENT_CONTRACTION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:TURBULENT_COMMON_FLOW_TRADES_ONLY_A_PREEXISTING_REPEATED_DEFENSE_CONTRACTION_SWEEP_AND_FIVE_MINUTE_RECLAIM"
)
TURBULENT_ADVERSE_FLOW_RULE = (
    "RESEARCH_HYPOTHESIS:CONTRACTION_RECLAIM_REQUIRES_ADVERSE_CONSTITUENT_TAKER_FLOW_SO_THE_EVENT_REPRESENTS_ABSORPTION_NOT_PREEXISTING_REINITIATIVE"
)
CONTRACTION_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_OPPOSITE_PIVOT_INSIDE_THE_REPEATED_DEFENSE_CONTRACTION_IS_THE_FIRST_FULL_POSITION_OBJECTIVE"
)
for _rule in (
    TURBULENT_CONTRACTION_RULE,
    TURBULENT_ADVERSE_FLOW_RULE,
    CONTRACTION_OBJECTIVE_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(slots=True)
class TurbulentContractionSetup:
    setup_id: str
    side: Side
    level: RepeatedDefenseLevel
    interaction_time_ns: int
    sweep_open: float
    sweep_high: float
    sweep_low: float
    sweep_close: float
    target_price: float
    state: SetupState = SetupState.WAITING_RECLAIM
    terminal_reason: str | None = None


class TurbulentContractionEngine:
    """Five-minute contraction fakeout with constituent adverse taker flow."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        *,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.minimum_gross_rr = minimum_gross_rr
        self.higher_minutes = 15
        self.decision_minutes = 5
        self.trigger_minutes = 1
        self.structure = RepeatedDefenseStructureBook(
            symbol,
            self.decision_minutes,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.flow_analyzer = CausalFlowAnalyzer(tick_size)
        self.common_snapshot = CommonAuctionSnapshot(
            CommonAuctionRegime.UNKNOWN,
            None,
            0,
            0,
            None,
            (),
            None,
            None,
        )
        self._pending: dict[str, TurbulentContractionSetup] = {}
        self.setups: list[TurbulentContractionSetup] = []
        self.plans: list[V5TradePlan] = []
        self.audit_zones: list[Any] = []
        self._zone_lookup: dict[str, Any] = {}
        self._trace_records: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}
        self.detectors: dict[int, Any] = {}

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _trace(self, kind: str, time_ns: int, **values: Any) -> None:
        self._trace_records.append(
            {
                "scenario_kind": kind,
                "event_time_ns": time_ns,
                "symbol": self.symbol,
                **values,
            }
        )

    def _audit(self, zone: Any) -> None:
        zone_id = getattr(zone, "zone_id", None)
        if zone_id and zone_id not in self._zone_lookup:
            self._zone_lookup[zone_id] = zone
            self.audit_zones.append(zone)

    def set_common_auction_snapshot(self, snapshot: CommonAuctionSnapshot) -> None:
        self.common_snapshot = snapshot

    @staticmethod
    def _side(level: RepeatedDefenseLevel) -> Side:
        return Side.LONG if level.side is ZoneSide.SUPPORT else Side.SHORT

    @staticmethod
    def _fakeout(level: RepeatedDefenseLevel, bar: Candle) -> bool:
        if level.side is ZoneSide.SUPPORT:
            return bar.low < level.lower and bar.close > level.upper
        return bar.high > level.upper and bar.close < level.lower

    def _target_price(self, level: RepeatedDefenseLevel) -> float | None:
        pivot = next(
            (
                item
                for item in self.structure.pivots
                if item.pivot_id == level.opposite_pivot_id
            ),
            None,
        )
        return None if pivot is None else pivot.price

    def _on_five(self, bar: Candle) -> None:
        self.structure.on_bar(bar)
        if self.common_snapshot.regime is CommonAuctionRegime.TURBULENT:
            for level in list(self.structure._active_defense.values()):
                if level.observed_time_ns >= bar.ts_close_ns:
                    continue
                if not self._fakeout(level, bar):
                    continue
                target = self._target_price(level)
                if target is None:
                    self._inc("turbulent_contraction_missing_opposite_pivot")
                    continue
                side = self._side(level)
                reward = target - bar.close if side is Side.LONG else bar.close - target
                if reward <= 0.0:
                    self._inc("turbulent_contraction_target_not_opposite")
                    continue
                setup_id = f"TURBULENT_CONTRACTION:{level.level_id}:{bar.ts_close_ns}"
                setup = TurbulentContractionSetup(
                    setup_id=setup_id,
                    side=side,
                    level=level,
                    interaction_time_ns=bar.ts_close_ns,
                    sweep_open=bar.open,
                    sweep_high=bar.high,
                    sweep_low=bar.low,
                    sweep_close=bar.close,
                    target_price=target,
                )
                self.setups.append(setup)
                self._pending[setup_id] = setup
                snapshot = self.structure._snapshot(level, bar.ts_close_ns)
                self._audit(snapshot)
                self._inc("turbulent_contraction_reclaim_waiting_complete_flow")
                self._trace(
                    "turbulent_contraction_reclaim_waiting_complete_flow",
                    bar.ts_close_ns,
                    setup_id=setup_id,
                    side=side.name,
                    level_id=level.level_id,
                    level_lower=level.lower,
                    level_upper=level.upper,
                    sweep_high=bar.high,
                    sweep_low=bar.low,
                    sweep_close=bar.close,
                    target_price=target,
                    common_flips=self.common_snapshot.flips,
                    rule_provenance=(
                        PERSISTENT_COMMON_AUCTION_RULE,
                        TURBULENT_CONTRACTION_RULE,
                    ),
                )
        self.structure.observe_price(bar)

    @staticmethod
    def _adverse(side: Side, value: float) -> bool:
        return value < 0.0 if side is Side.LONG else value > 0.0

    def _flow_evidence(
        self,
        setup: TurbulentContractionSetup,
    ) -> tuple[list[FlowObservation], float] | None:
        start = setup.interaction_time_ns - self.decision_minutes * 60 * 1_000_000_000
        observations = [
            item
            for item in self.flow_analyzer.history
            if start < item.ts_close_ns <= setup.interaction_time_ns
        ]
        if not observations:
            return None
        cumulative = sum(item.signed_taker_quote for item in observations)
        adverse = [
            item
            for item in observations
            if item.active
            and item.directed
            and self._adverse(setup.side, item.signed_taker_quote)
        ]
        if not adverse or not self._adverse(setup.side, cumulative):
            return None
        return observations, cumulative

    def _finalize(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for setup_id, setup in list(self._pending.items()):
            if setup.interaction_time_ns > bar.ts_close_ns:
                continue
            self._pending.pop(setup_id, None)
            if setup.interaction_time_ns != bar.ts_close_ns:
                setup.state = SetupState.UNRESOLVED
                setup.terminal_reason = "turbulent_contraction_missed_complete_constituent"
                self._inc(setup.terminal_reason)
                continue
            evidence = self._flow_evidence(setup)
            if evidence is None:
                setup.state = SetupState.UNRESOLVED
                setup.terminal_reason = "turbulent_contraction_without_adverse_flow"
                self._inc(setup.terminal_reason)
                continue
            observations, cumulative = evidence
            entry = setup.sweep_close
            stop = (
                setup.sweep_low - self.tick_size
                if setup.side is Side.LONG
                else setup.sweep_high + self.tick_size
            )
            target = setup.target_price
            risk = entry - stop if setup.side is Side.LONG else stop - entry
            reward = target - entry if setup.side is Side.LONG else entry - target
            if risk <= 0.0 or reward <= 0.0 or reward / risk + 1e-12 < self.minimum_gross_rr:
                setup.state = SetupState.NO_TRADE_GEOMETRY
                setup.terminal_reason = "turbulent_contraction_no_trade_geometry"
                self._inc(setup.terminal_reason)
                continue
            level_zone = self.structure._snapshot(setup.level, setup.interaction_time_ns)
            target_pivot = next(
                item
                for item in self.structure.pivots
                if item.pivot_id == setup.level.opposite_pivot_id
            )
            target_zone = self.structure._horizontal_snapshot(
                target_pivot,
                setup.interaction_time_ns,
            )
            self._audit(level_zone)
            self._audit(target_zone)
            gross_rr = reward / risk
            plan_id = f"PLAN:{setup.setup_id}:{bar.ts_close_ns}"
            plan = V5TradePlan(
                plan_id=plan_id,
                causal_event_id=setup.setup_id,
                symbol=self.symbol,
                family="TURBULENT_REPEATED_DEFENSE_SWEEP_RECLAIM",
                side=setup.side,
                observed_time_ns=bar.ts_close_ns,
                entry=entry,
                stop=stop,
                target=target,
                gross_rr=gross_rr,
                setup_id=setup.setup_id,
                higher_zone_id=level_zone.zone_id,
                higher_zone_kind=level_zone.kind,
                higher_strength_ratio=level_zone.strength_ratio,
                lower_zone_id=level_zone.zone_id,
                lower_zone_kind=level_zone.kind,
                lower_strength_ratio=level_zone.strength_ratio,
                trigger_zone_id=level_zone.zone_id,
                trigger_strength_ratio=max(item.activity_ratio for item in observations),
                target_zone_id=target_zone.zone_id,
                target_zone_kind=target_zone.kind,
                overlap_lower=level_zone.lower,
                overlap_upper=level_zone.upper,
                interaction_time_ns=setup.interaction_time_ns,
                trigger_time_ns=bar.ts_close_ns,
                scenario_path="REJECTION",
                setup_observed_time_ns=setup.level.observed_time_ns,
                trigger_zone_kind="TURBULENT_5M_SWEEP_ADVERSE_FLOW_ABSORBED",
                source_rule_count=3,
                rule_provenance=(
                    PERSISTENT_COMMON_AUCTION_RULE,
                    TURBULENT_CONTRACTION_RULE,
                    TURBULENT_ADVERSE_FLOW_RULE,
                    CONTRACTION_OBJECTIVE_RULE,
                ),
                scale_name="TURBULENT_CONTRACTION",
                higher_timeframe_minutes=5,
                decision_timeframe_minutes=5,
                trigger_timeframe_minutes=1,
            )
            self.plans.append(plan)
            output.append(plan)
            setup.state = SetupState.PLANNED
            setup.terminal_reason = "turbulent_contraction_planned"
            self._inc("turbulent_contraction_plan_created")
            self._trace(
                "turbulent_contraction_plan_created",
                bar.ts_close_ns,
                setup_id=setup.setup_id,
                plan_id=plan_id,
                side=setup.side.name,
                entry=entry,
                stop=stop,
                target=target,
                gross_rr=gross_rr,
                cumulative_signed_taker_quote=cumulative,
                constituent_bars=len(observations),
                rule_provenance=plan.rule_provenance,
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 5:
            self._on_five(bar)
            return []
        if timeframe_minutes != 1:
            return []
        self.flow_analyzer.observe(bar)
        return self._finalize(bar)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self._trace_records
        self._trace_records = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self._zone_lookup.get(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._counts.items())),
            "active_defense_levels": len(self.structure._active_defense),
            "pending": len(self._pending),
            "structure": dict(self.structure.diagnostics),
            "flow": self.flow_analyzer.diagnostics,
            "common_snapshot": {
                "regime": self.common_snapshot.regime.value,
                "side": None if self.common_snapshot.side is None else self.common_snapshot.side.name,
                "flips": self.common_snapshot.flips,
                "events": self.common_snapshot.events,
            },
            "rules": (
                TURBULENT_CONTRACTION_RULE,
                TURBULENT_ADVERSE_FLOW_RULE,
                CONTRACTION_OBJECTIVE_RULE,
            ),
        }


class FullAuctionStateStrategy(PersistentContinuationMarketStrategy):
    def _factor_allows(self, plan: V5TradePlan) -> bool:
        if plan.scale_name == "TURBULENT_CONTRACTION":
            self._pinc("dedicated_turbulent_contraction_allowed")
            return True
        return super()._factor_allows(plan)


class EasyChartRE1FullAuctionBundle(EasyChartRE1PersistentContinuationBundle):
    """Persistent continuation plus turbulent contraction control transfer."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.turbulent_contraction = TurbulentContractionEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["turbulent_contraction"] = 0
        self._turbulent_counts: dict[str, int] = {}
        self._turbulent_trace: list[dict[str, Any]] = []

    def _tcinc(self, key: str) -> None:
        self._turbulent_counts[key] = self._turbulent_counts.get(key, 0) + 1

    def set_common_auction_snapshot(self, snapshot: CommonAuctionSnapshot) -> None:
        super().set_common_auction_snapshot(snapshot)
        self.turbulent_contraction.set_common_auction_snapshot(snapshot)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.turbulent_contraction.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.turbulent_contraction.plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        existing = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes not in {5, 1}:
            return existing
        raw = self.turbulent_contraction.on_bar(timeframe_minutes, bar)
        self._sync_audit("turbulent_contraction", self.turbulent_contraction)
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._tcinc("turbulent_contraction_overlapped_existing_episode")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._tcinc("turbulent_contraction_plan_allowed")
            self._turbulent_trace.append(
                {
                    "scenario_kind": "turbulent_contraction_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": plan.rule_provenance,
                }
            )
        return existing + output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            super().drain_trace()
            + self.turbulent_contraction.drain_trace()
            + self._turbulent_trace
        )
        self._turbulent_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.turbulent_contraction.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["turbulent_contraction_family"] = {
            "routing_counts": dict(sorted(self._turbulent_counts.items())),
            "engine": self.turbulent_contraction.diagnostics,
            "rules": (
                TURBULENT_CONTRACTION_RULE,
                TURBULENT_ADVERSE_FLOW_RULE,
                CONTRACTION_OBJECTIVE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FullAuctionBundle
