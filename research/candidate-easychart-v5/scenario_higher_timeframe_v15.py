"""Assign every micro setup a causal role inside the 60m and 4h auctions.

The source gives 12h/4h/1h charts the intermediate-trend and support/resistance
role, while 15m/5m/1m provide the actual entry.  This policy therefore asks two
separate questions before a lower-timeframe plan reaches the account:

1. Does the completed 60m auction support this continuation or reversal?
2. If the completed 4h auction has a clear direction, is the plan aligned with
   it, or is a countertrend reversal occurring at an actual fresh 4h boundary?

A micro acceptance against an intact larger auction is a pullback, not a proven
trend change.  A rejection, rotation or bounce may reverse that auction only at
a pre-existing larger support/resistance, trend line or channel edge.  Mixed or
unresolved 4h structure does not veto a plan; the resolved 60m role remains the
nearest directional authority.

Only completed bars, confirmed wick pivots and exact price-band overlap are
used.  No indicator score, return threshold, volatility multiple, time exit,
risk multiplier or post-entry management rule is added.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import V5TradePlan
from domain import Candle, Side
from easychart_zones import ZoneSide
from scenario_close_detached_v14 import MicroCloseDetachedRetestBundleV14


HOUR_NS = 3_600_000_000_000

HIGHER_TIMEFRAME_ACCEPTANCE_RULE = (
    "SOURCE_EXPLICIT:60M_INTERMEDIATE_STRUCTURE_GIVES_DIRECTION_AND_15M_5M_1M_"
    "SUPPLY_ENTRY;ACCEPTANCE_RETEST_REQUIRES_ALIGNED_OR_DIRECTIONALLY_"
    "TRANSITIONING_60M_STRUCTURE"
)
HIGHER_TIMEFRAME_REVERSAL_RULE = (
    "SOURCE_EXPLICIT:COUNTERTREND_FAKEOUT_ROTATION_OR_BOUNCE_REQUIRES_"
    "ACTUAL_OVERLAP_WITH_PREEXISTING_60M_STRUCTURE"
)
FOUR_HOUR_ROLE_RULE = (
    "SOURCE_EXPLICIT:4H_IS_AN_INTERMEDIATE_TREND_AND_SUPPORT_RESISTANCE_"
    "TIMEFRAME;A_PLAN_AGAINST_RESOLVED_4H_DIRECTION_REQUIRES_AN_ACTUAL_"
    "PREEXISTING_4H_BOUNDARY"
)
HIGHER_TIMEFRAME_STATE_TRANSLATION = (
    "RESEARCH_HYPOTHESIS:CONFIRMED_WICK_PIVOT_SEQUENCE_DEFINES_AN_INTACT_"
    "AUCTION_AND_A_COMPLETED_CLOSE_THROUGH_ITS_LATEST_SWING_DEFINES_A_"
    "DIRECTIONAL_TRANSITION"
)
FOUR_HOUR_AGGREGATION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:4H_CONTEXT_USES_FOUR_COMPLETED_CONSECUTIVE_"
    "UTC_ALIGNED_60M_BARS"
)


class DirectionState(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    TRANSITION_UP = "TRANSITION_UP"
    TRANSITION_DOWN = "TRANSITION_DOWN"
    TRANSITION = "TRANSITION"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class DirectionPivot:
    side: str
    price: float
    event_time_ns: int
    observed_time_ns: int


class CausalHigherTimeframeDirection:
    """Online structure plus a current completed-close structural break."""

    PIVOT_SPAN = 2

    def __init__(self) -> None:
        self.bars: list[Candle] = []
        self.pivots: list[DirectionPivot] = []
        self._pivot_keys: set[tuple[str, int]] = set()

    def on_bar(self, bar: Candle) -> None:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("higher-timeframe bars must be chronological")
        self.bars.append(bar)
        observed_index = len(self.bars) - 1
        span = self.PIVOT_SPAN
        center = observed_index - span
        if center < span:
            return
        window = self.bars[center - span : center + span + 1]
        if len(window) != 2 * span + 1:
            return
        pivot = self.bars[center]
        highs = [item.high for item in window]
        lows = [item.low for item in window]
        if pivot.high == max(highs) and highs.count(pivot.high) == 1:
            self._add("HIGH", pivot.high, center, observed_index)
        if pivot.low == min(lows) and lows.count(pivot.low) == 1:
            self._add("LOW", pivot.low, center, observed_index)

    def _add(self, side: str, price: float, center: int, observed_index: int) -> None:
        key = (side, center)
        if key in self._pivot_keys:
            return
        self._pivot_keys.add(key)
        self.pivots.append(
            DirectionPivot(
                side=side,
                price=price,
                event_time_ns=self.bars[center].ts_close_ns,
                observed_time_ns=self.bars[observed_index].ts_close_ns,
            ),
        )

    def _base_state(
        self,
        highs: list[DirectionPivot],
        lows: list[DirectionPivot],
    ) -> DirectionState:
        if len(highs) < 2 or len(lows) < 2:
            return DirectionState.UNRESOLVED
        previous_high, last_high = highs[-2:]
        previous_low, last_low = lows[-2:]
        if last_high.price > previous_high.price and last_low.price > previous_low.price:
            return DirectionState.UP
        if last_high.price < previous_high.price and last_low.price < previous_low.price:
            return DirectionState.DOWN
        return DirectionState.TRANSITION

    def state(self) -> DirectionState:
        if not self.bars:
            return DirectionState.UNRESOLVED
        highs = [pivot for pivot in self.pivots if pivot.side == "HIGH"]
        lows = [pivot for pivot in self.pivots if pivot.side == "LOW"]
        base = self._base_state(highs, lows)
        close = self.bars[-1].close
        if lows and close < lows[-1].price and base is not DirectionState.DOWN:
            return DirectionState.TRANSITION_DOWN
        if highs and close > highs[-1].price and base is not DirectionState.UP:
            return DirectionState.TRANSITION_UP
        return base

    @property
    def observed_time_ns(self) -> int | None:
        return None if not self.bars else self.bars[-1].ts_close_ns

    @property
    def latest_high(self) -> float | None:
        highs = [pivot.price for pivot in self.pivots if pivot.side == "HIGH"]
        return None if not highs else highs[-1]

    @property
    def latest_low(self) -> float | None:
        lows = [pivot.price for pivot in self.pivots if pivot.side == "LOW"]
        return None if not lows else lows[-1]


class CompletedFourHourBars:
    """Causally aggregate completed 60m candles into UTC-aligned 4h candles."""

    def __init__(self) -> None:
        self.hourly: list[Candle] = []

    def on_hour(self, bar: Candle) -> Candle | None:
        if self.hourly and bar.ts_close_ns != self.hourly[-1].ts_close_ns + HOUR_NS:
            self.hourly.clear()
        self.hourly.append(bar)
        if len(self.hourly) > 4:
            self.hourly = self.hourly[-4:]
        close_hour = (bar.ts_close_ns // HOUR_NS) % 24
        if close_hour % 4 != 0 or len(self.hourly) != 4:
            return None
        if any(
            self.hourly[index].ts_close_ns + HOUR_NS != self.hourly[index + 1].ts_close_ns
            for index in range(3)
        ):
            return None
        return Candle(
            ts_close_ns=bar.ts_close_ns,
            open=self.hourly[0].open,
            high=max(item.high for item in self.hourly),
            low=min(item.low for item in self.hourly),
            close=self.hourly[-1].close,
            volume=sum(item.volume for item in self.hourly),
        )


class HigherTimeframeAcceptanceBundleV15(MicroCloseDetachedRetestBundleV14):
    """One micro decision policy whose paths receive 60m and 4h roles."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)

        self.higher_direction = CausalHigherTimeframeDirection()
        self.higher_structure = LifecycleAwareStructureBook(symbol, 60, tick_size)
        self._higher_snapshot_time_ns: int | None = None
        self._higher_boundaries_before_retirement: tuple[Any, ...] = ()

        self.four_hour_aggregator = CompletedFourHourBars()
        self.four_hour_direction = CausalHigherTimeframeDirection()
        self.four_hour_structure = LifecycleAwareStructureBook(symbol, 240, tick_size)
        self._four_hour_snapshot_time_ns: int | None = None
        self._four_hour_boundaries_before_retirement: tuple[Any, ...] = ()

    @staticmethod
    def _directionally_aligned(plan: V5TradePlan, state: DirectionState) -> bool:
        if state in {DirectionState.UP, DirectionState.TRANSITION_UP}:
            return plan.side is Side.LONG
        if state in {DirectionState.DOWN, DirectionState.TRANSITION_DOWN}:
            return plan.side is Side.SHORT
        return False

    @staticmethod
    def _directional(state: DirectionState) -> bool:
        return state in {
            DirectionState.UP,
            DirectionState.DOWN,
            DirectionState.TRANSITION_UP,
            DirectionState.TRANSITION_DOWN,
        }

    def _boundaries_at(
        self,
        book: LifecycleAwareStructureBook,
        snapshot_time_ns: int | None,
        snapshot: tuple[Any, ...],
        time_ns: int,
    ) -> tuple[Any, ...]:
        if snapshot_time_ns == time_ns:
            return snapshot
        return tuple(book.boundaries_at(time_ns))

    def _spatial_matches(
        self,
        plan: V5TradePlan,
        *,
        book: LifecycleAwareStructureBook,
        snapshot_time_ns: int | None,
        snapshot: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        wanted = ZoneSide.SUPPORT if plan.side is Side.LONG else ZoneSide.RESISTANCE
        lower = plan.overlap_lower - self.tick_size
        upper = plan.overlap_upper + self.tick_size
        return tuple(
            zone
            for zone in self._boundaries_at(
                book,
                snapshot_time_ns,
                snapshot,
                plan.observed_time_ns,
            )
            if zone.side is wanted
            and zone.observed_time_ns <= plan.observed_time_ns
            and zone.lower <= upper
            and zone.upper >= lower
        )

    def _role_allows(
        self,
        plan: V5TradePlan,
        state: DirectionState,
        spatial_matches: tuple[Any, ...],
        *,
        neutral_allows: bool,
    ) -> bool:
        if not self._directional(state):
            return neutral_allows
        aligned = self._directionally_aligned(plan, state)
        if plan.scenario_path == "ACCEPTANCE":
            return aligned
        return aligned or bool(spatial_matches)

    def _update_sixty_minute_context(self, bar: Candle) -> None:
        self.higher_direction.on_bar(bar)
        self.higher_structure.on_bar(bar)
        self._higher_snapshot_time_ns = bar.ts_close_ns
        self._higher_boundaries_before_retirement = tuple(
            self.higher_structure.boundaries_at(bar.ts_close_ns),
        )
        self.higher_structure.observe_price(bar)

    def _update_four_hour_context(self, hour: Candle) -> None:
        four_hour = self.four_hour_aggregator.on_hour(hour)
        if four_hour is None:
            return
        self.four_hour_direction.on_bar(four_hour)
        self.four_hour_structure.on_bar(four_hour)
        self._four_hour_snapshot_time_ns = four_hour.ts_close_ns
        self._four_hour_boundaries_before_retirement = tuple(
            self.four_hour_structure.boundaries_at(four_hour.ts_close_ns),
        )
        self.four_hour_structure.observe_price(four_hour)

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 60:
            self._update_sixty_minute_context(bar)
            self._update_four_hour_context(bar)

        plans = super().on_bar(timeframe_minutes, bar)
        if not plans:
            return []

        state_60m = self.higher_direction.state()
        time_60m = self.higher_direction.observed_time_ns
        state_4h = self.four_hour_direction.state()
        time_4h = self.four_hour_direction.observed_time_ns
        output: list[V5TradePlan] = []

        for plan in plans:
            if time_60m is not None and time_60m > plan.observed_time_ns:
                raise RuntimeError("60m router used future information")
            if time_4h is not None and time_4h > plan.observed_time_ns:
                raise RuntimeError("4h router used future information")

            spatial_60m = self._spatial_matches(
                plan,
                book=self.higher_structure,
                snapshot_time_ns=self._higher_snapshot_time_ns,
                snapshot=self._higher_boundaries_before_retirement,
            )
            spatial_4h = self._spatial_matches(
                plan,
                book=self.four_hour_structure,
                snapshot_time_ns=self._four_hour_snapshot_time_ns,
                snapshot=self._four_hour_boundaries_before_retirement,
            )
            allowed_60m = self._role_allows(
                plan,
                state_60m,
                spatial_60m,
                neutral_allows=False,
            )
            allowed_4h = self._role_allows(
                plan,
                state_4h,
                spatial_4h,
                neutral_allows=True,
            )
            allowed = allowed_60m and allowed_4h

            context_values = {
                "higher_timeframe_state": state_60m.value,
                "higher_timeframe_observed_ns": time_60m,
                "higher_timeframe_latest_high": self.higher_direction.latest_high,
                "higher_timeframe_latest_low": self.higher_direction.latest_low,
                "higher_structure_match_ids": [zone.source_structure_id for zone in spatial_60m],
                "higher_structure_match_kinds": [zone.kind.value for zone in spatial_60m],
                "higher_structure_match_count": len(spatial_60m),
                "directionally_aligned": self._directionally_aligned(plan, state_60m),
                "four_hour_state": state_4h.value,
                "four_hour_observed_ns": time_4h,
                "four_hour_latest_high": self.four_hour_direction.latest_high,
                "four_hour_latest_low": self.four_hour_direction.latest_low,
                "four_hour_structure_match_ids": [zone.source_structure_id for zone in spatial_4h],
                "four_hour_structure_match_kinds": [zone.kind.value for zone in spatial_4h],
                "four_hour_structure_match_count": len(spatial_4h),
                "four_hour_directionally_aligned": self._directionally_aligned(plan, state_4h),
                "allowed_by_60m_role": allowed_60m,
                "allowed_by_4h_role": allowed_4h,
                "acceptance_rule": HIGHER_TIMEFRAME_ACCEPTANCE_RULE,
                "reversal_rule": HIGHER_TIMEFRAME_REVERSAL_RULE,
                "four_hour_role_rule": FOUR_HOUR_ROLE_RULE,
                "state_translation": HIGHER_TIMEFRAME_STATE_TRANSLATION,
                "four_hour_aggregation": FOUR_HOUR_AGGREGATION_RULE,
            }

            if allowed:
                output.append(plan)
                self._bundle_trace.append(
                    {
                        "scenario_kind": "higher_timeframe_role_accepted",
                        "event_time_ns": plan.observed_time_ns,
                        "plan_id": plan.plan_id,
                        "scenario_path": plan.scenario_path,
                        "side": plan.side.name,
                        **context_values,
                    },
                )
            else:
                self._bundle_trace.append(
                    {
                        "scenario_kind": "plan_rejected_by_higher_timeframe_role",
                        "event_time_ns": plan.observed_time_ns,
                        "plan_id": plan.plan_id,
                        "scenario_path": plan.scenario_path,
                        "side": plan.side.name,
                        **context_values,
                    },
                )
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["higher_timeframe_role"] = {
            "60m": {
                "pivot_span": self.higher_direction.PIVOT_SPAN,
                "state": self.higher_direction.state().value,
                "latest_high": self.higher_direction.latest_high,
                "latest_low": self.higher_direction.latest_low,
                "active_structure_diagnostics": dict(self.higher_structure.diagnostics),
            },
            "4h": {
                "pivot_span": self.four_hour_direction.PIVOT_SPAN,
                "state": self.four_hour_direction.state().value,
                "latest_high": self.four_hour_direction.latest_high,
                "latest_low": self.four_hour_direction.latest_low,
                "completed_bars": len(self.four_hour_direction.bars),
                "active_structure_diagnostics": dict(self.four_hour_structure.diagnostics),
            },
            "acceptance_rule": HIGHER_TIMEFRAME_ACCEPTANCE_RULE,
            "reversal_rule": HIGHER_TIMEFRAME_REVERSAL_RULE,
            "four_hour_role_rule": FOUR_HOUR_ROLE_RULE,
            "state_translation": HIGHER_TIMEFRAME_STATE_TRANSLATION,
            "four_hour_aggregation": FOUR_HOUR_AGGREGATION_RULE,
        }
        return output
