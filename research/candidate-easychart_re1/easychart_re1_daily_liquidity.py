"""Previous-day extreme sweep, reclaim and first-response day trade.

This is an independent opportunity family, not a loosened version of an
existing setup.  The prior UTC day's completed high and low are objective
liquidity references known before the current day trades.  A five-minute bar
must sweep one reference and close back through it.  The first later completed
one-minute response must demonstrate that control remains on the reclaimed
side; otherwise the episode is over.

Entry, sweep-extreme stop and the first pre-existing objective are fixed before
submission.  The objective is the nearest still-unspent confirmed 5m/15m
opposing pivot or the prior-day midpoint when it is the nearer meaningful
rebalancing point.  No fitted distance, ATR multiplier, clock session, score,
trade cap, partial exit or stop movement is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import (
    ScenarioPath,
    StructureFamily,
    StructureZone,
    V5TradePlan,
)
from domain import Candle, Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_zones import ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


PREVIOUS_DAY_LIQUIDITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_COMPLETED_PREVIOUS_UTC_DAY_HIGH_AND_LOW_"
    "ARE_PREEXISTING_LIQUIDITY_REFERENCES_FOR_THE_CURRENT_DAY"
)
DAILY_SWEEP_RECLAIM_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_FIVE_MINUTE_SWEEP_AND_CLOSE_BACK_THROUGH_A_"
    "PREVIOUS_DAY_EXTREME_ARMS_ONE_FIRST_RESPONSE_REVERSAL_EPISODE"
)
DAILY_FIRST_RESPONSE_RULE = (
    "RESEARCH_HYPOTHESIS:THE_FIRST_LATER_COMPLETED_MINUTE_MUST_EXTEND_THE_RECLAIM_"
    "OR_SHOW_ADVERSE_FLOW_ABSORPTION_WHILE_CLOSING_ON_THE_INTENDED_SIDE"
)
DAILY_FIRST_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_DAILY_SWEEP_TARGET_IS_THE_NEAREST_"
    "PREEXISTING_UNSPENT_5M_15M_OPPOSING_SWING_OR_PRIOR_DAY_MIDPOINT"
)
for _rule in (
    PREVIOUS_DAY_LIQUIDITY_RULE,
    DAILY_SWEEP_RECLAIM_RULE,
    DAILY_FIRST_RESPONSE_RULE,
    DAILY_FIRST_OBJECTIVE_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class PreviousDayRange:
    session_date: date
    high: float
    low: float
    observed_time_ns: int

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0


@dataclass(slots=True)
class DailySweepSetup:
    setup_id: str
    trade_date: date
    side: Side
    level_kind: str
    level_price: float
    level_zone: StructureZone
    sweep_time_ns: int
    sweep_extreme: float
    sweep_close: float
    target_zone: StructureZone
    target_price: float
    terminal_reason: str | None = None


class DailyLiquiditySweepEngine:
    """One-symbol causal previous-day liquidity state machine."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.minimum_gross_rr = minimum_gross_rr
        self.higher_minutes = 1440
        self.decision_minutes = 5
        self.trigger_minutes = 1

        self.fifteen = NearestAnyPivotStructureBook(
            symbol,
            15,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.five = NearestAnyPivotStructureBook(
            symbol,
            5,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.flow = CausalFlowAnalyzer(tick_size)

        self._building_date: date | None = None
        self._building_high: float | None = None
        self._building_low: float | None = None
        self._building_last_close_ns: int | None = None
        self.previous_day: PreviousDayRange | None = None
        self._active: DailySweepSetup | None = None
        self._consumed: set[tuple[date, str]] = set()
        self._sequence = 0

        self.setups: list[DailySweepSetup] = []
        self.plans: list[V5TradePlan] = []
        self.audit_zones: list[StructureZone] = []
        self._zones: dict[str, StructureZone] = {}
        self._trace: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}

    @staticmethod
    def _utc_date(time_ns: int) -> date:
        return datetime.fromtimestamp(time_ns / 1_000_000_000, timezone.utc).date()

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _audit(self, zone: StructureZone) -> None:
        if zone.zone_id not in self._zones:
            self._zones[zone.zone_id] = zone
            self.audit_zones.append(zone)

    def _record(self, kind: str, time_ns: int, **values: Any) -> None:
        self._trace.append(
            {
                "scenario_kind": kind,
                "event_time_ns": time_ns,
                "symbol": self.symbol,
                **values,
            },
        )

    def _level_zone(
        self,
        day: PreviousDayRange,
        *,
        kind: str,
        side: ZoneSide,
        price: float,
        time_ns: int,
    ) -> StructureZone:
        source = f"PREVIOUS_DAY:{day.session_date.isoformat()}:{kind}"
        zone = StructureZone(
            zone_id=f"{source}:SNAP:{time_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=side,
            timeframe_minutes=1440,
            lower=price - self.tick_size * 0.5,
            upper=price + self.tick_size * 0.5,
            invalidation=(
                price - self.tick_size
                if side is ZoneSide.SUPPORT
                else price + self.tick_size
            ),
            impulse_extreme=price,
            formed_index=0,
            formed_time_ns=day.observed_time_ns,
            observed_time_ns=day.observed_time_ns,
            formation_indices=(),
            strength_ratio=1.0,
            source_structure_id=source,
            source_pivot_span=1,
        )
        self._audit(zone)
        return zone

    def _midpoint_zone(
        self,
        day: PreviousDayRange,
        side: Side,
        time_ns: int,
    ) -> StructureZone:
        zone_side = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        return self._level_zone(
            day,
            kind="PREVIOUS_DAY_MIDPOINT",
            side=zone_side,
            price=day.midpoint,
            time_ns=time_ns,
        )

    def _roll_daily_range(self, bar: Candle) -> None:
        bar_date = self._utc_date(bar.ts_close_ns)
        if self._building_date is None:
            self._building_date = bar_date
            self._building_high = bar.high
            self._building_low = bar.low
            self._building_last_close_ns = bar.ts_close_ns
            return

        if bar_date != self._building_date:
            if (
                self._building_high is not None
                and self._building_low is not None
                and self._building_last_close_ns is not None
            ):
                self.previous_day = PreviousDayRange(
                    session_date=self._building_date,
                    high=self._building_high,
                    low=self._building_low,
                    observed_time_ns=self._building_last_close_ns,
                )
                self._inc("previous_day_range_finalized")
                self._record(
                    "previous_day_range_finalized",
                    bar.ts_close_ns,
                    previous_date=self.previous_day.session_date.isoformat(),
                    previous_high=self.previous_day.high,
                    previous_low=self.previous_day.low,
                    previous_midpoint=self.previous_day.midpoint,
                    observed_time_ns=self.previous_day.observed_time_ns,
                    rule_provenance=PREVIOUS_DAY_LIQUIDITY_RULE,
                )
            if self._active is not None:
                self._finish(
                    self._active,
                    "daily_sweep_expired_at_new_utc_day",
                    bar.ts_close_ns,
                )
            self._building_date = bar_date
            self._building_high = bar.high
            self._building_low = bar.low
            self._building_last_close_ns = bar.ts_close_ns
            return

        assert self._building_high is not None
        assert self._building_low is not None
        self._building_high = max(self._building_high, bar.high)
        self._building_low = min(self._building_low, bar.low)
        self._building_last_close_ns = bar.ts_close_ns

    def _target_candidates(
        self,
        side: Side,
        bar: Candle,
    ) -> list[tuple[str, StructureZone, float]]:
        candidates: list[tuple[str, StructureZone, float]] = []
        for source, book in (("5M", self.five), ("15M", self.fifteen)):
            target = book.target_for(
                side,
                interaction_time_ns=bar.ts_close_ns,
                source_span=2,
                current_high=bar.high,
                current_low=bar.low,
            )
            if target is not None:
                candidates.append((source, target[0], target[1]))
        day = self.previous_day
        if day is not None:
            midpoint = day.midpoint
            ahead = midpoint > bar.close if side is Side.LONG else midpoint < bar.close
            if ahead:
                candidates.append(
                    (
                        "PREVIOUS_DAY_MIDPOINT",
                        self._midpoint_zone(day, side, bar.ts_close_ns),
                        midpoint,
                    ),
                )
        return candidates

    def _select_target(
        self,
        side: Side,
        bar: Candle,
    ) -> tuple[StructureZone, float, str] | None:
        candidates = self._target_candidates(side, bar)
        if not candidates:
            return None
        selected = (
            min(candidates, key=lambda item: (item[2], item[0], item[1].zone_id))
            if side is Side.LONG
            else max(candidates, key=lambda item: (item[2], item[0], item[1].zone_id))
        )
        self._audit(selected[1])
        return selected[1], selected[2], selected[0]

    def _finish(
        self,
        setup: DailySweepSetup,
        reason: str,
        time_ns: int,
        **values: Any,
    ) -> None:
        setup.terminal_reason = reason
        if self._active is setup:
            self._active = None
        self._inc(reason)
        self._record(
            reason,
            time_ns,
            setup_id=setup.setup_id,
            side=setup.side.name,
            level_kind=setup.level_kind,
            level_price=setup.level_price,
            sweep_time_ns=setup.sweep_time_ns,
            sweep_extreme=setup.sweep_extreme,
            **values,
        )

    def _discover_sweep(self, bar: Candle) -> None:
        day = self.previous_day
        if day is None or self._active is not None:
            return
        trade_date = self._utc_date(bar.ts_close_ns)
        swept_low = (
            bar.low <= day.low - self.tick_size
            and bar.close > day.low
        )
        swept_high = (
            bar.high >= day.high + self.tick_size
            and bar.close < day.high
        )
        if swept_low and swept_high:
            self._inc("five_minute_bar_swept_both_daily_extremes")
            return
        if not swept_low and not swept_high:
            return

        if swept_low:
            side = Side.LONG
            level_kind = "PREVIOUS_DAY_LOW"
            level_price = day.low
            zone_side = ZoneSide.SUPPORT
            sweep_extreme = bar.low
        else:
            side = Side.SHORT
            level_kind = "PREVIOUS_DAY_HIGH"
            level_price = day.high
            zone_side = ZoneSide.RESISTANCE
            sweep_extreme = bar.high

        key = (trade_date, level_kind)
        if key in self._consumed:
            self._inc("daily_level_already_consumed")
            return
        target = self._select_target(side, bar)
        if target is None:
            self._inc("daily_sweep_without_preexisting_objective")
            return
        target_zone, target_price, target_source = target
        valid = (
            sweep_extreme < bar.close < target_price
            if side is Side.LONG
            else target_price < bar.close < sweep_extreme
        )
        if not valid:
            self._inc("daily_sweep_invalid_initial_geometry")
            return

        self._sequence += 1
        level_zone = self._level_zone(
            day,
            kind=level_kind,
            side=zone_side,
            price=level_price,
            time_ns=bar.ts_close_ns,
        )
        setup = DailySweepSetup(
            setup_id=(
                f"DAILY_SWEEP:{self.symbol}:{trade_date.isoformat()}:"
                f"{level_kind}:{self._sequence}"
            ),
            trade_date=trade_date,
            side=side,
            level_kind=level_kind,
            level_price=level_price,
            level_zone=level_zone,
            sweep_time_ns=bar.ts_close_ns,
            sweep_extreme=sweep_extreme,
            sweep_close=bar.close,
            target_zone=target_zone,
            target_price=target_price,
        )
        self._consumed.add(key)
        self._active = setup
        self.setups.append(setup)
        self._inc("daily_sweep_reclaim_armed")
        self._record(
            "daily_sweep_reclaim_armed",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            side=side.name,
            level_kind=level_kind,
            level_price=level_price,
            sweep_extreme=sweep_extreme,
            sweep_close=bar.close,
            target_zone_id=target_zone.zone_id,
            target_price=target_price,
            target_source=target_source,
            rule_provenance=(
                PREVIOUS_DAY_LIQUIDITY_RULE,
                DAILY_SWEEP_RECLAIM_RULE,
                DAILY_FIRST_OBJECTIVE_RULE,
            ),
        )

    @staticmethod
    def _aligned_flow(side: Side, observation: FlowObservation) -> bool:
        return (
            observation.signed_taker_quote > 0.0 and observation.body > 0.0
            if side is Side.LONG
            else observation.signed_taker_quote < 0.0 and observation.body < 0.0
        )

    @staticmethod
    def _adverse_flow(side: Side, observation: FlowObservation) -> bool:
        return (
            observation.signed_taker_quote < 0.0
            if side is Side.LONG
            else observation.signed_taker_quote > 0.0
        )

    def _first_response_confirms(
        self,
        setup: DailySweepSetup,
        bar: Candle,
        observation: FlowObservation | None,
    ) -> tuple[bool, str]:
        intended_side = (
            bar.close > setup.level_price
            if setup.side is Side.LONG
            else bar.close < setup.level_price
        )
        if not intended_side:
            return False, "CLOSED_BACK_THROUGH_DAILY_LEVEL"

        extended_reclaim = (
            bar.close > setup.sweep_close
            if setup.side is Side.LONG
            else bar.close < setup.sweep_close
        )
        if extended_reclaim:
            return True, "FIRST_RESPONSE_EXTENDED_RECLAIM"

        if observation is None or not observation.active or not observation.directed:
            return False, "NO_EXTENDED_RECLAIM_OR_CAUSAL_FLOW"
        touches_level = bar.low <= setup.level_price if setup.side is Side.LONG else bar.high >= setup.level_price
        if touches_level and self._adverse_flow(setup.side, observation):
            return True, "FIRST_RESPONSE_ADVERSE_FLOW_ABSORBED"
        if observation.material_progress and self._aligned_flow(setup.side, observation):
            return True, "FIRST_RESPONSE_ALIGNED_INITIATIVE"
        return False, "FIRST_RESPONSE_FLOW_NOT_COHERENT"

    def _refresh_target(
        self,
        setup: DailySweepSetup,
        bar: Candle,
    ) -> None:
        target = self._select_target(setup.side, bar)
        if target is None:
            return
        zone, price, _ = target
        closer = (
            price < setup.target_price
            if setup.side is Side.LONG
            else price > setup.target_price
        )
        if closer:
            setup.target_zone = zone
            setup.target_price = price
            self._inc("daily_target_refreshed_to_nearer_preentry_objective")

    def _advance_first_response(
        self,
        bar: Candle,
        observation: FlowObservation | None,
    ) -> list[V5TradePlan]:
        setup = self._active
        if setup is None or bar.ts_close_ns <= setup.sweep_time_ns:
            return []

        stop = (
            setup.sweep_extreme - self.tick_size
            if setup.side is Side.LONG
            else setup.sweep_extreme + self.tick_size
        )
        stop_touched = bar.low <= stop if setup.side is Side.LONG else bar.high >= stop
        if stop_touched:
            self._finish(
                setup,
                "daily_sweep_stop_touched_before_first_response_entry",
                bar.ts_close_ns,
            )
            return []
        target_touched = (
            bar.high >= setup.target_price
            if setup.side is Side.LONG
            else bar.low <= setup.target_price
        )
        if target_touched:
            self._finish(
                setup,
                "daily_sweep_target_spent_before_first_response_entry",
                bar.ts_close_ns,
            )
            return []

        confirmed, mechanism = self._first_response_confirms(
            setup,
            bar,
            observation,
        )
        if not confirmed:
            self._finish(
                setup,
                "daily_sweep_first_response_failed",
                bar.ts_close_ns,
                response_open=bar.open,
                response_high=bar.high,
                response_low=bar.low,
                response_close=bar.close,
                mechanism=mechanism,
                rule_provenance=DAILY_FIRST_RESPONSE_RULE,
            )
            return []

        self._refresh_target(setup, bar)
        entry = bar.close
        target = setup.target_price
        risk = entry - stop if setup.side is Side.LONG else stop - entry
        reward = target - entry if setup.side is Side.LONG else entry - target
        if risk <= 0.0 or reward <= 0.0:
            self._finish(
                setup,
                "daily_sweep_nonpositive_preentry_geometry",
                bar.ts_close_ns,
                entry=entry,
                stop=stop,
                target=target,
            )
            return []
        gross_rr = reward / risk
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._finish(
                setup,
                "daily_sweep_below_minimum_gross_rr",
                bar.ts_close_ns,
                gross_rr=gross_rr,
            )
            return []

        self._sequence += 1
        plan = V5TradePlan(
            plan_id=f"daily-liquidity-{self.symbol}-{self._sequence:08d}",
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="DAILY_PREVIOUS_EXTREME_SWEEP_RECLAIM_FIRST_RESPONSE",
            side=setup.side,
            observed_time_ns=bar.ts_close_ns,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            setup_id=setup.setup_id,
            higher_zone_id=setup.level_zone.zone_id,
            higher_zone_kind=setup.level_zone.kind,
            higher_strength_ratio=1.0,
            lower_zone_id=setup.level_zone.zone_id,
            lower_zone_kind=setup.level_zone.kind,
            lower_strength_ratio=1.0,
            trigger_zone_id=setup.level_zone.zone_id,
            trigger_strength_ratio=(
                1.0
                if observation is None
                else max(1.0, observation.activity_ratio * observation.delta_ratio)
            ),
            target_zone_id=setup.target_zone.zone_id,
            target_zone_kind=setup.target_zone.kind,
            overlap_lower=setup.level_zone.lower,
            overlap_upper=setup.level_zone.upper,
            interaction_time_ns=setup.sweep_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path=ScenarioPath.REJECTION.value,
            setup_observed_time_ns=setup.level_zone.observed_time_ns,
            trigger_zone_kind=mechanism,
            source_rule_count=4,
            rule_provenance=(
                PREVIOUS_DAY_LIQUIDITY_RULE,
                DAILY_SWEEP_RECLAIM_RULE,
                DAILY_FIRST_RESPONSE_RULE,
                DAILY_FIRST_OBJECTIVE_RULE,
            ),
            scale_name="DAILY_LIQUIDITY",
            higher_timeframe_minutes=1440,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        self.plans.append(plan)
        self._finish(
            setup,
            "daily_sweep_plan_created",
            bar.ts_close_ns,
            plan_id=plan.plan_id,
            mechanism=mechanism,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            rule_provenance=(
                PREVIOUS_DAY_LIQUIDITY_RULE,
                DAILY_SWEEP_RECLAIM_RULE,
                DAILY_FIRST_RESPONSE_RULE,
                DAILY_FIRST_OBJECTIVE_RULE,
            ),
        )
        return [plan]

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 15:
            self.fifteen.on_bar(bar)
            self.fifteen.observe_price(bar)
            return []
        if timeframe_minutes == 5:
            self._roll_daily_range(bar)
            self.five.on_bar(bar)
            self._discover_sweep(bar)
            self.five.observe_price(bar)
            return []
        if timeframe_minutes != 1:
            return []
        observation = self.flow.observe(bar)
        return self._advance_first_response(bar, observation)

    def drain_trace(self) -> list[dict[str, Any]]:
        output, self._trace = self._trace, []
        return output

    def find_zone(self, zone_id: str) -> StructureZone | None:
        return self._zones.get(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._counts.items())),
            "active_setup": None if self._active is None else self._active.setup_id,
            "previous_day": (
                None
                if self.previous_day is None
                else {
                    "date": self.previous_day.session_date.isoformat(),
                    "high": self.previous_day.high,
                    "low": self.previous_day.low,
                    "midpoint": self.previous_day.midpoint,
                }
            ),
            "flow": self.flow.diagnostics,
            "rules": (
                PREVIOUS_DAY_LIQUIDITY_RULE,
                DAILY_SWEEP_RECLAIM_RULE,
                DAILY_FIRST_RESPONSE_RULE,
                DAILY_FIRST_OBJECTIVE_RULE,
            ),
        }
