"""Source-role top-down routing for the integrated EasyChart policy.

EasyChart assigns different jobs to timeframes: larger and intermediate charts
establish trend and important structure, while 15m/5m/1m charts refine entry.
The prior micro policy generated long and short plans symmetrically even when
the completed hourly swing structure clearly pointed one way.

This module observes only completed 60-minute bars.  Confirmed wick pivots
produce HH/HL, LH/LL, TRANSITION or UNRESOLVED states.  A resolved hourly trend
vetoes only a lower-timeframe plan in the opposite direction.  Conflicting or
unresolved hourly states do not invent a directional opinion and therefore do
not veto a plan.  Entry, stop, target, risk, one-slot routing and position
management remain unchanged.

The module also corrects channel objective persistence: when an accepted channel
break initially selected a nearer pre-existing opposing structure instead of the
first equal-width extension, that existing objective remains selected.  A moving
extension is recalculated at entry only when the extension was actually chosen.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, V5TradePlan
from diagonal_core_v20 import DiagonalCoreScenarioEngine, MicroDiagonalCoreBundleV20
from domain import Candle, Side
from scenario_channel_extension_v16 import ChannelObjectiveKind
from scenario_close_detached_v14 import CloseDetachedRetestScenarioEngine


HOURLY_DIRECTION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "COMPLETED_HOURLY_HH_HL_OR_LH_LL_VETOES_ONLY_OPPOSITE_LOWER_TIMEFRAME_DIRECTION"
)
CHANNEL_TARGET_PERSISTENCE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "CHANNEL_EXTENSION_MOVES_TO_ENTRY_TIME_ONLY_WHEN_EXTENSION_WAS_THE_SELECTED_OBJECTIVE"
)
for _rule in (HOURLY_DIRECTION_RULE, CHANNEL_TARGET_PERSISTENCE_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class HourlyState(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    TRANSITION = "TRANSITION"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class HourlyPivot:
    side: str
    price: float
    index: int
    observed_index: int
    observed_time_ns: int
    span: int


@dataclass(frozen=True, slots=True)
class HourlySnapshot:
    observed_time_ns: int
    local_state: HourlyState
    structural_state: HourlyState
    resolved_state: HourlyState
    local_span: int
    structural_span: int
    bars_observed: int


class CausalHourlyDirection:
    """Online hourly direction from two causal pivot scales."""

    def __init__(self, *, pivot_spans: tuple[int, int] = (2, 6)) -> None:
        if len(pivot_spans) != 2 or any(span <= 0 for span in pivot_spans):
            raise ValueError("exactly two positive pivot spans are required")
        self.local_span, self.structural_span = sorted(pivot_spans)
        self.bars: list[Candle] = []
        self.pivots: dict[int, list[HourlyPivot]] = {
            self.local_span: [],
            self.structural_span: [],
        }
        self._pivot_ids: set[tuple[int, str, int]] = set()
        self._latest: HourlySnapshot | None = None

    def _add_pivot(
        self,
        span: int,
        side: str,
        center: int,
        observed_index: int,
    ) -> None:
        identity = (span, side, center)
        if identity in self._pivot_ids:
            return
        self._pivot_ids.add(identity)
        bar = self.bars[center]
        self.pivots[span].append(
            HourlyPivot(
                side=side,
                price=bar.high if side == "HIGH" else bar.low,
                index=center,
                observed_index=observed_index,
                observed_time_ns=self.bars[observed_index].ts_close_ns,
                span=span,
            ),
        )

    def _register(self, observed_index: int) -> None:
        for span in (self.local_span, self.structural_span):
            center = observed_index - span
            if center < span:
                continue
            window = self.bars[center - span : center + span + 1]
            if len(window) != 2 * span + 1:
                continue
            center_bar = self.bars[center]
            highs = [bar.high for bar in window]
            lows = [bar.low for bar in window]
            if center_bar.high == max(highs) and highs.count(center_bar.high) == 1:
                self._add_pivot(span, "HIGH", center, observed_index)
            if center_bar.low == min(lows) and lows.count(center_bar.low) == 1:
                self._add_pivot(span, "LOW", center, observed_index)

    def _last_two(self, span: int, side: str) -> tuple[HourlyPivot | None, HourlyPivot | None]:
        values = [pivot for pivot in self.pivots[span] if pivot.side == side]
        if len(values) < 2:
            return None, None
        return values[-2], values[-1]

    def state(self, span: int) -> HourlyState:
        previous_high, last_high = self._last_two(span, "HIGH")
        previous_low, last_low = self._last_two(span, "LOW")
        if any(value is None for value in (previous_high, last_high, previous_low, last_low)):
            return HourlyState.UNRESOLVED
        assert previous_high is not None and last_high is not None
        assert previous_low is not None and last_low is not None
        if last_high.price > previous_high.price and last_low.price > previous_low.price:
            return HourlyState.UP
        if last_high.price < previous_high.price and last_low.price < previous_low.price:
            return HourlyState.DOWN
        return HourlyState.TRANSITION

    @staticmethod
    def resolve(local: HourlyState, structural: HourlyState) -> HourlyState:
        directional = {HourlyState.UP, HourlyState.DOWN}
        if local in directional and structural in directional:
            return local if local is structural else HourlyState.TRANSITION
        if structural in directional:
            return structural
        if local in directional:
            return local
        if HourlyState.TRANSITION in {local, structural}:
            return HourlyState.TRANSITION
        return HourlyState.UNRESOLVED

    def on_bar(self, bar: Candle) -> HourlySnapshot:
        if self.bars and bar.ts_close_ns <= self.bars[-1].ts_close_ns:
            raise ValueError("hourly bars must be strictly chronological")
        self.bars.append(bar)
        self._register(len(self.bars) - 1)
        local = self.state(self.local_span)
        structural = self.state(self.structural_span)
        self._latest = HourlySnapshot(
            observed_time_ns=bar.ts_close_ns,
            local_state=local,
            structural_state=structural,
            resolved_state=self.resolve(local, structural),
            local_span=self.local_span,
            structural_span=self.structural_span,
            bars_observed=len(self.bars),
        )
        return self._latest

    @property
    def latest(self) -> HourlySnapshot | None:
        return self._latest


class PersistentChannelTargetScenarioEngine(DiagonalCoreScenarioEngine):
    """Keep the objective selected by the completed pre-entry scenario."""

    def _channel_target_at(
        self,
        setup: ScenarioSetup,
        time_ns: int,
    ) -> tuple[Any, float] | None:
        if setup.path is ScenarioPath.ACCEPTANCE and setup.channel_id is not None:
            target_kind = getattr(
                getattr(setup.target_zone, "kind", None),
                "value",
                getattr(setup.target_zone, "kind", None),
            )
            if target_kind == ChannelObjectiveKind.CHANNEL_EXTENSION_TARGET.value:
                channel = self.structure.channel_by_id(setup.channel_id)
                if channel is None:
                    return None
                return self._channel_extension_at(channel, setup.side, time_ns)
            # A nearer pre-existing opposing structure was selected.  It is a
            # fixed observed price, not a moving channel projection.
            return None
        return CloseDetachedRetestScenarioEngine._channel_target_at(self, setup, time_ns)


class HourlyDirectionBundleV21(MicroDiagonalCoreBundleV20):
    """One micro plan stream, one hourly directional resolver."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = PersistentChannelTargetScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0
        self.hourly_direction = CausalHourlyDirection()
        self._hourly_veto_counts: dict[str, int] = {}

    @staticmethod
    def _opposite(plan: V5TradePlan, state: HourlyState) -> bool:
        return (
            state is HourlyState.UP and plan.side is Side.SHORT
        ) or (
            state is HourlyState.DOWN and plan.side is Side.LONG
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == 60:
            self.hourly_direction.on_bar(bar)
        plans = super().on_bar(timeframe_minutes, bar)
        if not plans:
            return plans

        snapshot = self.hourly_direction.latest
        if snapshot is None:
            return plans
        accepted: list[V5TradePlan] = []
        for plan in plans:
            if snapshot.observed_time_ns > plan.observed_time_ns:
                raise RuntimeError("hourly direction used information after plan observation")
            if self._opposite(plan, snapshot.resolved_state):
                key = f"{snapshot.resolved_state.value}_VETO_{plan.side.name}"
                self._hourly_veto_counts[key] = self._hourly_veto_counts.get(key, 0) + 1
                self._bundle_trace.append(
                    {
                        "scenario_kind": "hourly_direction_opposite_plan_vetoed",
                        "event_time_ns": plan.observed_time_ns,
                        "plan_id": plan.plan_id,
                        "symbol": plan.symbol,
                        "side": plan.side.name,
                        "family": plan.family,
                        "scenario_path": plan.scenario_path,
                        "higher_zone_kind": str(plan.higher_zone_kind),
                        "hourly_observed_time_ns": snapshot.observed_time_ns,
                        "hourly_local_state": snapshot.local_state.value,
                        "hourly_structural_state": snapshot.structural_state.value,
                        "hourly_resolved_state": snapshot.resolved_state.value,
                        "provenance": HOURLY_DIRECTION_RULE,
                    },
                )
                continue
            accepted.append(plan)
        return accepted

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        snapshot = self.hourly_direction.latest
        output["hourly_direction_router"] = {
            "policy": "RESOLVED_HOURLY_DIRECTION_VETOES_ONLY_OPPOSITE_SIDE",
            "latest": None
            if snapshot is None
            else {
                "observed_time_ns": snapshot.observed_time_ns,
                "local_state": snapshot.local_state.value,
                "structural_state": snapshot.structural_state.value,
                "resolved_state": snapshot.resolved_state.value,
                "bars_observed": snapshot.bars_observed,
            },
            "veto_counts": dict(sorted(self._hourly_veto_counts.items())),
            "rule_provenance": HOURLY_DIRECTION_RULE,
        }
        output["channel_target_persistence"] = {
            "rule_provenance": CHANNEL_TARGET_PERSISTENCE_RULE,
        }
        return output
