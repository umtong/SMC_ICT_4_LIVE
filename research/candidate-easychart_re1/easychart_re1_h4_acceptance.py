"""Accepted previous-H4 extreme, first return and first response continuation.

The completed-H4 rejection family handles failed auctions.  This independent
family handles the opposite state: the previous four-hour extreme is accepted
as a new support/resistance boundary.  A five-minute body break must be followed
by the required next five-minute hold.  Entry is never the break or hold chase;
it is the first successful return and the first later completed one-minute
response.

The natural stop is beyond the completed retest extreme.  The immutable target
is the first pre-existing 5m/15m/60m obstacle or the first half-range extension,
whichever is nearer and still unspent.  No fitted distance, ATR threshold,
session filter, score, trade cap, partial exit or stop movement is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_h4_liquidity import (
    CompletedH4Auction,
    H4LiquidityKind,
    H4LiquiditySweepEngine,
)
from easychart_zones import ZoneSide


H4_ACCEPTANCE_BREAK_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_FIVE_MINUTE_BODY_BREAK_OF_A_COMPLETED_"
    "PREVIOUS_H4_EXTREME_REQUIRES_THE_IMMEDIATE_NEXT_FIVE_MINUTE_BAR_TO_OPEN_"
    "AND_CLOSE_ON_THE_ACCEPTED_SIDE"
)
H4_ACCEPTANCE_FIRST_RETURN_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:AN_ACCEPTED_PREVIOUS_H4_EXTREME_ENTERS_ONLY_"
    "AFTER_ITS_FIRST_LATER_RETURN_CLOSES_ON_THE_ACCEPTED_SIDE"
)
H4_ACCEPTANCE_RESPONSE_RULE = (
    "RESEARCH_HYPOTHESIS:THE_FIRST_COMPLETED_MINUTE_AFTER_THE_H4_RETURN_MUST_"
    "CLOSE_BEYOND_THE_RETURN_EXTREME_BEFORE_STOP_OR_TARGET_IS_TOUCHED"
)
H4_ACCEPTANCE_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_ACCEPTED_H4_TARGET_IS_THE_NEAREST_"
    "PREEXISTING_UNSPENT_5M_15M_60M_OPPOSING_SWING_OR_FIRST_HALF_RANGE_EXTENSION"
)
for _rule in (
    H4_ACCEPTANCE_BREAK_RULE,
    H4_ACCEPTANCE_FIRST_RETURN_RULE,
    H4_ACCEPTANCE_RESPONSE_RULE,
    H4_ACCEPTANCE_OBJECTIVE_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class H4AcceptanceKind(str, Enum):
    FIRST_HALF_RANGE_EXTENSION = "H4_FIRST_HALF_RANGE_EXTENSION"


@dataclass(slots=True)
class H4AcceptanceSetup:
    setup_id: str
    reference_close_time_ns: int
    side: Side
    level_kind: H4LiquidityKind
    level_price: float
    level_zone: StructureZone
    break_time_ns: int
    break_index: int
    break_close: float
    target_zone: StructureZone
    target_price: float
    state: str = "WAITING_HOLD"
    hold_time_ns: int | None = None
    retest_time_ns: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    terminal_reason: str | None = None


class H4AcceptanceEngine:
    """One-symbol state machine for accepted previous-H4 boundaries."""

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

        # Reuse the completed-auction and causal pivot implementation without
        # invoking its rejection entry path.  Both opportunity families observe
        # identical bars but own different auction outcomes.
        self.book = H4LiquiditySweepEngine(symbol, tick_size, minimum_gross_rr)
        self.audit_zones = self.book.audit_zones
        self.setups: list[H4AcceptanceSetup] = []
        self.plans: list[V5TradePlan] = []
        self._active: H4AcceptanceSetup | None = None
        self._consumed: set[tuple[int, H4LiquidityKind]] = set()
        self._five_index = -1
        self._previous_five: Candle | None = None
        self._sequence = 0
        self._trace: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}

    @property
    def previous_auction(self) -> CompletedH4Auction | None:
        return self.book.previous_auction

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

    def _extension_zone(
        self,
        auction: CompletedH4Auction,
        side: Side,
        time_ns: int,
    ) -> tuple[StructureZone, float]:
        width = auction.high - auction.low
        if width <= self.tick_size:
            raise RuntimeError("completed H4 auction has nonpositive width")
        if side is Side.LONG:
            price = auction.high + 0.5 * width
            zone_side = ZoneSide.RESISTANCE
        else:
            price = auction.low - 0.5 * width
            zone_side = ZoneSide.SUPPORT
        source = (
            f"PREVIOUS_H4:{auction.close_time_ns}:"
            f"{H4AcceptanceKind.FIRST_HALF_RANGE_EXTENSION.value}:{side.name}"
        )
        zone = StructureZone(
            zone_id=f"{source}:SNAP:{time_ns}",
            kind=H4AcceptanceKind.FIRST_HALF_RANGE_EXTENSION,
            family=StructureFamily.HORIZONTAL,
            side=zone_side,
            timeframe_minutes=240,
            lower=price - self.tick_size * 0.5,
            upper=price + self.tick_size * 0.5,
            invalidation=(
                price + self.tick_size
                if side is Side.LONG
                else price - self.tick_size
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
        self.book._audit(zone)
        return zone, price

    def _target_candidates(
        self,
        side: Side,
        bar: Candle,
    ) -> list[tuple[str, StructureZone, float]]:
        candidates: list[tuple[str, StructureZone, float]] = []
        for source, structure in (
            ("5M", self.book.five),
            ("15M", self.book.fifteen),
            ("60M", self.book.sixty),
        ):
            target = structure.target_for(
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
            zone, price = self._extension_zone(auction, side, bar.ts_close_ns)
            ahead = price > bar.high if side is Side.LONG else price < bar.low
            if ahead:
                candidates.append(("H4_HALF_RANGE_EXTENSION", zone, price))
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
        self.book._audit(selected[1])
        return selected[1], selected[2], selected[0]

    def _finish(
        self,
        setup: H4AcceptanceSetup,
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
            break_time_ns=setup.break_time_ns,
            state=setup.state,
            **values,
        )

    @staticmethod
    def _target_touched(setup: H4AcceptanceSetup, bar: Candle) -> bool:
        return (
            bar.high >= setup.target_price
            if setup.side is Side.LONG
            else bar.low <= setup.target_price
        )

    def _discover_break(self, bar: Candle, index: int) -> None:
        auction = self.previous_auction
        previous = self._previous_five
        if (
            auction is None
            or previous is None
            or self._active is not None
            or bar.ts_close_ns <= auction.close_time_ns
        ):
            return
        long_break = (
            previous.close <= auction.high
            and bar.open <= auction.high + self.tick_size * 0.5
            and bar.close > auction.high + self.tick_size * 0.5
            and bar.close > bar.open
        )
        short_break = (
            previous.close >= auction.low
            and bar.open >= auction.low - self.tick_size * 0.5
            and bar.close < auction.low - self.tick_size * 0.5
            and bar.close < bar.open
        )
        if long_break and short_break:
            self._inc("h4_acceptance_bar_broke_both_extremes")
            return
        if not long_break and not short_break:
            return
        if long_break:
            side = Side.LONG
            kind = H4LiquidityKind.PREVIOUS_H4_HIGH
            price = auction.high
            zone_side = ZoneSide.SUPPORT
        else:
            side = Side.SHORT
            kind = H4LiquidityKind.PREVIOUS_H4_LOW
            price = auction.low
            zone_side = ZoneSide.RESISTANCE
        key = (auction.close_time_ns, kind)
        if key in self._consumed:
            self._inc("h4_acceptance_level_already_consumed")
            return
        target = self._select_target(side, bar)
        if target is None:
            self._inc("h4_acceptance_break_without_preexisting_objective")
            return
        target_zone, target_price, target_source = target
        valid = (
            price < bar.close < target_price
            if side is Side.LONG
            else target_price < bar.close < price
        )
        if not valid:
            self._inc("h4_acceptance_invalid_break_geometry")
            return
        level_zone = self.book._level_zone(
            auction,
            kind=kind,
            side=zone_side,
            price=price,
            time_ns=bar.ts_close_ns,
        )
        self._sequence += 1
        setup = H4AcceptanceSetup(
            setup_id=(
                f"H4_ACCEPTANCE:{self.symbol}:{auction.close_time_ns}:"
                f"{kind.value}:{self._sequence}"
            ),
            reference_close_time_ns=auction.close_time_ns,
            side=side,
            level_kind=kind,
            level_price=price,
            level_zone=level_zone,
            break_time_ns=bar.ts_close_ns,
            break_index=index,
            break_close=bar.close,
            target_zone=target_zone,
            target_price=target_price,
        )
        self._consumed.add(key)
        self._active = setup
        self.setups.append(setup)
        self._inc("h4_acceptance_break_armed")
        self._record(
            "h4_acceptance_break_armed",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            side=side.name,
            level_kind=kind.value,
            level_price=price,
            break_open=bar.open,
            break_high=bar.high,
            break_low=bar.low,
            break_close=bar.close,
            target_zone_id=target_zone.zone_id,
            target_price=target_price,
            target_source=target_source,
            rule_provenance=(
                H4_ACCEPTANCE_BREAK_RULE,
                H4_ACCEPTANCE_OBJECTIVE_RULE,
            ),
        )

    def _advance_five(self, bar: Candle, index: int) -> None:
        setup = self._active
        if setup is None:
            self._discover_break(bar, index)
            return
        if setup.state == "WAITING_HOLD":
            expected = setup.break_index + 1
            if index < expected:
                return
            if index > expected:
                self._finish(
                    setup,
                    "h4_acceptance_missing_immediate_hold_bar",
                    bar.ts_close_ns,
                )
                return
            if self._target_touched(setup, bar):
                self._finish(
                    setup,
                    "h4_acceptance_target_spent_on_hold_before_entry",
                    bar.ts_close_ns,
                )
                return
            held = (
                bar.open > setup.level_price
                and bar.close > setup.level_price
                if setup.side is Side.LONG
                else bar.open < setup.level_price
                and bar.close < setup.level_price
            )
            if not held:
                self._finish(
                    setup,
                    "h4_acceptance_required_hold_failed",
                    bar.ts_close_ns,
                    hold_open=bar.open,
                    hold_high=bar.high,
                    hold_low=bar.low,
                    hold_close=bar.close,
                    rule_provenance=H4_ACCEPTANCE_BREAK_RULE,
                )
                return
            setup.hold_time_ns = bar.ts_close_ns
            embedded = (
                bar.low <= setup.level_zone.upper
                if setup.side is Side.LONG
                else bar.high >= setup.level_zone.lower
            )
            if embedded:
                setup.retest_time_ns = bar.ts_close_ns
                setup.retest_high = bar.high
                setup.retest_low = bar.low
                setup.state = "WAITING_RESPONSE"
                self._inc("h4_acceptance_hold_embedded_first_return")
                self._record(
                    "h4_acceptance_hold_embedded_first_return",
                    bar.ts_close_ns,
                    setup_id=setup.setup_id,
                    side=setup.side.name,
                    retest_high=bar.high,
                    retest_low=bar.low,
                    retest_close=bar.close,
                    rule_provenance=H4_ACCEPTANCE_FIRST_RETURN_RULE,
                )
            else:
                setup.state = "WAITING_RETEST"
                self._inc("h4_acceptance_hold_confirmed_waiting_return")
                self._record(
                    "h4_acceptance_hold_confirmed_waiting_return",
                    bar.ts_close_ns,
                    setup_id=setup.setup_id,
                    side=setup.side.name,
                    hold_open=bar.open,
                    hold_close=bar.close,
                    rule_provenance=H4_ACCEPTANCE_BREAK_RULE,
                )
            return

        # A completed decision bar closing back through the accepted boundary
        # invalidates the continuation before any micro entry can be submitted.
        closed_inside = (
            bar.close < setup.level_price
            if setup.side is Side.LONG
            else bar.close > setup.level_price
        )
        if closed_inside:
            self._finish(
                setup,
                "h4_acceptance_closed_back_inside_before_entry",
                bar.ts_close_ns,
            )

    def _refresh_target(self, setup: H4AcceptanceSetup, bar: Candle) -> None:
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
            self._inc("h4_acceptance_target_refreshed_to_nearer_objective")

    def _advance_one(self, bar: Candle) -> list[V5TradePlan]:
        setup = self._active
        if setup is None or setup.hold_time_ns is None or bar.ts_close_ns <= setup.hold_time_ns:
            return []
        if self._target_touched(setup, bar):
            self._finish(
                setup,
                "h4_acceptance_target_spent_before_micro_entry",
                bar.ts_close_ns,
            )
            return []

        if setup.state == "WAITING_RETEST":
            touched = (
                bar.low <= setup.level_zone.upper
                if setup.side is Side.LONG
                else bar.high >= setup.level_zone.lower
            )
            if not touched:
                return []
            held = (
                bar.close > setup.level_price
                if setup.side is Side.LONG
                else bar.close < setup.level_price
            )
            if not held:
                self._finish(
                    setup,
                    "h4_acceptance_first_return_failed",
                    bar.ts_close_ns,
                    retest_open=bar.open,
                    retest_high=bar.high,
                    retest_low=bar.low,
                    retest_close=bar.close,
                    rule_provenance=H4_ACCEPTANCE_FIRST_RETURN_RULE,
                )
                return []
            setup.retest_time_ns = bar.ts_close_ns
            setup.retest_high = bar.high
            setup.retest_low = bar.low
            setup.state = "WAITING_RESPONSE"
            self._inc("h4_acceptance_first_return_confirmed")
            self._record(
                "h4_acceptance_first_return_confirmed",
                bar.ts_close_ns,
                setup_id=setup.setup_id,
                side=setup.side.name,
                retest_high=bar.high,
                retest_low=bar.low,
                retest_close=bar.close,
                rule_provenance=H4_ACCEPTANCE_FIRST_RETURN_RULE,
            )
            return []

        if setup.state != "WAITING_RESPONSE":
            return []
        if setup.retest_time_ns is None or bar.ts_close_ns <= setup.retest_time_ns:
            return []
        if setup.retest_low is None or setup.retest_high is None:
            raise RuntimeError("H4 acceptance response lost retest geometry")
        stop = (
            min(setup.retest_low, setup.level_price) - self.tick_size
            if setup.side is Side.LONG
            else max(setup.retest_high, setup.level_price) + self.tick_size
        )
        stop_touched = bar.low <= stop if setup.side is Side.LONG else bar.high >= stop
        if stop_touched:
            self._finish(
                setup,
                "h4_acceptance_stop_touched_before_response_entry",
                bar.ts_close_ns,
                stop=stop,
            )
            return []
        confirmed = (
            bar.close > setup.retest_high
            if setup.side is Side.LONG
            else bar.close < setup.retest_low
        )
        if not confirmed:
            self._finish(
                setup,
                "h4_acceptance_first_response_failed",
                bar.ts_close_ns,
                response_open=bar.open,
                response_high=bar.high,
                response_low=bar.low,
                response_close=bar.close,
                rule_provenance=H4_ACCEPTANCE_RESPONSE_RULE,
            )
            return []

        self._refresh_target(setup, bar)
        entry = bar.close
        target = setup.target_price
        reward = target - entry if setup.side is Side.LONG else entry - target
        risk = entry - stop if setup.side is Side.LONG else stop - entry
        if reward <= 0.0 or risk <= 0.0:
            self._finish(
                setup,
                "h4_acceptance_nonpositive_preentry_geometry",
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
                "h4_acceptance_below_minimum_gross_rr",
                bar.ts_close_ns,
                gross_rr=gross_rr,
            )
            return []

        self._sequence += 1
        plan = V5TradePlan(
            plan_id=f"h4-acceptance-{self.symbol}-{self._sequence:08d}",
            causal_event_id=setup.setup_id,
            symbol=self.symbol,
            family="PREVIOUS_H4_EXTREME_ACCEPTANCE_FIRST_RETURN_RESPONSE",
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
            trigger_strength_ratio=1.0,
            target_zone_id=setup.target_zone.zone_id,
            target_zone_kind=setup.target_zone.kind,
            overlap_lower=setup.level_zone.lower,
            overlap_upper=setup.level_zone.upper,
            interaction_time_ns=setup.break_time_ns,
            trigger_time_ns=bar.ts_close_ns,
            scenario_path=ScenarioPath.ACCEPTANCE.value,
            setup_observed_time_ns=setup.level_zone.observed_time_ns,
            trigger_zone_kind="H4_ACCEPTANCE_FIRST_RESPONSE",
            source_rule_count=4,
            rule_provenance=(
                H4_ACCEPTANCE_BREAK_RULE,
                H4_ACCEPTANCE_FIRST_RETURN_RULE,
                H4_ACCEPTANCE_RESPONSE_RULE,
                H4_ACCEPTANCE_OBJECTIVE_RULE,
            ),
            scale_name="H4_ACCEPTANCE",
            higher_timeframe_minutes=240,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        self.plans.append(plan)
        self._finish(
            setup,
            "h4_acceptance_plan_created",
            bar.ts_close_ns,
            plan_id=plan.plan_id,
            entry=entry,
            stop=stop,
            target=target,
            gross_rr=gross_rr,
            rule_provenance=(
                H4_ACCEPTANCE_BREAK_RULE,
                H4_ACCEPTANCE_FIRST_RETURN_RULE,
                H4_ACCEPTANCE_RESPONSE_RULE,
                H4_ACCEPTANCE_OBJECTIVE_RULE,
            ),
        )
        return [plan]

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 60:
            previous_close = (
                None
                if self.previous_auction is None
                else self.previous_auction.close_time_ns
            )
            self.book.sixty.on_bar(bar)
            self.book._on_sixty(bar)
            self.book.sixty.observe_price(bar)
            current_close = (
                None
                if self.previous_auction is None
                else self.previous_auction.close_time_ns
            )
            if (
                self._active is not None
                and current_close is not None
                and current_close != previous_close
            ):
                self._finish(
                    self._active,
                    "h4_acceptance_expired_at_next_auction_close",
                    bar.ts_close_ns,
                )
            return []
        if timeframe_minutes == 15:
            self.book.fifteen.on_bar(bar)
            self.book.fifteen.observe_price(bar)
            return []
        if timeframe_minutes == 5:
            self.book.five.on_bar(bar)
            self._five_index += 1
            self._advance_five(bar, self._five_index)
            self.book.five.observe_price(bar)
            self._previous_five = bar
            return []
        if timeframe_minutes != 1:
            return []
        self.book.flow.observe(bar)
        return self._advance_one(bar)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.book.drain_trace() + self._trace
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> StructureZone | None:
        return self.book.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._counts.items())),
            "active_setup": None if self._active is None else self._active.setup_id,
            "auction_book": self.book.diagnostics,
            "rules": (
                H4_ACCEPTANCE_BREAK_RULE,
                H4_ACCEPTANCE_FIRST_RETURN_RULE,
                H4_ACCEPTANCE_RESPONSE_RULE,
                H4_ACCEPTANCE_OBJECTIVE_RULE,
            ),
        }
