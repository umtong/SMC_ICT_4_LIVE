"""Canonical mature-balance fakeout family for EasyChart RE1.

The market alternates between delivery and balance.  A balance is not every pair
of nearby support and resistance labels.  It becomes tradable only after both
sides have been independently defended, the later-created side is followed by a
completed traversal through the box midpoint, and the same canonical box then
produces its first outside sweep and close back inside.

This engine keeps exactly one box per symbol at a time.  Nested or later labels
cannot create parallel episodes while that box is unresolved.  A directional
matching-scale liquidity draw disables and resets balance discovery; after the
draw ends, both boundaries must be established in the new balance epoch.

Entry is the completed five-minute reclaim close after constituent one-minute
aggression remains adverse to the reversal, stop is beyond the sweep extreme,
and the opposite defense band is the immutable full-position objective.  There
is no fitted distance, ATR, session, score, time expiry, partial exit or moving
stop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_re1_horizontal import (
    REPEATED_DEFENSE_RULE,
    RepeatedDefenseLevel,
    RepeatedDefenseStructureBook,
)
from easychart_zones import ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


MATURE_BALANCE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_BALANCE_REQUIRES_PREEXISTING_REPEATED_DEFENSE_ON_BOTH_SIDES_AND_A_LATER_COMPLETED_TRAVERSAL_THROUGH_ITS_MIDPOINT"
)
CANONICAL_BALANCE_EPISODE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ONE_CANONICAL_SAME_SCALE_BOX_OWNS_THE_FIRST_OUTSIDE_SWEEP_AND_NO_NESTED_PAIR_MAY_CREATE_A_PARALLEL_EPISODE"
)
BALANCE_ADVERSE_FLOW_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "A_MATURE_BOX_RECLAIM_IS_EXECUTABLE_ONLY_WHILE_CONSTITUENT_TAKER_FLOW_REMAINS_ADVERSE_TO_THE_REVERSAL"
)
BALANCE_DELIVERY_EXCLUSION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_MATCHING_SCALE_DIRECTIONAL_DRAW_AND_A_LOCAL_BALANCE_FAKEOUT_ARE_MUTUALLY_EXCLUSIVE_AUCTION_STATES"
)
for _rule in (
    MATURE_BALANCE_RULE,
    CANONICAL_BALANCE_EPISODE_RULE,
    BALANCE_ADVERSE_FLOW_RULE,
    BALANCE_DELIVERY_EXCLUSION_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class PersistentDefenseStructureBook(RepeatedDefenseStructureBook):
    """Keep defense facts available; the box state owns their lifecycle."""

    def observe_price(self, bar: Candle) -> None:
        NearestAnyPivotStructureBook.observe_price(self, bar)


@dataclass(slots=True)
class MatureBalanceBox:
    box_id: str
    support: RepeatedDefenseLevel
    resistance: RepeatedDefenseLevel
    activation_time_ns: int
    later_side: ZoneSide
    midpoint: float
    mature_time_ns: int | None = None

    @property
    def lower(self) -> float:
        return self.support.upper

    @property
    def upper(self) -> float:
        return self.resistance.lower


@dataclass(slots=True)
class PendingBalanceSweep:
    setup_id: str
    box: MatureBalanceBox
    side: Side
    interaction_time_ns: int
    sweep_high: float
    sweep_low: float
    reclaim_close: float
    state: SetupState = SetupState.WAITING_RECLAIM
    terminal_reason: str | None = None


class MatureBalanceEngine:
    """One canonical five-minute box, one mature sweep/reclaim decision."""

    DECISION_MINUTES = 5
    TRIGGER_MINUTES = 1

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
        self.higher_minutes = self.DECISION_MINUTES
        self.decision_minutes = self.DECISION_MINUTES
        self.trigger_minutes = self.TRIGGER_MINUTES
        self.structure = PersistentDefenseStructureBook(
            symbol,
            self.DECISION_MINUTES,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.flow_analyzer = CausalFlowAnalyzer(tick_size)
        self.directional_draw_active = False
        self.balance_epoch_start_ns = 0
        self.active_box: MatureBalanceBox | None = None
        self._pending: PendingBalanceSweep | None = None
        self._claimed_box_ids: set[str] = set()
        self.setups: list[PendingBalanceSweep] = []
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

    def set_directional_draw(self, active: bool, time_ns: int) -> None:
        if active == self.directional_draw_active:
            return
        self.directional_draw_active = active
        if active:
            if self.active_box is not None or self._pending is not None:
                self._inc("balance_reset_by_directional_draw")
            self.active_box = None
            self._pending = None
            self.balance_epoch_start_ns = 0
            self._trace(
                "balance_disabled_by_directional_draw",
                time_ns,
                rule_provenance=BALANCE_DELIVERY_EXCLUSION_RULE,
            )
        else:
            self.balance_epoch_start_ns = time_ns
            self._trace(
                "new_balance_epoch_after_directional_draw",
                time_ns,
                rule_provenance=BALANCE_DELIVERY_EXCLUSION_RULE,
            )

    @staticmethod
    def _box_id(
        support: RepeatedDefenseLevel,
        resistance: RepeatedDefenseLevel,
    ) -> str:
        return f"MATURE_BOX:{support.level_id}|{resistance.level_id}"

    def _pair_candidates(self, bar: Candle) -> list[MatureBalanceBox]:
        levels = [
            level
            for level in self.structure.defense_levels
            if level.observed_time_ns >= self.balance_epoch_start_ns
            and level.observed_time_ns < bar.ts_close_ns
        ]
        supports = [item for item in levels if item.side is ZoneSide.SUPPORT]
        resistances = [item for item in levels if item.side is ZoneSide.RESISTANCE]
        output: list[MatureBalanceBox] = []
        for support in supports:
            for resistance in resistances:
                if support.pivot_span != resistance.pivot_span:
                    continue
                if support.upper >= resistance.lower:
                    continue
                box_id = self._box_id(support, resistance)
                if box_id in self._claimed_box_ids:
                    continue
                if not support.upper < bar.close < resistance.lower:
                    continue
                later_side = (
                    support.side
                    if support.observed_time_ns > resistance.observed_time_ns
                    else resistance.side
                )
                output.append(
                    MatureBalanceBox(
                        box_id=box_id,
                        support=support,
                        resistance=resistance,
                        activation_time_ns=bar.ts_close_ns,
                        later_side=later_side,
                        midpoint=(support.upper + resistance.lower) / 2.0,
                    )
                )
        return output

    @staticmethod
    def _candidate_priority(box: MatureBalanceBox) -> tuple[int, int, int, float, str]:
        later = max(
            box.support.observed_time_ns,
            box.resistance.observed_time_ns,
        )
        earlier = min(
            box.support.observed_time_ns,
            box.resistance.observed_time_ns,
        )
        width = box.resistance.lower - box.support.upper
        return (
            later,
            earlier,
            box.support.pivot_span,
            -width,
            box.box_id,
        )

    def _activate_box(self, bar: Candle) -> None:
        if self.directional_draw_active or self.active_box is not None:
            return
        candidates = self._pair_candidates(bar)
        if not candidates:
            return
        box = max(candidates, key=self._candidate_priority)
        self.active_box = box
        support_zone = self.structure._snapshot(box.support, bar.ts_close_ns)
        resistance_zone = self.structure._snapshot(box.resistance, bar.ts_close_ns)
        self._audit(support_zone)
        self._audit(resistance_zone)
        self._inc("canonical_balance_activated")
        self._trace(
            "canonical_balance_activated",
            bar.ts_close_ns,
            box_id=box.box_id,
            support_level_id=box.support.level_id,
            resistance_level_id=box.resistance.level_id,
            support_upper=box.support.upper,
            resistance_lower=box.resistance.lower,
            midpoint=box.midpoint,
            later_side=box.later_side.value,
            pivot_span=box.support.pivot_span,
            rule_provenance=(
                REPEATED_DEFENSE_RULE,
                MATURE_BALANCE_RULE,
                CANONICAL_BALANCE_EPISODE_RULE,
            ),
        )

    @staticmethod
    def _inside(box: MatureBalanceBox, close: float) -> bool:
        return box.lower < close < box.upper

    def _advance_immature_box(self, box: MatureBalanceBox, bar: Candle) -> None:
        if bar.ts_close_ns <= box.activation_time_ns:
            return
        if not self._inside(box, bar.close):
            self._claimed_box_ids.add(box.box_id)
            self.active_box = None
            self._inc("immature_box_accepted_break")
            return
        crossed = (
            bar.close > box.midpoint
            if box.later_side is ZoneSide.SUPPORT
            else bar.close < box.midpoint
        )
        if not crossed:
            self._inc("immature_box_waiting_midpoint_traversal")
            return
        box.mature_time_ns = bar.ts_close_ns
        self._inc("canonical_balance_matured")
        self._trace(
            "canonical_balance_matured",
            bar.ts_close_ns,
            box_id=box.box_id,
            close=bar.close,
            midpoint=box.midpoint,
            rule_provenance=MATURE_BALANCE_RULE,
        )

    def _sweep_side(
        self,
        box: MatureBalanceBox,
        bar: Candle,
    ) -> Side | None:
        support_sweep = (
            bar.low < box.support.lower
            and box.lower < bar.close < box.upper
        )
        resistance_sweep = (
            bar.high > box.resistance.upper
            and box.lower < bar.close < box.upper
        )
        if support_sweep and resistance_sweep:
            self._inc("mature_box_swept_both_sides_unresolved")
            return None
        if support_sweep:
            return Side.LONG
        if resistance_sweep:
            return Side.SHORT
        return None

    def _arm_sweep(
        self,
        box: MatureBalanceBox,
        side: Side,
        bar: Candle,
    ) -> None:
        setup_id = f"{box.box_id}:SWEEP:{side.name}:{bar.ts_close_ns}"
        setup = PendingBalanceSweep(
            setup_id=setup_id,
            box=box,
            side=side,
            interaction_time_ns=bar.ts_close_ns,
            sweep_high=bar.high,
            sweep_low=bar.low,
            reclaim_close=bar.close,
        )
        self.setups.append(setup)
        self._pending = setup
        self._claimed_box_ids.add(box.box_id)
        self.active_box = None
        self._inc("mature_balance_sweep_waiting_complete_flow")
        self._trace(
            "mature_balance_sweep_waiting_complete_flow",
            bar.ts_close_ns,
            setup_id=setup_id,
            box_id=box.box_id,
            side=side.name,
            sweep_high=bar.high,
            sweep_low=bar.low,
            reclaim_close=bar.close,
            target_price=(
                box.resistance.lower
                if side is Side.LONG
                else box.support.upper
            ),
            rule_provenance=(
                CANONICAL_BALANCE_EPISODE_RULE,
                BALANCE_ADVERSE_FLOW_RULE,
            ),
        )

    def _on_five(self, bar: Candle) -> None:
        self.structure.on_bar(bar)
        if self.directional_draw_active:
            self.structure.observe_price(bar)
            return
        self._activate_box(bar)
        box = self.active_box
        if box is not None:
            if box.mature_time_ns is None:
                self._advance_immature_box(box, bar)
            elif bar.ts_close_ns > box.mature_time_ns:
                side = self._sweep_side(box, bar)
                if side is not None:
                    self._arm_sweep(box, side, bar)
                elif not self._inside(box, bar.close):
                    self._claimed_box_ids.add(box.box_id)
                    self.active_box = None
                    self._inc("mature_box_accepted_break_without_reclaim")
        self.structure.observe_price(bar)

    @staticmethod
    def _adverse(side: Side, value: float) -> bool:
        return value < 0.0 if side is Side.LONG else value > 0.0

    def _flow_evidence(
        self,
        setup: PendingBalanceSweep,
    ) -> tuple[list[FlowObservation], float] | None:
        start = (
            setup.interaction_time_ns
            - self.DECISION_MINUTES * 60 * 1_000_000_000
        )
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
        setup = self._pending
        if setup is None or setup.interaction_time_ns > bar.ts_close_ns:
            return []
        self._pending = None
        if setup.interaction_time_ns != bar.ts_close_ns:
            setup.state = SetupState.UNRESOLVED
            setup.terminal_reason = "mature_balance_missed_complete_constituent"
            self._inc(setup.terminal_reason)
            return []
        evidence = self._flow_evidence(setup)
        if evidence is None:
            setup.state = SetupState.UNRESOLVED
            setup.terminal_reason = "mature_balance_without_adverse_flow"
            self._inc(setup.terminal_reason)
            return []
        observations, cumulative = evidence
        entry = setup.reclaim_close
        stop = (
            setup.sweep_low - self.tick_size
            if setup.side is Side.LONG
            else setup.sweep_high + self.tick_size
        )
        target = (
            setup.box.resistance.lower
            if setup.side is Side.LONG
            else setup.box.support.upper
        )
        risk = entry - stop if setup.side is Side.LONG else stop - entry
        reward = target - entry if setup.side is Side.LONG else entry - target
        if (
            risk <= 0.0
            or reward <= 0.0
            or reward / risk + 1e-12 < self.minimum_gross_rr
        ):
            setup.state = SetupState.NO_TRADE_GEOMETRY
            setup.terminal_reason = "mature_balance_no_trade_geometry"
            self._inc(setup.terminal_reason)
            return []

        source_level = (
            setup.box.support
            if setup.side is Side.LONG
            else setup.box.resistance
        )
        target_level = (
            setup.box.resistance
            if setup.side is Side.LONG
            else setup.box.support
        )
        source_zone = self.structure._snapshot(
            source_level,
            setup.interaction_time_ns,
        )
        target_zone = self.structure._snapshot(
            target_level,
            setup.interaction_time_ns,
        )
        self._audit(source_zone)
        self._audit(target_zone)
        gross_rr = reward / risk
        plan_id = f"PLAN:{setup.setup_id}:{bar.ts_close_ns}"
        plan = V5TradePlan(
            plan_id=plan_id,
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="MATURE_BALANCE_FIRST_SWEEP_RECLAIM",
            side=setup.side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=source_zone.zone_id,
            higher_zone_kind=source_zone.kind,
            higher_strength_ratio=source_zone.strength_ratio,
            lower_zone_id=source_zone.zone_id,
            lower_zone_kind=source_zone.kind,
            lower_strength_ratio=source_zone.strength_ratio,
            trigger_zone_id=source_zone.zone_id,
            trigger_strength_ratio=max(
                item.activity_ratio for item in observations
            ),
            target_zone_id=target_zone.zone_id,
            target_zone_kind=target_zone.kind,
            overlap_lower=source_zone.lower,
            overlap_upper=source_zone.upper,
            interaction_time_ns=setup.interaction_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path="REJECTION",
            setup_observed_time_ns=max(
                setup.box.support.observed_time_ns,
                setup.box.resistance.observed_time_ns,
            ),
            trigger_zone_kind="MATURE_BALANCE_5M_SWEEP_ADVERSE_FLOW_ABSORBED",
            source_rule_count=4,
            rule_provenance=(
                REPEATED_DEFENSE_RULE,
                MATURE_BALANCE_RULE,
                CANONICAL_BALANCE_EPISODE_RULE,
                BALANCE_ADVERSE_FLOW_RULE,
                BALANCE_DELIVERY_EXCLUSION_RULE,
            ),
            scale_name="MATURE_BALANCE",
            higher_timeframe_minutes=self.DECISION_MINUTES,
            decision_timeframe_minutes=self.DECISION_MINUTES,
            trigger_timeframe_minutes=self.TRIGGER_MINUTES,
        )
        self.plans.append(plan)
        setup.state = SetupState.PLANNED
        setup.terminal_reason = "mature_balance_planned"
        self._inc("mature_balance_plan_created")
        self._trace(
            "mature_balance_plan_created",
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
        return [plan]

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.DECISION_MINUTES:
            self._on_five(bar)
            return []
        if timeframe_minutes != self.TRIGGER_MINUTES:
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
        box = self.active_box
        return {
            "counts": dict(sorted(self._counts.items())),
            "directional_draw_active": self.directional_draw_active,
            "balance_epoch_start_ns": self.balance_epoch_start_ns,
            "active_box": None
            if box is None
            else {
                "box_id": box.box_id,
                "support_level_id": box.support.level_id,
                "resistance_level_id": box.resistance.level_id,
                "midpoint": box.midpoint,
                "mature_time_ns": box.mature_time_ns,
            },
            "pending": None
            if self._pending is None
            else self._pending.setup_id,
            "claimed_boxes": len(self._claimed_box_ids),
            "structure": dict(self.structure.diagnostics),
            "flow": self.flow_analyzer.diagnostics,
            "rules": (
                MATURE_BALANCE_RULE,
                CANONICAL_BALANCE_EPISODE_RULE,
                BALANCE_ADVERSE_FLOW_RULE,
                BALANCE_DELIVERY_EXCLUSION_RULE,
            ),
        }
