"""Persistent common-flow displacement, rebalance and continuation.

The reversal core and the continuation core solve different auctions.  This
family is armed only when the latest six broad BTC/ETH/SOL/XRP initiative events
form a persistent same-direction regime, the traded symbol participates in the
latest event, and a high-quality five-minute OB or FVG is born with aligned
constituent one-minute taker flow and net price progress.

The broad shock is allowed to relinquish its one-candle midpoint while price
rebalances the footprint.  The setup remains valid through a transitional
pullback, but is cancelled by a turbulent common-flow sequence or a new
persistent direction.  Entry is the first later touch with either immediate
absorption/re-initiative at the footprint or the first following completed
one-minute response.  Stop and target stay fixed at the full five-minute
formation invalidation and the nearest pre-existing unspent 5m/15m objective.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_channel_abstention import EasyChartRE1ChannelAbstentionBundle
from easychart_re1_factor_continuation import FactorContinuationEngine
from easychart_re1_flow import FlowObservation
from easychart_zones import PriceZone, ZoneKind
from execution_re1_factor_persistence import (
    CommonAuctionRegime,
    CommonAuctionSnapshot,
    EasyChartRE1PersistentFactorStrategy,
    PERSISTENT_COMMON_AUCTION_RULE,
)

PERSISTENT_CONTINUATION_FORMATION_RULE = (
    "RESEARCH_HYPOTHESIS:PERSISTENT_COMMON_INITIATIVE_PLUS_ALIGNED_FLOW_VALIDATED_FIVE_MINUTE_OB_OR_FVG_DEFINES_A_CONTINUATION_FOOTPRINT"
)
PERSISTENT_REBALANCE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_PERSISTENT_INITIATIVE_FOOTPRINT_MAY_SURVIVE_TRANSITIONAL_REBALANCE_BUT_NOT_TURBULENT_OR_OPPOSITE_COMMON_FLOW"
)
PERSISTENT_FIRST_RETURN_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:FIRST_LATER_FIVE_MINUTE_FOOTPRINT_TOUCH_ENTERS_ON_IMMEDIATE_CAUSAL_FLOW_RESPONSE_OR_THE_FIRST_FOLLOWING_COMPLETED_MINUTE_RESPONSE"
)
for _rule in (
    PERSISTENT_CONTINUATION_FORMATION_RULE,
    PERSISTENT_REBALANCE_RULE,
    PERSISTENT_FIRST_RETURN_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(slots=True)
class PersistentContinuationSetup:
    setup_id: str
    side: Side
    source_zone: PriceZone
    factor_event_time_ns: int
    factor_flips: int
    target_zone: StructureZone
    target_price: float
    state: SetupState = SetupState.WAITING_FOOTPRINT_RETEST
    first_touch_time_ns: int | None = None
    touch_high: float | None = None
    touch_low: float | None = None
    terminal_reason: str | None = None

    @property
    def active(self) -> bool:
        return self.terminal_reason is None


class PersistentContinuationEngine(FactorContinuationEngine):
    """Five-minute footprint continuation under a persistent broad auction."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
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
        self.setups: list[PersistentContinuationSetup] = []
        self._active: dict[str, PersistentContinuationSetup] = {}
        self._current_flow: FlowObservation | None = None

    def set_common_auction_snapshot(self, snapshot: CommonAuctionSnapshot) -> None:
        self.common_snapshot = snapshot

    def _formation_context(self, side: Side) -> bool:
        snapshot = self.common_snapshot
        return (
            snapshot.regime is CommonAuctionRegime.PERSISTENT
            and snapshot.active_matches_history
            and snapshot.side is side
            and self.symbol in snapshot.latest_agreeing_symbols
            and snapshot.latest_event_time_ns is not None
        )

    def _setup_context_survives(self, setup: PersistentContinuationSetup) -> bool:
        snapshot = self.common_snapshot
        if snapshot.regime is CommonAuctionRegime.TURBULENT:
            return False
        if snapshot.side is not setup.side:
            return False
        if snapshot.latest_event_time_ns is None:
            return False
        return snapshot.latest_event_time_ns >= setup.factor_event_time_ns

    def _on_fifteen(self, bar: Candle) -> None:
        self.local_structure.on_bar(bar)
        self.local_structure.observe_price(bar)

    def _on_five(self, bar: Candle) -> None:
        self.decision_structure.on_bar(bar)
        created = self.footprints.on_bar(bar)
        for zone in created:
            self._audit(zone)
            if zone.kind not in {ZoneKind.ORDER_BLOCK, ZoneKind.FVG}:
                continue
            if not zone.high_quality_by_size:
                continue
            side = self._zone_side(zone)
            if not self._formation_context(side):
                self._inc("persistent_footprint_without_formation_context")
                continue
            self._pending_formations[zone.zone_id] = zone
            self._inc(f"persistent_{zone.kind.value.lower()}_waiting_complete_flow")
            self._trace(
                "persistent_continuation_footprint_waiting_complete_flow",
                bar.ts_close_ns,
                zone_id=zone.zone_id,
                zone_kind=zone.kind.value,
                side=side.name,
                factor_event_time_ns=self.common_snapshot.latest_event_time_ns,
                factor_flips=self.common_snapshot.flips,
                agreeing_symbols=list(self.common_snapshot.latest_agreeing_symbols),
                rule_provenance=(
                    PERSISTENT_COMMON_AUCTION_RULE,
                    PERSISTENT_CONTINUATION_FORMATION_RULE,
                ),
            )
        self.decision_structure.observe_price(bar)

    def _formation_observations(self, zone: PriceZone) -> list[FlowObservation]:
        if zone.kind is ZoneKind.ORDER_BLOCK:
            start = zone.formed_time_ns
            end = zone.observed_time_ns
        else:
            indices = tuple(zone.formation_indices)
            if len(indices) != 3:
                return []
            middle_index = indices[1]
            bars = self.footprints.bars
            if middle_index <= 0 or middle_index >= len(bars):
                return []
            start = bars[middle_index - 1].ts_close_ns
            end = bars[middle_index].ts_close_ns
        return [
            item
            for item in self.flow_analyzer.history
            if start < item.ts_close_ns <= end
        ]

    def _formation_flow(
        self,
        zone: PriceZone,
        side: Side,
    ) -> tuple[list[FlowObservation], float, float] | None:
        observations = self._formation_observations(zone)
        if not observations:
            return None
        cumulative = sum(item.signed_taker_quote for item in observations)
        progress = self._progress(side, observations[0].open, observations[-1].close)
        aligned = [
            item
            for item in observations
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned(side, item.signed_taker_quote)
            and (item.body > 0.0 if side is Side.LONG else item.body < 0.0)
        ]
        if not self._aligned(side, cumulative) or progress <= 0.0 or not aligned:
            return None
        return observations, cumulative, progress

    def _finalize_pending_formations(self, bar: Candle) -> None:
        for zone_id, zone in list(self._pending_formations.items()):
            if zone.observed_time_ns > bar.ts_close_ns:
                continue
            self._pending_formations.pop(zone_id, None)
            if zone.observed_time_ns != bar.ts_close_ns:
                self._inc("persistent_pending_footprint_missed_same_close_constituent")
                continue
            side = self._zone_side(zone)
            if not self._formation_context(side):
                self._inc("persistent_formation_context_changed_before_completion")
                continue
            evidence = self._formation_flow(zone, side)
            if evidence is None:
                self._inc("persistent_footprint_rejected_without_aligned_flow")
                continue
            observations, cumulative, progress = evidence
            target = self._nearest_target(
                side,
                time_ns=bar.ts_close_ns,
                high=max(item.high for item in observations),
                low=min(item.low for item in observations),
            )
            if target is None:
                self._inc("persistent_footprint_without_preexisting_target")
                continue
            target_zone, target_price = target
            event_time = self.common_snapshot.latest_event_time_ns
            assert event_time is not None
            setup_id = f"PERSISTENT_CONTINUATION:{zone.zone_id}:{event_time}"
            if any(item.setup_id == setup_id for item in self.setups):
                continue
            setup = PersistentContinuationSetup(
                setup_id=setup_id,
                side=side,
                source_zone=zone,
                factor_event_time_ns=event_time,
                factor_flips=self.common_snapshot.flips,
                target_zone=target_zone,
                target_price=target_price,
            )
            self.setups.append(setup)
            self._active[setup_id] = setup
            self._inc("persistent_continuation_setup_armed")
            self._trace(
                "persistent_continuation_setup_armed",
                bar.ts_close_ns,
                setup_id=setup_id,
                side=side.name,
                source_zone_id=zone.zone_id,
                source_zone_kind=zone.kind.value,
                source_lower=zone.lower,
                source_upper=zone.upper,
                source_invalidation=zone.invalidation,
                target_zone_id=target_zone.zone_id,
                target_price=target_price,
                factor_event_time_ns=event_time,
                factor_flips=self.common_snapshot.flips,
                formation_bars=len(observations),
                cumulative_signed_taker_quote=cumulative,
                net_price_progress=progress,
                rule_provenance=(
                    PERSISTENT_COMMON_AUCTION_RULE,
                    PERSISTENT_CONTINUATION_FORMATION_RULE,
                ),
            )

    def _finish(
        self,
        setup: PersistentContinuationSetup,
        reason: str,
        time_ns: int,
        **values: Any,
    ) -> None:
        setup.terminal_reason = reason
        if reason == "persistent_continuation_planned":
            setup.state = SetupState.PLANNED
        elif "target_spent" in reason:
            setup.state = SetupState.TARGET_SPENT
        elif "invalidated" in reason:
            setup.state = SetupState.INVALIDATED
        elif "geometry" in reason:
            setup.state = SetupState.NO_TRADE_GEOMETRY
        else:
            setup.state = SetupState.UNRESOLVED
        self._active.pop(setup.setup_id, None)
        self._inc(reason)
        self._trace(
            reason,
            time_ns,
            setup_id=setup.setup_id,
            side=setup.side.name,
            source_zone_id=setup.source_zone.zone_id,
            **values,
        )

    @staticmethod
    def _target_touched(setup: PersistentContinuationSetup, bar: Candle) -> bool:
        return bar.high >= setup.target_price if setup.side is Side.LONG else bar.low <= setup.target_price

    @staticmethod
    def _stop_touched(setup: PersistentContinuationSetup, bar: Candle) -> bool:
        stop = setup.source_zone.invalidation
        return bar.low <= stop if setup.side is Side.LONG else bar.high >= stop

    @staticmethod
    def _opposite_delta(side: Side, value: float) -> bool:
        return value < 0.0 if side is Side.LONG else value > 0.0

    def _immediate_response(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
        observation: FlowObservation | None,
    ) -> tuple[bool, str]:
        if observation is None or not observation.active or not observation.directed:
            return False, "NONE"
        midpoint = (setup.source_zone.lower + setup.source_zone.upper) / 2.0
        reclaimed = bar.close > midpoint if setup.side is Side.LONG else bar.close < midpoint
        if not reclaimed:
            return False, "NONE"
        aligned_initiative = (
            observation.material_progress
            and self._aligned(setup.side, observation.signed_taker_quote)
            and (observation.body > 0.0 if setup.side is Side.LONG else observation.body < 0.0)
        )
        adverse_absorbed = self._opposite_delta(setup.side, observation.signed_taker_quote)
        if aligned_initiative:
            return True, "FIRST_TOUCH_REINITIATIVE"
        if adverse_absorbed:
            return True, "FIRST_TOUCH_ADVERSE_FLOW_ABSORBED"
        return False, "NONE"

    def _refresh_target(self, setup: PersistentContinuationSetup, bar: Candle) -> None:
        target = self._nearest_target(
            setup.side,
            time_ns=bar.ts_close_ns,
            high=bar.high,
            low=bar.low,
        )
        if target is None:
            return
        zone, price = target
        closer = price < setup.target_price if setup.side is Side.LONG else price > setup.target_price
        if closer:
            setup.target_zone = zone
            setup.target_price = price
            self._inc("persistent_continuation_target_refreshed")

    def _make_plan(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
        mechanism: str,
    ) -> V5TradePlan | None:
        self._refresh_target(setup, bar)
        entry = bar.close
        stop = setup.source_zone.invalidation
        target = setup.target_price
        risk = entry - stop if setup.side is Side.LONG else stop - entry
        reward = target - entry if setup.side is Side.LONG else entry - target
        if risk <= 0.0 or reward <= 0.0:
            self._inc("persistent_continuation_nonpositive_geometry")
            return None
        gross_rr = reward / risk
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._inc("persistent_continuation_below_minimum_gross_rr")
            return None
        plan_id = f"PLAN:{setup.setup_id}:{bar.ts_close_ns}"
        plan = V5TradePlan(
            plan_id=plan_id,
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="PERSISTENT_COMMON_FLOW_5M_FOOTPRINT_FIRST_RETURN",
            side=setup.side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=setup.source_zone.zone_id,
            higher_zone_kind=setup.source_zone.kind,
            higher_strength_ratio=setup.source_zone.strength_ratio,
            lower_zone_id=setup.source_zone.zone_id,
            lower_zone_kind=setup.source_zone.kind,
            lower_strength_ratio=setup.source_zone.strength_ratio,
            trigger_zone_id=setup.source_zone.zone_id,
            trigger_strength_ratio=setup.source_zone.strength_ratio,
            target_zone_id=setup.target_zone.zone_id,
            target_zone_kind=setup.target_zone.kind,
            overlap_lower=setup.source_zone.lower,
            overlap_upper=setup.source_zone.upper,
            interaction_time_ns=setup.first_touch_time_ns or setup.source_zone.observed_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path="ACCEPTANCE",
            setup_observed_time_ns=setup.source_zone.observed_time_ns,
            trigger_zone_kind=f"PERSISTENT_{mechanism}",
            source_rule_count=3,
            rule_provenance=(
                PERSISTENT_COMMON_AUCTION_RULE,
                PERSISTENT_CONTINUATION_FORMATION_RULE,
                PERSISTENT_REBALANCE_RULE,
                PERSISTENT_FIRST_RETURN_RULE,
            ),
            scale_name="PERSISTENT_CONTINUATION",
            higher_timeframe_minutes=15,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        self.plans.append(plan)
        self._inc("persistent_continuation_plan_created")
        return plan

    def _advance_setups(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        observation = self._current_flow
        for setup in list(self._active.values()):
            if not self._setup_context_survives(setup):
                self._finish(
                    setup,
                    "persistent_continuation_common_regime_changed",
                    bar.ts_close_ns,
                    regime=self.common_snapshot.regime.value,
                    latest_side=None if self.common_snapshot.side is None else self.common_snapshot.side.name,
                )
                continue
            if bar.ts_close_ns <= setup.source_zone.observed_time_ns:
                continue
            if self._target_touched(setup, bar):
                self._finish(setup, "persistent_continuation_target_spent_before_entry", bar.ts_close_ns)
                continue
            if self._stop_touched(setup, bar):
                self._finish(setup, "persistent_continuation_source_invalidated_before_entry", bar.ts_close_ns)
                continue

            if setup.first_touch_time_ns is None:
                touched = bar.low <= setup.source_zone.upper and bar.high >= setup.source_zone.lower
                if not touched:
                    continue
                setup.first_touch_time_ns = bar.ts_close_ns
                setup.touch_high = bar.high
                setup.touch_low = bar.low
                immediate, mechanism = self._immediate_response(setup, bar, observation)
                if immediate:
                    plan = self._make_plan(setup, bar, mechanism)
                    if plan is not None:
                        self._finish(setup, "persistent_continuation_planned", bar.ts_close_ns, plan_id=plan.plan_id)
                        output.append(plan)
                    else:
                        self._finish(setup, "persistent_continuation_no_trade_geometry", bar.ts_close_ns)
                    continue
                self._inc("persistent_continuation_first_touch_waiting_response")
                self._trace(
                    "persistent_continuation_first_touch_waiting_response",
                    bar.ts_close_ns,
                    setup_id=setup.setup_id,
                    side=setup.side.name,
                    touch_high=bar.high,
                    touch_low=bar.low,
                    rule_provenance=PERSISTENT_FIRST_RETURN_RULE,
                )
                continue

            if bar.ts_close_ns <= setup.first_touch_time_ns:
                continue
            assert setup.touch_high is not None and setup.touch_low is not None
            confirms_price = (
                bar.close > setup.touch_high
                if setup.side is Side.LONG
                else bar.close < setup.touch_low
            )
            confirms_flow = (
                observation is not None
                and observation.active
                and observation.directed
                and self._aligned(setup.side, observation.signed_taker_quote)
            )
            if not (confirms_price and confirms_flow):
                self._finish(
                    setup,
                    "persistent_continuation_first_response_failed",
                    bar.ts_close_ns,
                    response_close=bar.close,
                    touch_high=setup.touch_high,
                    touch_low=setup.touch_low,
                )
                continue
            plan = self._make_plan(setup, bar, "FIRST_FOLLOWING_RESPONSE")
            if plan is None:
                self._finish(setup, "persistent_continuation_no_trade_geometry", bar.ts_close_ns)
                continue
            self._finish(setup, "persistent_continuation_planned", bar.ts_close_ns, plan_id=plan.plan_id)
            output.append(plan)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 15:
            self._on_fifteen(bar)
            return []
        if timeframe_minutes == 5:
            self._on_five(bar)
            return []
        if timeframe_minutes != 1:
            return []
        self._current_flow = self.flow_analyzer.observe(bar)
        self._finalize_pending_formations(bar)
        return self._advance_setups(bar)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output.update(
            {
                "common_snapshot": {
                    "regime": self.common_snapshot.regime.value,
                    "side": None if self.common_snapshot.side is None else self.common_snapshot.side.name,
                    "active_side": None
                    if self.common_snapshot.active_side is None
                    else self.common_snapshot.active_side.name,
                    "flips": self.common_snapshot.flips,
                    "events": self.common_snapshot.events,
                    "latest_event_time_ns": self.common_snapshot.latest_event_time_ns,
                    "latest_agreeing_symbols": self.common_snapshot.latest_agreeing_symbols,
                },
                "persistent_rules": (
                    PERSISTENT_CONTINUATION_FORMATION_RULE,
                    PERSISTENT_REBALANCE_RULE,
                    PERSISTENT_FIRST_RETURN_RULE,
                ),
            }
        )
        return output


class PersistentContinuationMarketStrategy(EasyChartRE1PersistentFactorStrategy):
    """Publish the regime snapshot and preserve dedicated continuation plans."""

    def _factor_allows(self, plan: V5TradePlan) -> bool:
        if plan.scale_name == "PERSISTENT_CONTINUATION":
            self._pinc("dedicated_persistent_continuation_allowed")
            return True
        return super()._factor_allows(plan)


class EasyChartRE1PersistentContinuationBundle(EasyChartRE1ChannelAbstentionBundle):
    """Selective reversal core plus an independent persistent-flow pullback family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.persistent_continuation = PersistentContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["persistent_continuation"] = 0
        self._persistent_counts: dict[str, int] = {}
        self._persistent_trace: list[dict[str, Any]] = []

    def _pcinc(self, key: str) -> None:
        self._persistent_counts[key] = self._persistent_counts.get(key, 0) + 1

    def set_common_auction_snapshot(self, snapshot: CommonAuctionSnapshot) -> None:
        self.persistent_continuation.set_common_auction_snapshot(snapshot)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.persistent_continuation.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.persistent_continuation.plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        existing = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes not in {15, 5, 1}:
            return existing
        raw = self.persistent_continuation.on_bar(timeframe_minutes, bar)
        self._sync_audit("persistent_continuation", self.persistent_continuation)
        output: list[V5TradePlan] = []
        for plan in raw:
            if self._duplicate_episode(plan):
                self._pcinc("persistent_continuation_overlapped_existing_episode")
                continue
            if not self._route_plan(plan):
                self._pcinc("persistent_continuation_rejected_by_macro_context")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._pcinc("persistent_continuation_plan_allowed")
            self._persistent_trace.append(
                {
                    "scenario_kind": "persistent_continuation_plan_allowed",
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
            + self.persistent_continuation.drain_trace()
            + self._persistent_trace
        )
        self._persistent_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.persistent_continuation.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["persistent_continuation_family"] = {
            "routing_counts": dict(sorted(self._persistent_counts.items())),
            "engine": self.persistent_continuation.diagnostics,
            "rules": (
                PERSISTENT_CONTINUATION_FORMATION_RULE,
                PERSISTENT_REBALANCE_RULE,
                PERSISTENT_FIRST_RETURN_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1PersistentContinuationBundle
