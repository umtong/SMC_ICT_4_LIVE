"""Assign every micro setup a causal role inside the completed 60m auction.

EasyChart uses larger timeframes for direction and support/resistance, then
15m/5m/1m for the actual setup and entry.  A micro acceptance is continuation
only when the completed 60m structure is aligned or has already broken in that
direction.  A rejection, rotation or bounce may trade against the current 60m
direction only when the lower-timeframe interaction actually overlaps a fresh
60m support/resistance, trend line or channel edge.  Otherwise it is merely a
small countertrend reaction inside an intact larger auction.

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


HIGHER_TIMEFRAME_ACCEPTANCE_RULE = (
    "SOURCE_EXPLICIT:LARGER_TIMEFRAME_STRUCTURE_GIVES_DIRECTION_AND_SMALLER_"
    "TIMEFRAME_SUPPLIES_ENTRY;ACCEPTANCE_RETEST_REQUIRES_ALIGNED_OR_"
    "DIRECTIONALLY_TRANSITIONING_60M_STRUCTURE;DIRECTIONLESS_TRANSITION_"
    "REMAINS_UNRESOLVED"
)
HIGHER_TIMEFRAME_REVERSAL_RULE = (
    "SOURCE_EXPLICIT:COUNTERTREND_FAKEOUT_ROTATION_OR_BOUNCE_REQUIRES_"
    "ACTUAL_OVERLAP_WITH_PREEXISTING_60M_STRUCTURE"
)
HIGHER_TIMEFRAME_STATE_TRANSLATION = (
    "RESEARCH_HYPOTHESIS:CONFIRMED_60M_WICK_PIVOT_SEQUENCE_DEFINES_THE_"
    "INTACT_AUCTION_AND_A_COMPLETED_CLOSE_THROUGH_ITS_LATEST_SWING_DEFINES_"
    "DIRECTIONAL_TRANSITION"
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
    """Online 60m structure plus current completed-close structural break."""

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


class HigherTimeframeAcceptanceBundleV15(MicroCloseDetachedRetestBundleV14):
    """One micro decision policy whose paths receive distinct 60m roles."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.higher_direction = CausalHigherTimeframeDirection()
        self.higher_structure = LifecycleAwareStructureBook(symbol, 60, tick_size)
        self._higher_snapshot_time_ns: int | None = None
        self._higher_boundaries_before_retirement: tuple[Any, ...] = ()

    @staticmethod
    def _directionally_aligned(plan: V5TradePlan, state: DirectionState) -> bool:
        if state in {DirectionState.UP, DirectionState.TRANSITION_UP}:
            return plan.side is Side.LONG
        if state in {DirectionState.DOWN, DirectionState.TRANSITION_DOWN}:
            return plan.side is Side.SHORT
        return False

    def _higher_boundaries_at(self, time_ns: int) -> tuple[Any, ...]:
        if self._higher_snapshot_time_ns == time_ns:
            return self._higher_boundaries_before_retirement
        return tuple(self.higher_structure.boundaries_at(time_ns))

    def _higher_spatial_matches(self, plan: V5TradePlan) -> tuple[Any, ...]:
        wanted = ZoneSide.SUPPORT if plan.side is Side.LONG else ZoneSide.RESISTANCE
        # One tick of adjacency is the discrete-price equivalent of the same
        # boundary and is already the clustering convention used by the lower
        # structure engine.  It is not a tunable distance threshold.
        lower = plan.overlap_lower - self.tick_size
        upper = plan.overlap_upper + self.tick_size
        return tuple(
            zone
            for zone in self._higher_boundaries_at(plan.observed_time_ns)
            if zone.side is wanted
            and zone.observed_time_ns <= plan.observed_time_ns
            and zone.lower <= upper
            and zone.upper >= lower
        )

    def _plan_allowed(
        self,
        plan: V5TradePlan,
        state: DirectionState,
        spatial_matches: tuple[Any, ...],
    ) -> bool:
        aligned = self._directionally_aligned(plan, state)
        if plan.scenario_path == "ACCEPTANCE":
            return aligned
        # Rejection/rotation/bounce in the larger direction is an ordinary
        # pullback continuation.  Against it, the larger timeframe must supply
        # an actual price location for the reversal hypothesis.
        return aligned or bool(spatial_matches)

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 60:
            self.higher_direction.on_bar(bar)
            self.higher_structure.on_bar(bar)
            self._higher_snapshot_time_ns = bar.ts_close_ns
            self._higher_boundaries_before_retirement = tuple(
                self.higher_structure.boundaries_at(bar.ts_close_ns),
            )
            # Preserve the current completed bar's first interaction in the
            # snapshot above, then retire it for later lower-timeframe plans.
            self.higher_structure.observe_price(bar)

        plans = super().on_bar(timeframe_minutes, bar)
        if not plans:
            return []

        state = self.higher_direction.state()
        context_time = self.higher_direction.observed_time_ns
        output: list[V5TradePlan] = []
        for plan in plans:
            if context_time is not None and context_time > plan.observed_time_ns:
                raise RuntimeError("higher-timeframe router used future information")
            spatial = self._higher_spatial_matches(plan)
            allowed = self._plan_allowed(plan, state, spatial)
            context_values = {
                "higher_timeframe_state": state.value,
                "higher_timeframe_observed_ns": context_time,
                "higher_timeframe_close": (
                    None if not self.higher_direction.bars else self.higher_direction.bars[-1].close
                ),
                "higher_timeframe_latest_high": self.higher_direction.latest_high,
                "higher_timeframe_latest_low": self.higher_direction.latest_low,
                "higher_structure_match_ids": [zone.source_structure_id for zone in spatial],
                "higher_structure_match_kinds": [zone.kind.value for zone in spatial],
                "higher_structure_match_count": len(spatial),
                "directionally_aligned": self._directionally_aligned(plan, state),
                "acceptance_rule": HIGHER_TIMEFRAME_ACCEPTANCE_RULE,
                "reversal_rule": HIGHER_TIMEFRAME_REVERSAL_RULE,
                "state_translation": HIGHER_TIMEFRAME_STATE_TRANSLATION,
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
            "timeframe_minutes": 60,
            "pivot_span": self.higher_direction.PIVOT_SPAN,
            "state": self.higher_direction.state().value,
            "latest_high": self.higher_direction.latest_high,
            "latest_low": self.higher_direction.latest_low,
            "active_structure_diagnostics": dict(self.higher_structure.diagnostics),
            "acceptance_rule": HIGHER_TIMEFRAME_ACCEPTANCE_RULE,
            "reversal_rule": HIGHER_TIMEFRAME_REVERSAL_RULE,
            "state_translation": HIGHER_TIMEFRAME_STATE_TRANSLATION,
        }
        return output
