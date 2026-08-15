"""Arm a confirmed control transfer and enter its first successful boundary return."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState, V5TradePlan
from domain import Side
from easychart_re1_control_transfer import (
    DecisionFrameControlTransferMixin,
    PendingControlTransfer,
)
from easychart_re1_flow import FlowSignal

CONTROL_TRANSFER_FIRST_RETEST_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:CONTROL_TRANSFER_ARMS_THE_FIRST_LATER_RETURN_TO_THE_RECLAIMED_DYNAMIC_BOUNDARY_AND_ENTRY_REQUIRES_A_CLOSE_ON_THE_INTENDED_SIDE"
)
CONTROL_TRANSFER_SWEEP_INVALIDATION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_ORIGINAL_SWEEP_EXTREME_OWNS_INVALIDATION_AND_A_FAILED_FIRST_BOUNDARY_RETURN_IS_NO_TRADE"
)
for _rule in (CONTROL_TRANSFER_FIRST_RETEST_RULE, CONTROL_TRANSFER_SWEEP_INVALIDATION_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(slots=True)
class ArmedControlRetest:
    confirmation_time_ns: int
    sweep_time_ns: int
    sweep_extreme: float
    signal: FlowSignal


class DecisionFrameFirstRetestMixin(DecisionFrameControlTransferMixin):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._confirmed_transfer_context: dict[str, tuple[PendingControlTransfer, FlowSignal]] = {}
        self._armed_control_retests: dict[str, ArmedControlRetest] = {}
        self._control_retest_counts: dict[str, int] = {}

    def _rtinc(self, key: str) -> None:
        self._control_retest_counts[key] = self._control_retest_counts.get(key, 0) + 1

    def _pending_signal(self, setup: Any, bar: Any, observation: Any) -> FlowSignal | None:
        pending = self._pending_control_transfers.get(setup.setup_id)
        signal = super()._pending_signal(setup, bar, observation)
        if signal is not None and pending is not None:
            self._confirmed_transfer_context[setup.setup_id] = (pending, signal)
        return signal

    def _create_flow_plan(
        self,
        setup: Any,
        bar: Any,
        signal: FlowSignal,
        *,
        acceptance: bool,
    ) -> V5TradePlan | None:
        transfer = signal.mechanism in {
            "DECISION_FRAME_PASSIVE_ABSORPTION_CONTROL_TRANSFER",
            "DECISION_FRAME_REINITIATIVE_CONTROL_TRANSFER",
        }
        if acceptance or not transfer:
            return super()._create_flow_plan(setup, bar, signal, acceptance=acceptance)
        context = self._confirmed_transfer_context.pop(setup.setup_id, None)
        if context is None:
            raise RuntimeError("confirmed transfer lost its sweep context")
        pending, _ = context
        self._armed_control_retests[setup.setup_id] = ArmedControlRetest(
            bar.ts_close_ns,
            pending.sweep_time_ns,
            pending.sweep_extreme,
            signal,
        )
        self._rtinc("armed")
        self._trace(
            "control_transfer_armed_for_first_boundary_retest",
            bar.ts_close_ns,
            setup,
            sweep_time_ns=pending.sweep_time_ns,
            sweep_extreme=pending.sweep_extreme,
            confirmation_close=bar.close,
            flow_mechanism=signal.mechanism,
            flow_strength=signal.strength,
            rule_provenance=(
                CONTROL_TRANSFER_FIRST_RETEST_RULE,
                CONTROL_TRANSFER_SWEEP_INVALIDATION_RULE,
            ),
        )
        return None

    @staticmethod
    def _sweep_breached(side: Side, bar: Any, extreme: float) -> bool:
        return bar.low < extreme if side is Side.LONG else bar.high > extreme

    def _advance_control_retest(
        self,
        setup: Any,
        bar: Any,
        armed: ArmedControlRetest,
    ) -> V5TradePlan | None:
        if bar.ts_close_ns <= armed.confirmation_time_ns:
            return None
        if self._target_is_spent(setup, bar):
            self._armed_control_retests.pop(setup.setup_id, None)
            self._rtinc("target_spent")
            self._finish(setup, SetupState.TARGET_SPENT, bar.ts_close_ns, "target_spent_before_control_retest")
            return None
        if self._sweep_breached(setup.side, bar, armed.sweep_extreme):
            self._armed_control_retests.pop(setup.setup_id, None)
            self._rtinc("sweep_breached")
            self._finish(setup, SetupState.INVALIDATED, bar.ts_close_ns, "original_sweep_extreme_breached_before_control_retest")
            return None

        _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
        touched = bar.low <= upper and bar.high >= lower
        if not touched:
            self._rtinc("waiting")
            return None
        held = bar.close > upper if setup.side is Side.LONG else bar.close < lower
        if not held:
            self._armed_control_retests.pop(setup.setup_id, None)
            self._rtinc("failed_hold")
            self._trace(
                "first_boundary_return_failed_to_hold",
                bar.ts_close_ns,
                setup,
                retest_low=bar.low,
                retest_high=bar.high,
                retest_close=bar.close,
                projected_lower=lower,
                projected_upper=upper,
                sweep_extreme=armed.sweep_extreme,
                rule_provenance=CONTROL_TRANSFER_SWEEP_INVALIDATION_RULE,
            )
            self._finish(setup, SetupState.NO_TRADE_GEOMETRY, bar.ts_close_ns, "first_boundary_return_failed_to_hold")
            return None

        stop = armed.sweep_extreme - self.tick_size if setup.side is Side.LONG else armed.sweep_extreme + self.tick_size
        proxy = self._flow_proxy(setup, bar.ts_close_ns)
        self._audit(proxy)
        plan = self._make_plan(
            setup,
            bar,
            entry=bar.close,
            stop=stop,
            trigger_zone=proxy,
            trigger_kind=armed.signal.kind,
            trigger_strength=armed.signal.strength,
        )
        self._armed_control_retests.pop(setup.setup_id, None)
        if plan is None:
            self._rtinc("geometry_rejected")
            return None
        self._flow_plans.append(plan)
        self._finc("flow_control_transfer_first_retest_plan_created")
        self._rtinc("plan_created")
        self._trace(
            "control_transfer_first_retest_plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            gross_rr=plan.gross_rr,
            sweep_time_ns=armed.sweep_time_ns,
            sweep_extreme=armed.sweep_extreme,
            confirmation_time_ns=armed.confirmation_time_ns,
            retest_low=bar.low,
            retest_high=bar.high,
            retest_close=bar.close,
            projected_lower=lower,
            projected_upper=upper,
            rule_provenance=(
                CONTROL_TRANSFER_FIRST_RETEST_RULE,
                CONTROL_TRANSFER_SWEEP_INVALIDATION_RULE,
            ),
            **self._signal_trace(armed.signal),
        )
        return plan

    def on_bar(self, timeframe_minutes: int, bar: Any) -> list[V5TradePlan]:
        if timeframe_minutes != self.trigger_minutes:
            return super().on_bar(timeframe_minutes, bar)
        existing = super().on_bar(timeframe_minutes, bar)
        created: list[V5TradePlan] = []
        for setup_id, armed in list(self._armed_control_retests.items()):
            setup = self._active.get(setup_id)
            if setup is None:
                self._armed_control_retests.pop(setup_id, None)
                self._rtinc("cleared")
                continue
            if setup.state is SetupState.WAITING_FOOTPRINT_RETEST:
                self._armed_control_retests.pop(setup_id, None)
                self._rtinc("visual_owner")
                continue
            plan = self._advance_control_retest(setup, bar, armed)
            if plan is not None:
                created.append(plan)
        for setup_id, (_, signal) in list(self._confirmed_transfer_context.items()):
            if signal.observation.ts_close_ns <= bar.ts_close_ns:
                self._confirmed_transfer_context.pop(setup_id, None)
        unique = {plan.plan_id: plan for plan in existing + created}
        return sorted(unique.values(), key=lambda p: (p.interaction_time_ns, p.observed_time_ns, p.plan_id))

    @property
    def control_transfer_retest_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._control_retest_counts.items())),
            "armed": len(self._armed_control_retests),
            "provisional": len(self._confirmed_transfer_context),
            "rules": (CONTROL_TRANSFER_FIRST_RETEST_RULE, CONTROL_TRANSFER_SWEEP_INVALIDATION_RULE),
        }
