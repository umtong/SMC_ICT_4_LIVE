"""One-pass research labels for every emitted immutable RE1 plan.

The real Nautilus account remains single-position.  In parallel, every emitted
plan is advanced on strictly later completed one-minute bars until its original
stop or target is touched.  Labels are written to the event log only and never
participate in order decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts_v5 import V5TradePlan
from domain import Side
from execution_re1 import EasyChartMTFConfig, EasyChartRE1Strategy


@dataclass(slots=True)
class ShadowState:
    plan: V5TradePlan
    max_favorable_r: float = 0.0
    max_adverse_r: float = 0.0
    bars_observed: int = 0
    resolved: bool = False


class EasyChartRE1PlanLabelStrategy(EasyChartRE1Strategy):
    """Production-equivalent account plus non-trading immutable-plan labels."""

    def __init__(self, config: EasyChartMTFConfig) -> None:
        super().__init__(config)
        self.shadow_plans: dict[str, ShadowState] = {}
        self.shadow_registered: set[str] = set()
        self.shadow_last_time_ns: int | None = None

    @staticmethod
    def _risk(plan: V5TradePlan) -> float:
        return abs(float(plan.entry) - float(plan.stop))

    @staticmethod
    def _target_hit(plan: V5TradePlan, high: float, low: float) -> bool:
        return high >= plan.target if plan.side is Side.LONG else low <= plan.target

    @staticmethod
    def _stop_hit(plan: V5TradePlan, high: float, low: float) -> bool:
        return low <= plan.stop if plan.side is Side.LONG else high >= plan.stop

    @staticmethod
    def _favorable(plan: V5TradePlan, high: float, low: float, risk: float) -> float:
        move = high - plan.entry if plan.side is Side.LONG else plan.entry - low
        return float(move / risk)

    @staticmethod
    def _adverse(plan: V5TradePlan, high: float, low: float, risk: float) -> float:
        move = plan.entry - low if plan.side is Side.LONG else high - plan.entry
        return float(move / risk)

    def _resolve(
        self,
        state: ShadowState,
        *,
        outcome: str,
        time_ns: int | None,
        high: float | None = None,
        low: float | None = None,
    ) -> None:
        if state.resolved:
            return
        state.resolved = True
        plan = state.plan
        self._record(
            "shadow_plan_resolved",
            plan_id=plan.plan_id,
            causal_event_id=plan.causal_event_id,
            setup_id=plan.setup_id,
            symbol=plan.symbol,
            outcome=outcome,
            resolution_time_ns=time_ns,
            max_favorable_r=state.max_favorable_r,
            max_adverse_r=state.max_adverse_r,
            bars_observed=state.bars_observed,
            resolution_high=high,
            resolution_low=low,
        )

    def _advance_shadow(self) -> None:
        one_minute: dict[str, Any] = {}
        for instrument_id, timeframe, bar in self.bar_bucket:
            if timeframe != self.EXECUTION_MINUTES:
                continue
            instrument = self.instruments.get(instrument_id)
            if instrument is not None:
                one_minute[instrument.raw_symbol.value] = bar
                self.shadow_last_time_ns = int(bar.ts_event)

        for state in self.shadow_plans.values():
            if state.resolved:
                continue
            plan = state.plan
            bar = one_minute.get(plan.symbol)
            if bar is None or int(bar.ts_event) <= int(plan.observed_time_ns):
                continue
            risk = self._risk(plan)
            if risk <= 0.0:
                self._resolve(state, outcome="INVALID_GEOMETRY", time_ns=int(bar.ts_event))
                continue
            high = float(bar.high)
            low = float(bar.low)
            state.bars_observed += 1
            state.max_favorable_r = max(
                state.max_favorable_r, self._favorable(plan, high, low, risk)
            )
            state.max_adverse_r = max(
                state.max_adverse_r, self._adverse(plan, high, low, risk)
            )
            stop_hit = self._stop_hit(plan, high, low)
            target_hit = self._target_hit(plan, high, low)
            if stop_hit:
                self._resolve(
                    state,
                    outcome="STOP_TIE" if target_hit else "STOP",
                    time_ns=int(bar.ts_event),
                    high=high,
                    low=low,
                )
            elif target_hit:
                self._resolve(
                    state,
                    outcome="TARGET",
                    time_ns=int(bar.ts_event),
                    high=high,
                    low=low,
                )

    def _register_shadow(self) -> None:
        for plan_id, plan in self.plan_log.items():
            if plan_id in self.shadow_registered:
                continue
            self.shadow_registered.add(plan_id)
            self.shadow_plans[plan_id] = ShadowState(plan=plan)
            self._record("shadow_plan_registered", **self._plan_event_values(plan))

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        self._advance_shadow()
        super()._flush_bar_bucket()
        self._register_shadow()

    def on_stop(self) -> None:
        for state in self.shadow_plans.values():
            if not state.resolved:
                self._resolve(
                    state,
                    outcome="UNRESOLVED",
                    time_ns=self.shadow_last_time_ns,
                )
        super().on_stop()
