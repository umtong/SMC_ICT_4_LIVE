"""Counterfactual fixed-plan labels for every emitted EasyChart RE1 plan.

The live account can hold only one position, so ordinary trade audit data label
only the plan selected by account arbitration.  This strategy keeps the real
NautilusTrader account unchanged and, in parallel, advances every emitted plan
through subsequent completed one-minute bars until its immutable stop or target
is touched.  Same-bar stop/target ties are assigned to the stop, making the
label conservative.

The labels are research evidence, not executable orders.  They expose whether a
state/family/geometry policy generalizes without multiplying accounts, splitting
positions, or using outcomes in the trading strategy.  All feature snapshots are
recorded at plan creation; all labels use strictly later completed bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Side
from easychart_re1_turbulent_contraction import FullAuctionStateStrategy

COUNTERFACTUAL_FIXED_PLAN_LABEL_RULE = (
    "EXTERNAL_METHOD:EVERY_EMITTED_IMMUTABLE_PLAN_IS_LABELED_ON_STRICTLY_LATER_ONE_MINUTE_BARS_WITH_CONSERVATIVE_STOP_FIRST_TIES"
)
if COUNTERFACTUAL_FIXED_PLAN_LABEL_RULE not in _contracts.EXTERNAL_RULES:
    _contracts.EXTERNAL_RULES += (COUNTERFACTUAL_FIXED_PLAN_LABEL_RULE,)


@dataclass(slots=True)
class ShadowPlanState:
    plan: V5TradePlan
    registered_time_ns: int
    factor_regime: str
    factor_side: str | None
    factor_active_side: str | None
    factor_flips: int
    factor_events: int
    factor_participated: bool
    max_favorable_r: float = 0.0
    max_adverse_r: float = 0.0
    bars_observed: int = 0
    resolved: bool = False


class EasyChartRE1ShadowLabelStrategy(FullAuctionStateStrategy):
    """Production account plus non-trading counterfactual plan labels."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.shadow_plans: dict[str, ShadowPlanState] = {}
        self._shadow_registered: set[str] = set()
        self.shadow_counts: dict[str, int] = {}

    def _sinc(self, key: str) -> None:
        self.shadow_counts[key] = self.shadow_counts.get(key, 0) + 1

    @staticmethod
    def _risk(plan: V5TradePlan) -> float:
        return (
            plan.entry - plan.stop
            if plan.side is Side.LONG
            else plan.stop - plan.entry
        )

    @staticmethod
    def _target_hit(plan: V5TradePlan, high: float, low: float) -> bool:
        return high >= plan.target if plan.side is Side.LONG else low <= plan.target

    @staticmethod
    def _stop_hit(plan: V5TradePlan, high: float, low: float) -> bool:
        return low <= plan.stop if plan.side is Side.LONG else high >= plan.stop

    @staticmethod
    def _favorable_r(plan: V5TradePlan, high: float, low: float, risk: float) -> float:
        move = high - plan.entry if plan.side is Side.LONG else plan.entry - low
        return move / risk

    @staticmethod
    def _adverse_r(plan: V5TradePlan, high: float, low: float, risk: float) -> float:
        move = plan.entry - low if plan.side is Side.LONG else high - plan.entry
        return move / risk

    def _resolve_shadow(
        self,
        state: ShadowPlanState,
        *,
        outcome: str,
        time_ns: int,
        high: float | None,
        low: float | None,
    ) -> None:
        if state.resolved:
            return
        state.resolved = True
        plan = state.plan
        gross_result_r = plan.gross_rr if outcome == "TARGET" else (-1.0 if outcome == "STOP" else None)
        self._record(
            "shadow_plan_resolved",
            plan_id=plan.plan_id,
            causal_event_id=plan.causal_event_id,
            setup_id=plan.setup_id,
            symbol=plan.symbol,
            family=plan.family,
            scale_name=plan.scale_name,
            side=plan.side.name,
            scenario_path=plan.scenario_path,
            observed_time_ns=plan.observed_time_ns,
            interaction_time_ns=plan.interaction_time_ns,
            resolution_time_ns=time_ns,
            outcome=outcome,
            gross_result_r=gross_result_r,
            planned_gross_rr=plan.gross_rr,
            max_favorable_r=state.max_favorable_r,
            max_adverse_r=state.max_adverse_r,
            bars_observed=state.bars_observed,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            resolution_high=high,
            resolution_low=low,
            factor_regime=state.factor_regime,
            factor_side=state.factor_side,
            factor_active_side=state.factor_active_side,
            factor_flips=state.factor_flips,
            factor_events=state.factor_events,
            factor_participated=state.factor_participated,
            higher_zone_kind=str(plan.higher_zone_kind),
            lower_zone_kind=str(plan.lower_zone_kind),
            trigger_zone_kind=str(plan.trigger_zone_kind),
            target_zone_kind=str(plan.target_zone_kind),
            higher_strength_ratio=plan.higher_strength_ratio,
            lower_strength_ratio=plan.lower_strength_ratio,
            trigger_strength_ratio=plan.trigger_strength_ratio,
            source_rule_count=plan.source_rule_count,
            rule_provenance=COUNTERFACTUAL_FIXED_PLAN_LABEL_RULE,
        )
        self._sinc(f"shadow_resolved_{outcome.lower()}")

    def _advance_shadow_plans(self) -> None:
        one_minute: dict[str, Any] = {}
        for instrument_id, timeframe, bar in self.bar_bucket:
            if timeframe != self.EXECUTION_MINUTES:
                continue
            symbol = self.factor_symbols.get(instrument_id)
            if symbol is None:
                instrument = self.instruments.get(instrument_id)
                symbol = None if instrument is None else instrument.raw_symbol.value
            if symbol is not None:
                one_minute[symbol] = bar

        for state in self.shadow_plans.values():
            if state.resolved:
                continue
            plan = state.plan
            bar = one_minute.get(plan.symbol)
            if bar is None or int(bar.ts_event) <= int(plan.observed_time_ns):
                continue
            risk = self._risk(plan)
            if risk <= 0.0:
                self._resolve_shadow(
                    state,
                    outcome="INVALID_GEOMETRY",
                    time_ns=int(bar.ts_event),
                    high=float(bar.high),
                    low=float(bar.low),
                )
                continue
            high = float(bar.high)
            low = float(bar.low)
            state.bars_observed += 1
            state.max_favorable_r = max(
                state.max_favorable_r,
                self._favorable_r(plan, high, low, risk),
            )
            state.max_adverse_r = max(
                state.max_adverse_r,
                self._adverse_r(plan, high, low, risk),
            )
            stop_hit = self._stop_hit(plan, high, low)
            target_hit = self._target_hit(plan, high, low)
            if stop_hit:
                outcome = "STOP_TIE" if target_hit else "STOP"
                self._resolve_shadow(
                    state,
                    outcome="STOP" if outcome == "STOP" else "STOP_TIE",
                    time_ns=int(bar.ts_event),
                    high=high,
                    low=low,
                )
            elif target_hit:
                self._resolve_shadow(
                    state,
                    outcome="TARGET",
                    time_ns=int(bar.ts_event),
                    high=high,
                    low=low,
                )

    def _register_new_shadow_plans(self) -> None:
        snapshot = self._auction_snapshot()
        for plan_id, plan in self.plan_log.items():
            if plan_id in self._shadow_registered:
                continue
            self._shadow_registered.add(plan_id)
            participated = plan.symbol in snapshot.latest_agreeing_symbols
            state = ShadowPlanState(
                plan=plan,
                registered_time_ns=int(self.bar_bucket_ts or plan.observed_time_ns),
                factor_regime=snapshot.regime.value,
                factor_side=None if snapshot.side is None else snapshot.side.name,
                factor_active_side=None if snapshot.active_side is None else snapshot.active_side.name,
                factor_flips=snapshot.flips,
                factor_events=snapshot.events,
                factor_participated=participated,
            )
            self.shadow_plans[plan_id] = state
            self._record(
                "shadow_plan_registered",
                plan_id=plan.plan_id,
                causal_event_id=plan.causal_event_id,
                setup_id=plan.setup_id,
                symbol=plan.symbol,
                family=plan.family,
                scale_name=plan.scale_name,
                side=plan.side.name,
                scenario_path=plan.scenario_path,
                observed_time_ns=plan.observed_time_ns,
                interaction_time_ns=plan.interaction_time_ns,
                entry=plan.entry,
                stop=plan.stop,
                target=plan.target,
                planned_gross_rr=plan.gross_rr,
                factor_regime=state.factor_regime,
                factor_side=state.factor_side,
                factor_active_side=state.factor_active_side,
                factor_flips=state.factor_flips,
                factor_events=state.factor_events,
                factor_participated=state.factor_participated,
                higher_zone_kind=str(plan.higher_zone_kind),
                lower_zone_kind=str(plan.lower_zone_kind),
                trigger_zone_kind=str(plan.trigger_zone_kind),
                target_zone_kind=str(plan.target_zone_kind),
                higher_strength_ratio=plan.higher_strength_ratio,
                lower_strength_ratio=plan.lower_strength_ratio,
                trigger_strength_ratio=plan.trigger_strength_ratio,
                source_rule_count=plan.source_rule_count,
                rule_provenance=COUNTERFACTUAL_FIXED_PLAN_LABEL_RULE,
            )
            self._sinc("shadow_plan_registered")

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        self._advance_shadow_plans()
        super()._flush_bar_bucket()
        self._register_new_shadow_plans()

    def on_stop(self) -> None:
        now = self.clock.timestamp_ns()
        for state in self.shadow_plans.values():
            if not state.resolved:
                self._resolve_shadow(
                    state,
                    outcome="UNRESOLVED_AT_END",
                    time_ns=now,
                    high=None,
                    low=None,
                )
        super().on_stop()

    @property
    def shadow_label_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self.shadow_counts.items())),
            "registered": len(self.shadow_plans),
            "unresolved": sum(not state.resolved for state in self.shadow_plans.values()),
            "rule_provenance": COUNTERFACTUAL_FIXED_PLAN_LABEL_RULE,
        }
