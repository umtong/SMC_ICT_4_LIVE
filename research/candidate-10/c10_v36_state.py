"""v36 two-stage CE retest/rejection auction state machine.

The first failed-auction displacement is a detector confirmation, not an entry.
When enabled, the engine waits for price to revisit the exact consequent
encroachment of that displacement. A new completed bar must then break the CE
touch bar in the trade direction with the already-frozen displacement body,
aggressor-flow and close-location conditions. Only this second displacement
creates an executable passive retrace plan.

The final hard invalidation is the actual CE-retest extreme plus the frozen ATR
buffer; the primary target is either the source dealing-range equilibrium or,
for exact attribution, the original independent external draw.
"""
from __future__ import annotations

from dataclasses import replace
import os
from typing import Any

from logic import Auction, BarObs, Direction, Scenario, TradePlan
from session_engine import RegionalHandoffAuctionEngine

from c10_v36_overlay import consequent_encroachment
from c10_v36_overlay import rejection_displacement
from c10_v36_overlay import source_equilibrium


class ConsequentEncroachmentRejectionEngine(RegionalHandoffAuctionEngine):
    """Regional SCDAM with an explicit FAR CE-retest rejection state."""

    def __init__(self, config: Any, instrument_id: str) -> None:
        super().__init__(config, instrument_id)
        self._ce_states: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _ce_enabled() -> bool:
        return os.environ.get("C10_V36_CE_REJECTION", "0") == "1"

    @staticmethod
    def _equilibrium_enabled() -> bool:
        return os.environ.get("C10_V36_EQUILIBRIUM_TARGET", "0") == "1"

    def _target_delivered(
        self,
        *,
        direction: Direction,
        target: float,
        start_ts_ns: int,
        end_ts_ns: int,
    ) -> bool:
        for point in self.bars:
            if not start_ts_ns <= point.ts_ns <= end_ts_ns:
                continue
            if direction == Direction.LONG and point.high >= target:
                return True
            if direction == Direction.SHORT and point.low <= target:
                return True
        return False

    def _selected_target(self, a: Auction) -> tuple[float, str]:
        assert a.target_price is not None
        if self._equilibrium_enabled():
            return source_equilibrium(a.pool), "SOURCE_DEALING_RANGE_EQUILIBRIUM"
        return float(a.target_price), "INDEPENDENT_EXTERNAL_DRAW"

    def _decorate_plan(
        self,
        plan: TradePlan,
        *,
        state: dict[str, Any],
        entry_process: str,
    ) -> TradePlan:
        details = dict(plan.details)
        details["ce_rejection_primary"] = {
            "schema": "candidate-10-v36-ce-rejection-v1",
            "entry_process": entry_process,
            "target_contract": state["target_contract"],
            "consequent_encroachment": state.get("ce"),
            "initial_confirmation_ts_ns": state["confirmation_ts_ns"],
            "ce_touch_ts_ns": state.get("touch_ts_ns"),
            "ce_touch_bar_threshold": state.get("touch_bar_threshold"),
            "retest_extreme": state.get("retest_extreme"),
            "rejection_confirmation_ts_ns": state.get("rejection_ts_ns"),
            "initial_raid_invalidation": state["initial_raid_stop"],
            "final_retest_invalidation": plan.stop_price,
            "selected_target": plan.target_price,
            "original_independent_external_draw": state["original_external_target"],
            "source_equilibrium": state["source_equilibrium"],
            "state_sequence": [
                "FAILED_AUCTION_CONFIRMED",
                "CE_RETEST_ARMED",
                "CE_RETEST_TOUCHED",
                "CE_REJECTION_DISPLACEMENT_CONFIRMED",
                "SECOND_DISPLACEMENT_RETRACE_PENDING",
            ],
            "threshold_contract": (
                "uses only frozen Candidate 11 displacement body, directional "
                "aggressor flow and close-location conditions"
            ),
            "runner_contract": (
                "NOT_PART_OF_PRIMARY_TRADE; post-equilibrium continuation "
                "requires a separately funded state machine"
            ),
        }
        return replace(plan, details=details)

    def _arm_ce_retest(
        self,
        a: Auction,
        confirmation_bar: BarObs,
    ) -> TradePlan | None:
        assert a.direction is not None
        assert a.stop_price is not None
        assert a.target_price is not None
        assert a.zone_low is not None and a.zone_high is not None
        try:
            ce = consequent_encroachment(a.zone_low, a.zone_high)
            equilibrium = source_equilibrium(a.pool)
        except ValueError as exc:
            self._terminal(a, confirmation_bar, str(exc).upper().replace(" ", "_"))
            return None

        selected_target, target_contract = self._selected_target(a)
        sweep_ts_ns = (
            a.initial_sweep_ts_ns
            if a.initial_sweep_ts_ns is not None
            else a.sweep.ts_ns
        )
        if self._target_delivered(
            direction=a.direction,
            target=selected_target,
            start_ts_ns=sweep_ts_ns,
            end_ts_ns=confirmation_bar.ts_ns,
        ):
            self._terminal(
                a,
                confirmation_bar,
                "V36_SELECTED_TARGET_DELIVERED_BEFORE_CE_STATE",
            )
            return None

        if a.direction == Direction.LONG:
            causal_order = a.stop_price < ce < selected_target
            passive = ce < confirmation_bar.close
        else:
            causal_order = selected_target < ce < a.stop_price
            passive = ce > confirmation_bar.close
        if not causal_order or not passive:
            self._terminal(
                a,
                confirmation_bar,
                "V36_CE_STATE_NON_CAUSAL_PRICE_ORDER",
            )
            return None

        state = {
            "confirmation_index": self._index,
            "confirmation_ts_ns": confirmation_bar.ts_ns,
            "ce": ce,
            "source_equilibrium": equilibrium,
            "selected_target": selected_target,
            "target_contract": target_contract,
            "original_external_target": float(a.target_price),
            "initial_raid_stop": float(a.stop_price),
            "touch_index": None,
            "touch_ts_ns": None,
            "touch_bar_threshold": None,
            "retest_extreme": None,
            "rejection_ts_ns": None,
        }
        self._ce_states[a.pool.scenario_id] = state
        a.target_price = selected_target
        a.state = "FAR_CE_RETEST_ARMED"
        a.elapsed = 0
        self._event(
            a.pool.scenario_id,
            "CE_RETEST_ARMED",
            a.sweep.ts_ns,
            confirmation_bar.ts_ns,
            "FAR_CONFIRMED",
            "FAR_CE_RETEST_ARMED",
            target_contract,
            ce,
            {
                "consequent_encroachment": ce,
                "selected_target": selected_target,
                "source_equilibrium": equilibrium,
                "original_external_target": state["original_external_target"],
                "initial_raid_stop": state["initial_raid_stop"],
                "expiry_bars": self.config.retrace_expiry_bars,
            },
        )
        return None

    def _immediate_equilibrium_plan(
        self,
        a: Auction,
        confirmation_bar: BarObs,
        reason: str,
    ) -> TradePlan | None:
        assert a.direction is not None
        assert a.target_price is not None
        equilibrium = source_equilibrium(a.pool)
        sweep_ts_ns = (
            a.initial_sweep_ts_ns
            if a.initial_sweep_ts_ns is not None
            else a.sweep.ts_ns
        )
        if self._target_delivered(
            direction=a.direction,
            target=equilibrium,
            start_ts_ns=sweep_ts_ns,
            end_ts_ns=confirmation_bar.ts_ns,
        ):
            self._terminal(
                a,
                confirmation_bar,
                "V36_SOURCE_EQUILIBRIUM_DELIVERED_BEFORE_IMMEDIATE_PLAN",
            )
            return None
        original_target = float(a.target_price)
        a.target_price = equilibrium
        plan = super()._costed_limit_plan(
            a,
            confirmation_bar,
            "FAR_IMMEDIATE_RETRACE_TO_SOURCE_EQUILIBRIUM",
        )
        if plan is None:
            return None
        state = {
            "confirmation_ts_ns": confirmation_bar.ts_ns,
            "target_contract": "SOURCE_DEALING_RANGE_EQUILIBRIUM",
            "initial_raid_stop": float(a.stop_price),
            "original_external_target": original_target,
            "source_equilibrium": equilibrium,
        }
        return self._decorate_plan(
            plan,
            state=state,
            entry_process="IMMEDIATE_FIRST_DISPLACEMENT_RETRACE",
        )

    def _costed_limit_plan(
        self,
        a: Auction,
        confirmation_bar: BarObs,
        reason: str,
    ) -> TradePlan | None:
        if a.scenario != Scenario.FAR or a.state != "FAR_CONFIRMED":
            return super()._costed_limit_plan(a, confirmation_bar, reason)
        if self._ce_enabled():
            return self._arm_ce_retest(a, confirmation_bar)
        if self._equilibrium_enabled():
            return self._immediate_equilibrium_plan(a, confirmation_bar, reason)
        return super()._costed_limit_plan(a, confirmation_bar, reason)

    def _waiting_target_reached(
        self,
        a: Auction,
        bar: BarObs,
        target: float,
    ) -> bool:
        assert a.direction is not None
        return (
            bar.high >= target
            if a.direction == Direction.LONG
            else bar.low <= target
        )

    def _waiting_stop_reached(
        self,
        a: Auction,
        bar: BarObs,
        stop: float,
    ) -> bool:
        assert a.direction is not None
        return (
            bar.low <= stop
            if a.direction == Direction.LONG
            else bar.high >= stop
        )

    def _confirm_ce_rejection(
        self,
        a: Auction,
        bar: BarObs,
    ) -> TradePlan | None:
        state = self._ce_states.get(a.pool.scenario_id)
        if state is None or a.direction is None:
            self._terminal(a, bar, "V36_CE_STATE_MISSING")
            return None
        if self._index - int(state["confirmation_index"]) > self.config.retrace_expiry_bars:
            self._terminal(a, bar, "V36_CE_RETEST_EXPIRED")
            return None

        if self._waiting_stop_reached(a, bar, float(state["initial_raid_stop"])):
            self._terminal(a, bar, "V36_RAID_INVALIDATED_BEFORE_CE_ENTRY")
            return None
        if self._waiting_target_reached(a, bar, float(state["selected_target"])):
            self._terminal(a, bar, "V36_PRIMARY_TARGET_REACHED_BEFORE_CE_ENTRY")
            return None

        ce = float(state["ce"])
        touched = bar.low <= ce if a.direction == Direction.LONG else bar.high >= ce
        if a.state == "FAR_CE_RETEST_ARMED":
            if not touched:
                return None
            state["touch_index"] = self._index
            state["touch_ts_ns"] = bar.ts_ns
            state["touch_bar_threshold"] = (
                bar.high if a.direction == Direction.LONG else bar.low
            )
            state["retest_extreme"] = (
                bar.low if a.direction == Direction.LONG else bar.high
            )
            a.state = "FAR_CE_RETEST_TOUCHED"
            self._event(
                a.pool.scenario_id,
                "CE_RETEST_TOUCHED",
                a.sweep.ts_ns,
                bar.ts_ns,
                "FAR_CE_RETEST_ARMED",
                "FAR_CE_RETEST_TOUCHED",
                "COMPLETED_BAR_TOUCHED_CONSEQUENT_ENCROACHMENT",
                ce,
                {
                    "touch_bar_threshold": state["touch_bar_threshold"],
                    "retest_extreme": state["retest_extreme"],
                },
            )
            return None

        if a.state != "FAR_CE_RETEST_TOUCHED":
            return None
        if a.direction == Direction.LONG:
            state["retest_extreme"] = min(
                float(state["retest_extreme"]),
                bar.low,
            )
        else:
            state["retest_extreme"] = max(
                float(state["retest_extreme"]),
                bar.high,
            )
        if self._index <= int(state["touch_index"]):
            return None

        signal = rejection_displacement(
            direction=a.direction.value,
            bar=bar,
            touch_bar_threshold=float(state["touch_bar_threshold"]),
            atr=a.atr,
            config=self.config,
        )
        if not signal.confirmed:
            return None
        if self._waiting_target_reached(a, bar, float(state["selected_target"])):
            self._terminal(
                a,
                bar,
                "V36_PRIMARY_TARGET_REACHED_ON_REJECTION_CONFIRMATION",
            )
            return None

        buffer = self.config.stop_buffer_atr * a.atr
        if a.direction == Direction.LONG:
            stop = float(state["retest_extreme"]) - buffer
        else:
            stop = float(state["retest_extreme"]) + buffer
        a.stop_price = stop
        a.target_price = float(state["selected_target"])
        a.displacement_index = self._index
        a.zone_low, a.zone_high = self._zone_from_displacement(
            self.bars,
            self._index,
            a.direction,
        )
        a.state = "FAR_CE_RECONFIRMED"
        a.elapsed = 0
        state["rejection_ts_ns"] = bar.ts_ns
        self._event(
            a.pool.scenario_id,
            "CE_REJECTION_DISPLACEMENT_CONFIRMED",
            a.sweep.ts_ns,
            bar.ts_ns,
            "FAR_CE_RETEST_TOUCHED",
            "FAR_CE_RECONFIRMED",
            "TOUCH_BAR_BREAK_WITH_FROZEN_DISPLACEMENT_FLOW_LOCATION",
            ce,
            {
                "structural_break": signal.structural_break,
                "directional_flow": signal.directional_flow,
                "displacement_body": signal.displacement_body,
                "close_location": signal.close_location,
                "retest_extreme": state["retest_extreme"],
                "final_stop": stop,
                "selected_target": state["selected_target"],
                "target_contract": state["target_contract"],
                "zone_low": a.zone_low,
                "zone_high": a.zone_high,
            },
        )
        reason = (
            "FAR_CE_REJECTION_TO_SOURCE_EQUILIBRIUM"
            if state["target_contract"] == "SOURCE_DEALING_RANGE_EQUILIBRIUM"
            else "FAR_CE_REJECTION_TO_EXTERNAL_DRAW"
        )
        plan = super()._costed_limit_plan(a, bar, reason)
        if plan is None:
            return None
        return self._decorate_plan(
            plan,
            state=state,
            entry_process="CE_RETEST_SECOND_DISPLACEMENT_RETRACE",
        )

    def _confirm_far(self, a: Auction, bar: BarObs) -> TradePlan | None:
        if a.state in {"FAR_CE_RETEST_ARMED", "FAR_CE_RETEST_TOUCHED"}:
            return self._confirm_ce_rejection(a, bar)
        if a.state == "FAR_CE_RECONFIRMED":
            return None
        return super()._confirm_far(a, bar)

    def _confirm_aac(self, a: Auction, bar: BarObs) -> TradePlan | None:
        if a.state != "OBSERVE":
            return None
        return super()._confirm_aac(a, bar)

    def _terminal(self, a: Auction, bar: BarObs, reason: str) -> None:
        self._ce_states.pop(a.pool.scenario_id, None)
        super()._terminal(a, bar, reason)

    def mark_submitted(
        self,
        plan: TradePlan,
        quantity: Any,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._ce_states.pop(plan.scenario_id, None)
        super().mark_submitted(plan, quantity, details)

    def mark_rejected(
        self,
        plan: TradePlan,
        ts_ns: int,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._ce_states.pop(plan.scenario_id, None)
        super().mark_rejected(plan, ts_ns, reason, details)


__all__ = ["ConsequentEncroachmentRejectionEngine"]
