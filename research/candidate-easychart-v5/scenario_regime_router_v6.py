"""Causal higher-timeframe direction routing for EasyChart micro scenarios.

The supplied material assigns different jobs to timeframes: larger structures
supply market direction and range, while smaller structures supply timing.  A
human trader naturally avoids a lower-timeframe entry which directly opposes a
clear higher-timeframe swing sequence.  This module translates that behavior
without scores, risk multipliers, moving averages or outcome labels.

Only fully confirmed 60-minute wick pivots are used.  The local pivot span was
already fixed at two bars by the v5 causal structure contract; no value is
selected from PnL.  Two completed highs and two completed lows define one of:

* BULL: higher high and higher low;
* BEAR: lower high and lower low;
* EXPANDING: higher high and lower low;
* CONTRACTING: lower high and higher low;
* FLAT / UNKNOWN: unresolved.

Two routing hypotheses are diagnosed:

* OPPOSITION_VETO: reject only LONG in clear BEAR and SHORT in clear BULL;
* STRICT_ALIGNMENT: admit only LONG in clear BULL and SHORT in clear BEAR.

The unfiltered micro-nearest-objective policy is retained as the control.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import Pivot, V5TradePlan
from domain import Candle, Side
from scenario_micro_nearest_target_v5 import (
    MicroNearestAnyTargetResearchScenarioBundleV5,
)


class MacroSwingState(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    EXPANDING = "EXPANDING"
    CONTRACTING = "CONTRACTING"
    FLAT = "FLAT"
    UNKNOWN = "UNKNOWN"


class RouterPolicy(str, Enum):
    NONE = "none"
    OPPOSITION_VETO = "opposition_veto"
    STRICT_ALIGNMENT = "strict_alignment"


DIRECTION_RULE = (
    "RESEARCH_HYPOTHESIS:CONFIRMED_60M_SPAN2_HIGH_LOW_SEQUENCE_DEFINES_CAUSAL_DIRECTION"
)
OPPOSITION_VETO_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:CLEAR_HIGHER_TIMEFRAME_DIRECTION_VETOES_OPPOSING_MICRO_ENTRY"
)
STRICT_ALIGNMENT_RULE = (
    "RESEARCH_HYPOTHESIS:MICRO_ENTRY_REQUIRES_CLEAR_60M_DIRECTIONAL_ALIGNMENT"
)


@dataclass(frozen=True, slots=True)
class MacroRegimeSnapshot:
    state: MacroSwingState
    observed_time_ns: int
    span: int
    previous_high: float | None
    latest_high: float | None
    previous_low: float | None
    latest_low: float | None


class CausalMacroSwingObserver:
    """Observe 60-minute bars and expose only confirmed pivot information."""

    TIMEFRAME_MINUTES = 60
    PIVOT_SPAN = 2

    def __init__(self, symbol: str, tick_size: float) -> None:
        self.book = LifecycleAwareStructureBook(
            symbol,
            self.TIMEFRAME_MINUTES,
            tick_size,
            pivot_spans=(self.PIVOT_SPAN,),
        )
        self.last_bar_time_ns = 0
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def on_bar(self, bar: Candle) -> None:
        self.book.on_bar(bar)
        self.last_bar_time_ns = bar.ts_close_ns
        self._inc("macro_bar_observed")

    def _last_two(self, side: str) -> tuple[Pivot, Pivot] | None:
        values = [
            pivot
            for pivot in self.book.pivots
            if pivot.span == self.PIVOT_SPAN and pivot.side == side
        ]
        if len(values) < 2:
            return None
        return values[-2], values[-1]

    def snapshot(self) -> MacroRegimeSnapshot:
        highs = self._last_two("HIGH")
        lows = self._last_two("LOW")
        if highs is None or lows is None:
            state = MacroSwingState.UNKNOWN
            previous_high = latest_high = previous_low = latest_low = None
        else:
            previous_high, latest_high = highs[0].price, highs[1].price
            previous_low, latest_low = lows[0].price, lows[1].price
            high_delta = (latest_high > previous_high) - (latest_high < previous_high)
            low_delta = (latest_low > previous_low) - (latest_low < previous_low)
            if high_delta > 0 and low_delta > 0:
                state = MacroSwingState.BULL
            elif high_delta < 0 and low_delta < 0:
                state = MacroSwingState.BEAR
            elif high_delta > 0 and low_delta < 0:
                state = MacroSwingState.EXPANDING
            elif high_delta < 0 and low_delta > 0:
                state = MacroSwingState.CONTRACTING
            else:
                state = MacroSwingState.FLAT
        self._inc(f"snapshot_{state.value.lower()}")
        return MacroRegimeSnapshot(
            state=state,
            observed_time_ns=self.last_bar_time_ns,
            span=self.PIVOT_SPAN,
            previous_high=previous_high,
            latest_high=latest_high,
            previous_low=previous_low,
            latest_low=latest_low,
        )


class _RegimeRoutedMicroBundleV6(MicroNearestAnyTargetResearchScenarioBundleV5):
    POLICY = RouterPolicy.NONE

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.macro_regime = CausalMacroSwingObserver(symbol, tick_size)
        self.router_diagnostics: dict[str, int] = {}

    def _router_inc(self, key: str) -> None:
        self.router_diagnostics[key] = self.router_diagnostics.get(key, 0) + 1

    @classmethod
    def allows(cls, state: MacroSwingState, side: Side) -> bool:
        if cls.POLICY is RouterPolicy.NONE:
            return True
        aligned = (
            (state is MacroSwingState.BULL and side is Side.LONG)
            or (state is MacroSwingState.BEAR and side is Side.SHORT)
        )
        if cls.POLICY is RouterPolicy.STRICT_ALIGNMENT:
            return aligned
        if cls.POLICY is RouterPolicy.OPPOSITION_VETO:
            opposed = (
                (state is MacroSwingState.BULL and side is Side.SHORT)
                or (state is MacroSwingState.BEAR and side is Side.LONG)
            )
            return not opposed
        raise RuntimeError(f"unsupported router policy {cls.POLICY}")

    def _provenance(self) -> tuple[str, ...]:
        if self.POLICY is RouterPolicy.OPPOSITION_VETO:
            return (DIRECTION_RULE, OPPOSITION_VETO_RULE)
        if self.POLICY is RouterPolicy.STRICT_ALIGNMENT:
            return (DIRECTION_RULE, STRICT_ALIGNMENT_RULE)
        return ()

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["macro_regime_router"] = {
            "policy": self.POLICY.value,
            "pivot_timeframe_minutes": self.macro_regime.TIMEFRAME_MINUTES,
            "pivot_span": self.macro_regime.PIVOT_SPAN,
            "observer": self.macro_regime.diagnostics,
            "router": self.router_diagnostics,
        }
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.macro_regime.TIMEFRAME_MINUTES:
            self.macro_regime.on_bar(bar)

        plans = super().on_bar(timeframe_minutes, bar)
        if not plans:
            return plans

        snapshot = self.macro_regime.snapshot()
        output: list[V5TradePlan] = []
        for plan in plans:
            allowed = self.allows(snapshot.state, plan.side)
            self._router_inc(f"considered_{snapshot.state.value.lower()}_{plan.side.name.lower()}")
            self._bundle_trace.append(
                {
                    "scenario_kind": "macro_regime_route_decision",
                    "event_time_ns": plan.observed_time_ns,
                    "scale_name": plan.scale_name,
                    "higher_timeframe_minutes": plan.higher_timeframe_minutes,
                    "decision_timeframe_minutes": plan.decision_timeframe_minutes,
                    "trigger_timeframe_minutes": plan.trigger_timeframe_minutes,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "router_policy": self.POLICY.value,
                    "macro_state": snapshot.state.value,
                    "macro_observed_time_ns": snapshot.observed_time_ns,
                    "macro_pivot_span": snapshot.span,
                    "macro_previous_high": snapshot.previous_high,
                    "macro_latest_high": snapshot.latest_high,
                    "macro_previous_low": snapshot.previous_low,
                    "macro_latest_low": snapshot.latest_low,
                    "allowed": allowed,
                },
            )
            if not allowed:
                self._router_inc(f"rejected_{snapshot.state.value.lower()}_{plan.side.name.lower()}")
                continue
            self._router_inc(f"accepted_{snapshot.state.value.lower()}_{plan.side.name.lower()}")
            additions = tuple(
                rule for rule in self._provenance() if rule not in plan.rule_provenance
            )
            output.append(
                replace(
                    plan,
                    rule_provenance=plan.rule_provenance + additions,
                ),
            )
        return output


class UnfilteredMicroBundleV6(_RegimeRoutedMicroBundleV6):
    POLICY = RouterPolicy.NONE


class OppositionVetoMicroBundleV6(_RegimeRoutedMicroBundleV6):
    POLICY = RouterPolicy.OPPOSITION_VETO


class StrictAlignmentMicroBundleV6(_RegimeRoutedMicroBundleV6):
    POLICY = RouterPolicy.STRICT_ALIGNMENT


BUNDLE_BY_ROUTER_POLICY = {
    RouterPolicy.NONE.value: UnfilteredMicroBundleV6,
    RouterPolicy.OPPOSITION_VETO.value: OppositionVetoMicroBundleV6,
    RouterPolicy.STRICT_ALIGNMENT.value: StrictAlignmentMicroBundleV6,
}
