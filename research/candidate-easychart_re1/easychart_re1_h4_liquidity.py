"""Completed four-hour auction extreme sweep and first-response reversal.

A prior completed four-hour range is a fully observed higher-timeframe auction,
not a rolling future-dependent window.  Its high, low and midpoint remain
available during the next four-hour auction.  The first five-minute sweep and
close back through one extreme may arm a reversal, but the order is submitted
only after the first later completed one-minute response confirms that the
reclaim still controls the boundary.

This family broadens the opportunity set without loosening the existing local
patterns.  It captures a different causal episode: rejection of the immediately
preceding higher-timeframe auction extreme.  Stop, first objective and minimum
1R geometry are fixed before the single full-position entry.  No ATR threshold,
session score, fitted distance, trade cap, partial exit or stop movement is
introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_zones import ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


H4_COMPLETED_AUCTION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:FOUR_COMPLETED_SIXTY_MINUTE_BARS_DEFINE_ONE_"
    "CAUSAL_FOUR_HOUR_AUCTION_WHOSE_EXTREMES_ARE_REFERENCES_FOR_THE_NEXT_AUCTION"
)
H4_SWEEP_RECLAIM_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_FIRST_FIVE_MINUTE_SWEEP_AND_CLOSE_BACK_"
    "THROUGH_A_PREVIOUS_FOUR_HOUR_EXTREME_ARMS_ONE_REVERSAL_EPISODE"
)
H4_FIRST_RESPONSE_RULE = (
    "RESEARCH_HYPOTHESIS:THE_FIRST_LATER_COMPLETED_MINUTE_MUST_EXTEND_THE_"
    "RECLAIM_OR_SHOW_CAUSAL_ABSORPTION_AT_THE_RECLAIMED_FOUR_HOUR_BOUNDARY"
)
H4_FIRST_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_FOUR_HOUR_SWEEP_TARGET_IS_THE_NEAREST_"
    "PREEXISTING_UNSPENT_5M_15M_60M_OPPOSING_SWING_OR_PRIOR_AUCTION_MIDPOINT"
)
for _rule in (
    H4_COMPLETED_AUCTION_RULE,
    H4_SWEEP_RECLAIM_RULE,
    H4_FIRST_RESPONSE_RULE,
    H4_FIRST_OBJECTIVE_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class H4LiquidityKind(str, Enum):
    PREVIOUS_H4_HIGH = "PREVIOUS_H4_HIGH"
    PREVIOUS_H4_LOW = "PREVIOUS_H4_LOW"
    PREVIOUS_H4_MIDPOINT = "PREVIOUS_H4_MIDPOINT"


@dataclass(frozen=True, slots=True)
class CompletedH4Auction:
    close_time_ns: int
    high: float
    low: float
    bars: int

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0


@dataclass(slots=True)
class H4SweepSetup:
    setup_id: str
    reference_close_time_ns: int
    side: Side
    level_kind: H4LiquidityKind
    level_price: float
    level_zone: StructureZone
    sweep_time_ns: int
    sweep_extreme: float
    sweep_close: float
    target_zone: StructureZone
    target_price: float
    terminal_reason: str | None = None


class H4LiquiditySweepEngine:
    """Causal previous-four-hour sweep/reclaim state machine."""

    HOUR_NS = 60 * 60 * 1_000_000_000
    H4_NS = 4 * HOUR_NS

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.minimum_gross_rr = minimum_gross_rr
        self.higher_minutes = 240
        self.decision_minutes = 5
        self.trigger_minutes = 1

        self.sixty = NearestAnyPivotStructureBook(
            symbol,
            60,
            tick_size,
            pivot_spans=(2, 6),
        )
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

        self._building_bucket: int | None = None
        self._building_high: float | None = None
        self._building_low: float | None = None
        self._building_bars = 0
        self.previous_auction: CompletedH4Auction | None = None
        self._active: H4SweepSetup | None = None
        self._consumed: set[tuple[int, H4LiquidityKind]] = set()
        self._sequence = 0

        self.setups: list[H4SweepSetup] = []
        self.plans: list[V5TradePlan] = []
        self.audit_zones: list[StructureZone] = []
        self._zones: dict[str, StructureZone] = {}
        self._trace: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}

    @classmethod
    def _interval_bucket(cls, time_ns: int) -> int:
        return (time_ns - 1) // cls.H4_NS

    @classmethod
    def _is_h4_close(cls, time_ns: int) -> bool:
        return time_ns % cls.H4_NS == 0

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _record(self, kind: str, time_ns: int, **values: Any) -> None:
        self._trace.append(
            {
                "scenario_kind": kind,
                "event_time_ns": time_ns,
                "symbol": self.symbol,
                **values,
            },
        )

    def _audit(self, zone: StructureZone) -> None:
        if zone.zone_id not in self._zones:
            self._zones[zone.zone_id] = zone
            self.audit_zones.append(zone)

    def _level_zone(
        self,
        auction: CompletedH4Auction,
        *,
        kind: H4LiquidityKind,
        side: ZoneSide,
        price: float,
        time_ns: int,
    ) -> StructureZone:
        source = f"PREVIOUS_H4:{auction.close_time_ns}:{kind.value}"
        zone = StructureZone(
            zone_id=f"{source}:SNAP:{time_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=side,
            timeframe_minutes=240,
            lower=price - self.tick_size * 0.5,
            upper=price + self.tick_size * 0.5,
            invalidation=(
                price - self.tick_size
                if side is ZoneSide.SUPPORT
                else price + self.tick_size
            ),
            impulse_extreme=price,
            formed_index=0,
            formed_time_ns=auction.close_time_ns,
            observed_time_ns=auction.close_time_ns,
            formation_indices=(),
            strength_ratio=1.0,
            source_structure_id=source,
            source_pivot_span=1,
        )
        self._audit(zone)
        return zone

    def _midpoint_zone(
        self,
        auction: CompletedH4Auction,
        side: Side,
        time_ns: int,
    ) -> StructureZone:
        return self._level_zone(
            auction,
            kind=H4LiquidityKind.PREVIOUS_H4_MIDPOINT,
            side=(ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT),
            price=auction.midpoint,
            time_ns=time_ns,
        )

    def _finalize_auction(self, close_time_ns: int) -> None:
        if (
            self._building_high is None
            or self._building_low is None
            or self._building_bars != 4
        ):
            self._inc("incomplete_h4_auction_not_registered")
        else:
            self.previous_auction = CompletedH4Auction(
                close_time_ns=close_time_ns,
                high=self._building_high,
                low=self._building_low,
                bars=self._building_bars,
            )
            self._inc("completed_h4_auction_registered")
            self._record(
                "completed_h4_auction_registered",
                close_time_ns,
                auction_high=self.previous_auction.high,
                auction_low=self.previous_auction.low,
                auction_midpoint=self.previous_auction.midpoint,
                bars=self.previous_auction.bars,
                rule_provenance=H4_COMPLETED_AUCTION_RULE,
            )
        if self._active is not None:
            self._finish(
                self._active,
                "h4_sweep_expired_at_next_auction_close",
                close_time_ns,
            )
        self._building_bucket = None
        self._building_high = None
        self._building_low = None
        self._building_bars = 0

    def _on_sixty(self, bar: Candle) -> None:
        bucket = self._interval_bucket(bar.ts_close_ns)
        if self._building_bucket is None:
            self._building_bucket = bucket
        elif bucket != self._building_bucket:
            # A missing boundary close should not leak a partial range forward.
            self._finalize_auction(self._building_bucket * self.H4_NS + self.H4_NS)
            self._building_bucket = bucket

        self._building_high = (
            bar.high
            if self._building_high is None
            else max(self._building_high, bar.high)
        )
        self._building_low = (
            bar.low
            if self._building_low is None
            else min(self._building_low, bar.low)
        )
        self._building_bars += 1
        if self._is_h4_close(bar.ts_close_ns):
            self._finalize_auction(bar.ts_close_ns)

    def _target_candidates(
        self,
        side: Side,
        bar: Candle,
    ) -> list[tuple[str, StructureZone, float]]:
        candidates: list[tuple[str, StructureZone, float]] = []
        for source, book in (
            ("5M", self.five),
            ("15M", self.fifteen),
            ("60M", self.sixty),
        ):
            target = book.target_for(
                side,
                interaction_time_ns=bar.ts_close_ns,
                source_span=2,
                current_high=bar.high,
                current_low=bar.low,
            )
            if target is not None:
                candidates.append((source, target[0], target[1]))
        auction = self.previous_auction
        if auction is not None:
            midpoint = auction.midpoint
            # The target must remain wholly unspent at the completed decision
            # close, not merely beyond its close.
            ahead = midpoint > bar.high if side is Side.LONG else midpoint < bar.low
            if ahead:
                candidates.append(
                    (
                        "PREVIOUS_H4_MIDPOINT",
                        self._midpoint_zone(auction, side, bar.ts_close_ns),
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
        setup: H4SweepSetup,
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
            level_kind=setup.level_kind.value,
            level_price=setup.level_price,
            sweep_time_ns=setup.sweep_time_ns,
            sweep_extreme=setup.sweep_extreme,
            **values,
        )

    def _discover_sweep(self, bar: Candle) -> None:
        auction = self.previous_auction
        if (
            auction is None
            or self._active is not None
            or bar.ts_close_ns <= auction.close_time_ns
        ):
            return
        swept_low = (
            bar.low <= auction.low - self.tick_size
            and bar.close > auction.low
        )
        swept_high = (
            bar.high >= auction.high + self.tick_size
            and bar.close < auction.high
        )
        if swept_low and swept_high:
            self._inc("five_minute_bar_swept_both_h4_extremes")
            return
        if not swept_low and not swept_high:
            return

        if swept_low:
            side = Side.LONG
            kind = H4LiquidityKind.PREVIOUS_H4_LOW
            price = auction.low
            zone_side = ZoneSide.SUPPORT
            extreme = bar.low
        else:
            side = Side.SHORT
            kind = H4LiquidityKind.PREVIOUS_H4_HIGH
            price = auction.high
            zone_side = ZoneSide.RESISTANCE
            extreme = bar.high

        key = (auction.close_time_ns, kind)
        if key in self._consumed:
            self._inc("h4_level_already_consumed")
            return
        target = self._select_target(side, bar)
        if target is None:
            self._inc("h4_sweep_without_preexisting_objective")
            return
        target_zone, target_price, target_source = target
        valid = (
            extreme < bar.close < target_price
            if side is Side.LONG
            else target_price < bar.close < extreme
        )
        if not valid:
            self._inc("h4_sweep_invalid_initial_geometry")
            return

        self._sequence += 1
        level_zone = self._level_zone(
            auction,
            kind=kind,
            side=zone_side,
            price=price,
            time_ns=bar.ts_close_ns,
        )
        setup = H4SweepSetup(
            setup_id=(
                f"H4_SWEEP:{self.symbol}:{auction.close_time_ns}:"
                f"{kind.value}:{self._sequence}"
            ),
            reference_close_time_ns=auction.close_time_ns,
            side=side,
            level_kind=kind,
            level_price=price,
            level_zone=level_zone,
            sweep_time_ns=bar.ts_close_ns,
            sweep_extreme=extreme,
            sweep_close=bar.close,
            target_zone=target_zone,
            target_price=target_price,
        )
        self._consumed.add(key)
        self._active = setup
        self.setups.append(setup)
        self._inc("h4_sweep_reclaim_armed")
        self._record(
            "h4_sweep_reclaim_armed",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            side=side.name,
            level_kind=kind.value,
            level_price=price,
            sweep_extreme=extreme,
            sweep_close=bar.close,
            target_zone_id=target_zone.zone_id,
            target_price=target_price,
            target_source=target_source,
            rule_provenance=(
                H4_COMPLETED_AUCTION_RULE,
                H4_SWEEP_RECLAIM_RULE,
                H4_FIRST_OBJECTIVE_RULE,
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

    def _response_confirms(
        self,
        setup: H4SweepSetup,
        bar: Candle,
        observation: FlowObservation | None,
    ) -> tuple[bool, str]:
        intended_side = (
            bar.close > setup.level_price
            if setup.side is Side.LONG
            else bar.close < setup.level_price
        )
        if not intended_side:
            return False, "CLOSED_BACK_THROUGH_H4_LEVEL"
        extended = (
            bar.close > setup.sweep_close
            if setup.side is Side.LONG
            else bar.close < setup.sweep_close
        )
        if extended:
            return True, "FIRST_RESPONSE_EXTENDED_H4_RECLAIM"
        if observation is None or not observation.active or not observation.directed:
            return False, "NO_EXTENDED_RECLAIM_OR_CAUSAL_FLOW"
        touches = (
            bar.low <= setup.level_price
            if setup.side is Side.LONG
            else bar.high >= setup.level_price
        )
        if touches and self._adverse_flow(setup.side, observation):
            return True, "FIRST_RESPONSE_H4_ADVERSE_FLOW_ABSORBED"
        if observation.material_progress and self._aligned_flow(setup.side, observation):
            return True, "FIRST_RESPONSE_H4_ALIGNED_INITIATIVE"
        return False, "FIRST_RESPONSE_H4_FLOW_NOT_COHERENT"

    def _refresh_target(self, setup: H4SweepSetup, bar: Candle) -> None:
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
            self._inc("h4_target_refreshed_to_nearer_preentry_objective")

    def _advance_response(
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
            self._finish(setup, "h4_stop_touched_before_response_entry", bar.ts_close_ns)
            return []
        target_touched = (
            bar.high >= setup.target_price
            if setup.side is Side.LONG
            else bar.low <= setup.target_price
        )
        if target_touched:
            self._finish(setup, "h4_target_spent_before_response_entry", bar.ts_close_ns)
            return []

        confirmed, mechanism = self._response_confirms(setup, bar, observation)
        if not confirmed:
            self._finish(
                setup,
                "h4_first_response_failed",
                bar.ts_close_ns,
                response_open=bar.open,
                response_high=bar.high,
                response_low=bar.low,
                response_close=bar.close,
                mechanism=mechanism,
                rule_provenance=H4_FIRST_RESPONSE_RULE,
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
                "h4_nonpositive_preentry_geometry",
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
                "h4_below_minimum_gross_rr",
                bar.ts_close_ns,
                gross_rr=gross_rr,
            )
            return []

        self._sequence += 1
        plan = V5TradePlan(
            plan_id=f"h4-liquidity-{self.symbol}-{self._sequence:08d}",
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="PREVIOUS_H4_EXTREME_SWEEP_RECLAIM_FIRST_RESPONSE",
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
                H4_COMPLETED_AUCTION_RULE,
                H4_SWEEP_RECLAIM_RULE,
                H4_FIRST_RESPONSE_RULE,
                H4_FIRST_OBJECTIVE_RULE,
            ),
            scale_name="H4_LIQUIDITY",
            higher_timeframe_minutes=240,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        self.plans.append(plan)
        self._finish(
            setup,
            "h4_sweep_plan_created",
            bar.ts_close_ns,
            plan_id=plan.plan_id,
            mechanism=mechanism,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            rule_provenance=(
                H4_COMPLETED_AUCTION_RULE,
                H4_SWEEP_RECLAIM_RULE,
                H4_FIRST_RESPONSE_RULE,
                H4_FIRST_OBJECTIVE_RULE,
            ),
        )
        return [plan]

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 60:
            self.sixty.on_bar(bar)
            self._on_sixty(bar)
            self.sixty.observe_price(bar)
            return []
        if timeframe_minutes == 15:
            self.fifteen.on_bar(bar)
            self.fifteen.observe_price(bar)
            return []
        if timeframe_minutes == 5:
            self.five.on_bar(bar)
            self._discover_sweep(bar)
            self.five.observe_price(bar)
            return []
        if timeframe_minutes != 1:
            return []
        observation = self.flow.observe(bar)
        return self._advance_response(bar, observation)

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
            "previous_auction": (
                None
                if self.previous_auction is None
                else {
                    "close_time_ns": self.previous_auction.close_time_ns,
                    "high": self.previous_auction.high,
                    "low": self.previous_auction.low,
                    "midpoint": self.previous_auction.midpoint,
                }
            ),
            "flow": self.flow.diagnostics,
            "rules": (
                H4_COMPLETED_AUCTION_RULE,
                H4_SWEEP_RECLAIM_RULE,
                H4_FIRST_RESPONSE_RULE,
                H4_FIRST_OBJECTIVE_RULE,
            ),
        }
