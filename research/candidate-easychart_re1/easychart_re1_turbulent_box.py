"""Complete repeated-defense box fakeout for turbulent common-flow states.

A single repeated-defense band and one intervening pivot did not define a mature
contraction.  It produced many local reversals with remote opposing-pivot
objectives and artificial double-digit R plans.  The source's contraction stage
requires both sides of a visible range to be learned before their outside
liquidity can be harvested.

This refinement requires two independently pre-existing repeated-defense bands
of the same machine scale: support below resistance.  A later completed
five-minute candle must sweep one side, close back inside the full box, and not
have already touched the opposite side.  Constituent one-minute taker flow must
remain adverse to the intended reversal, proving that the reclaim occurred while
stop-flow aggression was being absorbed.  Entry is the completed reclaim close,
stop is beyond the sweep extreme, and the opposite defense band is the full
position objective.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState, V5TradePlan
from domain import Candle, Side
from easychart_re1_horizontal import RepeatedDefenseLevel
from easychart_re1_turbulent_contraction import (
    CONTRACTION_OBJECTIVE_RULE,
    TURBULENT_ADVERSE_FLOW_RULE,
    EasyChartRE1FullAuctionBundle,
    FullAuctionStateStrategy,
    TurbulentContractionEngine,
)
from easychart_zones import ZoneSide
from execution_re1_factor_persistence import CommonAuctionRegime, PERSISTENT_COMMON_AUCTION_RULE

COMPLETE_CONTRACTION_BOX_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_TURBULENT_FAKEOUT_REQUIRES_PREEXISTING_REPEATED_DEFENSE_ON_BOTH_SIDES_OF_THE_SAME_SCALE_AND_A_CLOSE_BACK_INSIDE_THE_BOX"
)
LOCAL_BOX_SELECTION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:WHEN_MULTIPLE_NESTED_BOXES_EXIST_THE_MOST_RECENT_SAME_SCALE_COMPLETE_BOX_OWNS_THE_CAUSAL_EPISODE"
)
for _rule in (COMPLETE_CONTRACTION_BOX_RULE, LOCAL_BOX_SELECTION_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(slots=True)
class TurbulentBoxSetup:
    setup_id: str
    side: Side
    swept_level: RepeatedDefenseLevel
    target_level: RepeatedDefenseLevel
    interaction_time_ns: int
    sweep_open: float
    sweep_high: float
    sweep_low: float
    sweep_close: float
    target_price: float
    state: SetupState = SetupState.WAITING_RECLAIM
    terminal_reason: str | None = None


class TurbulentBoxEngine(TurbulentContractionEngine):
    """One complete range box, one sweep, one reclaim and one objective."""

    @staticmethod
    def _target_for_level(level: RepeatedDefenseLevel) -> float:
        return level.lower if level.side is ZoneSide.RESISTANCE else level.upper

    @staticmethod
    def _box_width(swept: RepeatedDefenseLevel, target: RepeatedDefenseLevel) -> float:
        if swept.side is ZoneSide.SUPPORT:
            return target.lower - swept.upper
        return swept.lower - target.upper

    def _opposing_levels(
        self,
        swept: RepeatedDefenseLevel,
        bar: Candle,
    ) -> list[RepeatedDefenseLevel]:
        wanted = ZoneSide.RESISTANCE if swept.side is ZoneSide.SUPPORT else ZoneSide.SUPPORT
        output: list[RepeatedDefenseLevel] = []
        for level in self.structure._active_defense.values():
            if level.level_id == swept.level_id:
                continue
            if level.side is not wanted or level.pivot_span != swept.pivot_span:
                continue
            if level.observed_time_ns >= bar.ts_close_ns:
                continue
            if swept.side is ZoneSide.SUPPORT:
                inside = swept.upper < bar.close < level.lower
                target_not_spent = bar.high < level.lower
            else:
                inside = level.upper < bar.close < swept.lower
                target_not_spent = bar.low > level.upper
            if inside and target_not_spent and self._box_width(swept, level) > self.tick_size:
                output.append(level)
        return output

    def _candidate_boxes(
        self,
        bar: Candle,
    ) -> list[tuple[RepeatedDefenseLevel, RepeatedDefenseLevel]]:
        output: list[tuple[RepeatedDefenseLevel, RepeatedDefenseLevel]] = []
        for swept in self.structure._active_defense.values():
            if swept.observed_time_ns >= bar.ts_close_ns or not self._fakeout(swept, bar):
                continue
            targets = self._opposing_levels(swept, bar)
            if not targets:
                self._inc("turbulent_sweep_without_complete_opposite_defense")
                continue
            target = (
                min(targets, key=lambda item: (item.lower, -item.observed_time_ns, item.level_id))
                if swept.side is ZoneSide.SUPPORT
                else max(targets, key=lambda item: (item.upper, item.observed_time_ns, item.level_id))
            )
            output.append((swept, target))
        return output

    @staticmethod
    def _pair_recency(pair: tuple[RepeatedDefenseLevel, RepeatedDefenseLevel]) -> tuple[int, int, float, str, str]:
        swept, target = pair
        width = (
            target.lower - swept.upper
            if swept.side is ZoneSide.SUPPORT
            else swept.lower - target.upper
        )
        return (
            max(swept.observed_time_ns, target.observed_time_ns),
            min(swept.observed_time_ns, target.observed_time_ns),
            -width,
            swept.level_id,
            target.level_id,
        )

    def _on_five(self, bar: Candle) -> None:
        self.structure.on_bar(bar)
        if self.common_snapshot.regime is CommonAuctionRegime.TURBULENT:
            pairs = self._candidate_boxes(bar)
            by_side: dict[Side, list[tuple[RepeatedDefenseLevel, RepeatedDefenseLevel]]] = {
                Side.LONG: [],
                Side.SHORT: [],
            }
            for pair in pairs:
                by_side[self._side(pair[0])].append(pair)
            for side, candidates in by_side.items():
                if not candidates:
                    continue
                swept, target = max(candidates, key=self._pair_recency)
                target_price = self._target_for_level(target)
                setup_id = (
                    f"TURBULENT_BOX:{swept.level_id}|{target.level_id}:"
                    f"{bar.ts_close_ns}"
                )
                setup = TurbulentBoxSetup(
                    setup_id=setup_id,
                    side=side,
                    swept_level=swept,
                    target_level=target,
                    interaction_time_ns=bar.ts_close_ns,
                    sweep_open=bar.open,
                    sweep_high=bar.high,
                    sweep_low=bar.low,
                    sweep_close=bar.close,
                    target_price=target_price,
                )
                self.setups.append(setup)
                self._pending[setup_id] = setup
                swept_zone = self.structure._snapshot(swept, bar.ts_close_ns)
                target_zone = self.structure._snapshot(target, bar.ts_close_ns)
                self._audit(swept_zone)
                self._audit(target_zone)
                self._inc("turbulent_complete_box_reclaim_waiting_complete_flow")
                self._trace(
                    "turbulent_complete_box_reclaim_waiting_complete_flow",
                    bar.ts_close_ns,
                    setup_id=setup_id,
                    side=side.name,
                    swept_level_id=swept.level_id,
                    target_level_id=target.level_id,
                    support_level_id=(swept.level_id if side is Side.LONG else target.level_id),
                    resistance_level_id=(target.level_id if side is Side.LONG else swept.level_id),
                    box_width=self._box_width(swept, target),
                    sweep_high=bar.high,
                    sweep_low=bar.low,
                    sweep_close=bar.close,
                    target_price=target_price,
                    common_flips=self.common_snapshot.flips,
                    rule_provenance=(
                        PERSISTENT_COMMON_AUCTION_RULE,
                        COMPLETE_CONTRACTION_BOX_RULE,
                        LOCAL_BOX_SELECTION_RULE,
                    ),
                )
        self.structure.observe_price(bar)

    def _finalize(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for setup_id, setup in list(self._pending.items()):
            if setup.interaction_time_ns > bar.ts_close_ns:
                continue
            self._pending.pop(setup_id, None)
            if setup.interaction_time_ns != bar.ts_close_ns:
                setup.state = SetupState.UNRESOLVED
                setup.terminal_reason = "turbulent_box_missed_complete_constituent"
                self._inc(setup.terminal_reason)
                continue
            evidence = self._flow_evidence(setup)
            if evidence is None:
                setup.state = SetupState.UNRESOLVED
                setup.terminal_reason = "turbulent_box_without_adverse_flow"
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
                setup.terminal_reason = "turbulent_box_no_trade_geometry"
                self._inc(setup.terminal_reason)
                continue
            swept_zone = self.structure._snapshot(setup.swept_level, setup.interaction_time_ns)
            target_zone = self.structure._snapshot(setup.target_level, setup.interaction_time_ns)
            self._audit(swept_zone)
            self._audit(target_zone)
            gross_rr = reward / risk
            plan_id = f"PLAN:{setup.setup_id}:{bar.ts_close_ns}"
            plan = V5TradePlan(
                plan_id=plan_id,
                causal_event_id=setup.setup_id,
                symbol=self.symbol,
                family="TURBULENT_COMPLETE_BOX_SWEEP_RECLAIM",
                side=setup.side,
                observed_time_ns=bar.ts_close_ns,
                entry=entry,
                stop=stop,
                target=target,
                gross_rr=gross_rr,
                setup_id=setup.setup_id,
                higher_zone_id=swept_zone.zone_id,
                higher_zone_kind=swept_zone.kind,
                higher_strength_ratio=swept_zone.strength_ratio,
                lower_zone_id=swept_zone.zone_id,
                lower_zone_kind=swept_zone.kind,
                lower_strength_ratio=swept_zone.strength_ratio,
                trigger_zone_id=swept_zone.zone_id,
                trigger_strength_ratio=max(item.activity_ratio for item in observations),
                target_zone_id=target_zone.zone_id,
                target_zone_kind=target_zone.kind,
                overlap_lower=swept_zone.lower,
                overlap_upper=swept_zone.upper,
                interaction_time_ns=setup.interaction_time_ns,
                trigger_time_ns=bar.ts_close_ns,
                scenario_path="REJECTION",
                setup_observed_time_ns=max(
                    setup.swept_level.observed_time_ns,
                    setup.target_level.observed_time_ns,
                ),
                trigger_zone_kind="TURBULENT_COMPLETE_BOX_5M_SWEEP_ADVERSE_FLOW_ABSORBED",
                source_rule_count=4,
                rule_provenance=(
                    PERSISTENT_COMMON_AUCTION_RULE,
                    COMPLETE_CONTRACTION_BOX_RULE,
                    TURBULENT_ADVERSE_FLOW_RULE,
                    CONTRACTION_OBJECTIVE_RULE,
                    LOCAL_BOX_SELECTION_RULE,
                ),
                scale_name="TURBULENT_CONTRACTION",
                higher_timeframe_minutes=5,
                decision_timeframe_minutes=5,
                trigger_timeframe_minutes=1,
            )
            self.plans.append(plan)
            output.append(plan)
            setup.state = SetupState.PLANNED
            setup.terminal_reason = "turbulent_box_planned"
            self._inc("turbulent_complete_box_plan_created")
            self._trace(
                "turbulent_complete_box_plan_created",
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

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["complete_box_policy"] = {
            "name": "SAME_SCALE_SUPPORT_AND_RESISTANCE_REPEATED_DEFENSE",
            "rules": (COMPLETE_CONTRACTION_BOX_RULE, LOCAL_BOX_SELECTION_RULE),
        }
        return output


class EasyChartRE1BoxAuctionBundle(EasyChartRE1FullAuctionBundle):
    """Use complete-box turbulent reversals instead of one-sided defense bands."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.turbulent_contraction = TurbulentBoxEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["turbulent_contraction"] = 0


MultiScaleScenarioBundle = EasyChartRE1BoxAuctionBundle
StrategyClass = FullAuctionStateStrategy
