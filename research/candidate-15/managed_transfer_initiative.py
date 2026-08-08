"""Candidate 15 V8 managed residual information-transfer plans.

V7 proved that one narrow partial-transfer geometry is too sparse. V8 preserves
its causal fixes -- refreshed evidence timestamps, a unique residual receiver,
and prior-only five-minute ATR -- but restores two economically distinct receiver
states:

1. PARTIAL_CATCH_UP: convergence parity remains ahead and unconsumed.
2. PARITY_HANDOFF_RETEST: the current fresh receiver displacement itself crosses
   and closes beyond parity after no earlier post-evidence bar consumed it.

Both states retain the live external-liquidity target. The portfolio strategy
uses the recorded parity/completion reference to move the original protective
STOP_MARKET to the smallest fee-covering profitable price after a completed bar
confirms transfer. This module owns no fills, account state, or outcome data.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

from bounded_transfer_initiative import (
    BoundedResidualTransferContinuationEngine,
    BoundedTransferInitiativeState,
    BoundedTransferPersistentQuarterHourRouter,
)
from logic import BarObs, Direction, LogicConfig, TradePlan


V8_MODULE = "MANAGED_RESIDUAL_TRANSFER_MSS_FVG"
V8_ROUTER_KEY = "PORTFOLIO::MANAGED_RESIDUAL_TRANSFER"


class ManagedTransferPersistentQuarterHourRouter(
    BoundedTransferPersistentQuarterHourRouter,
):
    """V7 causal receiver state, retained unchanged for V8 management research."""


class ManagedResidualTransferContinuationEngine(
    BoundedResidualTransferContinuationEngine,
):
    """Restore partial-delivery and same-leg parity-handoff receiver plans."""

    def _parity_consumed_before_current(
        self,
        state: BoundedTransferInitiativeState,
        completed_end_ts_ns: int,
    ) -> bool:
        for bar in self._bars:
            if (
                bar.end_ts_ns <= state.effective_ts_ns
                or bar.end_ts_ns >= completed_end_ts_ns
            ):
                continue
            if state.direction is Direction.LONG and bar.high >= state.parity_price:
                return True
            if state.direction is Direction.SHORT and bar.low <= state.parity_price:
                return True
        return False

    @staticmethod
    def _current_bar_consumes_parity(
        state: BoundedTransferInitiativeState,
        completed: Any,
    ) -> tuple[bool, bool]:
        if state.direction is Direction.LONG:
            return completed.high >= state.parity_price, completed.close >= state.parity_price
        return completed.low <= state.parity_price, completed.close <= state.parity_price

    def _reject_managed_plan(
        self,
        plan: TradePlan,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        self.skips[reason] += 1
        self.mark_rejected(plan, plan.observed_ts_ns, reason, details)

    def _qualify_managed_transfer(
        self,
        plan: TradePlan,
        state: BoundedTransferInitiativeState | None,
        completed: Any,
    ) -> TradePlan | None:
        if not isinstance(state, BoundedTransferInitiativeState):
            self._reject_managed_plan(
                plan,
                "QHI_V8_TRANSFER_STATE_TYPE_UNRESOLVED",
                {},
            )
            return None
        if self.symbol != state.residual_symbol:
            self._reject_managed_plan(
                plan,
                "QHI_V8_PLAN_NOT_STATE_RESIDUAL",
                {
                    "symbol": self.symbol,
                    "residual_symbol": state.residual_symbol,
                    "accepted_symbols": list(state.accepted_symbols),
                },
            )
            return None

        sign = 1.0 if state.direction is Direction.LONG else -1.0
        gross_parity_gain = sign * (state.parity_price - plan.expected_entry)
        parity_net_gain = (
            gross_parity_gain
            - plan.expected_entry * self.config.effective_maker_rate
            - state.parity_price * self.config.effective_maker_rate
        )
        parity_costed_r = (
            parity_net_gain / plan.loss_per_unit
            if plan.loss_per_unit > 0.0
            else float("-inf")
        )
        prior_consumed = self._parity_consumed_before_current(
            state,
            completed.end_ts_ns,
        )
        current_touched, current_closed_beyond = self._current_bar_consumes_parity(
            state,
            completed,
        )

        if (
            not prior_consumed
            and current_touched
            and current_closed_beyond
        ):
            transfer_stage = "PARITY_HANDOFF_RETEST"
        elif (
            not prior_consumed
            and not current_touched
            and isfinite(parity_costed_r)
            and parity_costed_r > 0.0
        ):
            transfer_stage = "PARTIAL_CATCH_UP"
        else:
            self._reject_managed_plan(
                plan,
                "QHI_V8_TRANSFER_STAGE_UNRESOLVED",
                {
                    "accepted_symbols": list(state.accepted_symbols),
                    "residual_symbol": state.residual_symbol,
                    "effective_ts_ns": state.effective_ts_ns,
                    "parity_price": state.parity_price,
                    "parity_costed_r": parity_costed_r,
                    "prior_parity_consumed": prior_consumed,
                    "current_bar_touched_parity": current_touched,
                    "current_bar_closed_beyond_parity": current_closed_beyond,
                },
            )
            return None

        transfer = {
            "module": V8_MODULE,
            "policy": "MANAGED_RESIDUAL_INFORMATION_TRANSFER",
            "stage": transfer_stage,
            "effective_ts_ns": state.effective_ts_ns,
            "evidence_event_ids": list(state.evidence_event_ids),
            "accepted_symbols": list(state.accepted_symbols),
            "residual_symbol": state.residual_symbol,
            "residual_reference_price": state.residual_reference_price,
            "residual_confirmation_price": state.residual_confirmation_price,
            "residual_directional_progress": state.residual_directional_progress,
            "accepted_median_progress": state.median_directional_progress,
            "delivery_gap": state.delivery_gap,
            "parity_price": state.parity_price,
            "parity_gross_gain": gross_parity_gain,
            "parity_net_gain": parity_net_gain,
            "parity_costed_r": parity_costed_r,
            "prior_parity_consumed": prior_consumed,
            "current_bar_touched_parity": current_touched,
            "current_bar_closed_beyond_parity": current_closed_beyond,
            "management_trigger_model": "COMPLETED_CLOSE_AT_OR_BEYOND_PARITY_OR_COST_COVER",
            "management_action": "MODIFY_EXISTING_STOP_TO_MINIMUM_POSITIVE_COST_COVER",
            "final_target_model": plan.details.get("target_model"),
            "original_stop": plan.stop_price,
        }
        plan.details["module"] = V8_MODULE
        plan.details["route"] = "MANAGED_RESIDUAL_INFORMATION_TRANSFER"
        plan.details["candidate15_v8_transfer"] = transfer
        return plan

    def on_bar(
        self,
        observation: BarObs,
        *,
        state: BoundedTransferInitiativeState | None,
        external_engine: Any,
    ) -> TradePlan | None:
        # Evaluate the receiver bar against prior completed five-minute ranges.
        completed = self._aggregate.update(observation)
        if completed is None:
            return None
        previous_close = self._bars[-1].close if self._bars else None
        self._bars.append(completed)
        if len(self._bars) > 512:
            del self._bars[:-384]

        plan = None
        if state is None:
            self.skips["QHI_CONTINUATION_WITHOUT_ACTIVE_INITIATIVE"] += 1
        elif observation.ts_ns >= state.expires_ts_ns:
            self.skips["QHI_CONTINUATION_INITIATIVE_EXPIRED"] += 1
        else:
            plan = self._build_plan(
                completed=completed,
                observed_ts_ns=observation.ts_ns,
                state=state,
                external_engine=external_engine,
            )
        self._confirm_pivot(observation.ts_ns)
        self._ranges.append(self._true_range(completed, previous_close))
        if plan is None:
            return None
        return self._qualify_managed_transfer(plan, state, completed)

    def mark_submitted(
        self,
        plan: TradePlan,
        quantity: Any,
        details: dict[str, Any],
    ) -> None:
        super().mark_submitted(plan, quantity, details)
        if self.events:
            self.events[-1].details["module"] = V8_MODULE

    def mark_trade_terminal(self, ts_ns: int, reason: str) -> None:
        before = len(self.events)
        super().mark_trade_terminal(ts_ns, reason)
        if len(self.events) > before:
            self.events[-1].details["module"] = V8_MODULE
