"""Candidate 17 v2: confirmed initiative, first retest, executable geometry.

The v1 state router is retained. V2 changes the failed-auction execution leg:
initiative is evidence, not an entry. The system waits for the first retest of
the broken failure-bar boundary and trades only if that retest holds. It also
rejects any bracket whose expected execution costs exceed its structural price
risk, because such sizing turns small fill noise into account-level loss.
"""
from __future__ import annotations

from dataclasses import asdict
import math

from displayed_liquidity_router import InitiativeDecision
from displayed_liquidity_router import InitiativeObservation
from displayed_liquidity_router import advance_failure_leg
from failure_retest_router import FailureRetest
from failure_retest_router import RetestDecision
from failure_retest_router import RetestObservation
from failure_retest_router import advance_failure_retest
from logic import planned_loss_per_unit
from remembered_defense_strategy import Candidate17Config as Candidate17V1Config
from remembered_defense_strategy import Candidate17Strategy as Candidate17V1Strategy
from strategy_base import PendingSetup


class Candidate17V2Config(Candidate17V1Config, frozen=True):
    """Retest uses the already registered acceptance geometry."""


class Candidate17V2Strategy(Candidate17V1Strategy):
    """Trade a failed auction only after its first post-initiative retest holds."""

    def __init__(self, config: Candidate17V2Config) -> None:
        super().__init__(config=config)
        self.failure_retest: FailureRetest | None = None
        self.diagnostics.update(
            {
                "candidate17_v2_failure_retests_armed": 0,
                "candidate17_v2_failure_retest_observations": 0,
                "candidate17_v2_failure_retests_confirmed": 0,
                "candidate17_v2_failure_retests_invalidated": 0,
                "candidate17_v2_failure_retests_expired": 0,
                "candidate17_v2_cost_dominated_geometry_rejections": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.failure_retest = None

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        retest_pending = self.pending is not None and self.pending.branch == "FAILURE_RETEST"
        super()._expire_pending(row, reason)
        if retest_pending:
            self.failure_retest = None

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        if self.pending is not None and self.pending.branch == "FAILURE_RETEST":
            return self._process_failure_retest(row)
        return super()._process_pending(row)

    def _process_failure_initiative(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        state = self.failure_leg
        if setup is None or state is None:
            self._expire_pending(row, "MISSING_FROZEN_FAILURE_STATE")
            return True
        if self.bar_index <= setup.created_index:
            return True

        state = advance_failure_leg(
            state,
            InitiativeObservation(
                bar_index=self.bar_index,
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                flow_60s=self._feature("flow_60s"),
                ret_60s_bps=self._feature("ret_60s_bps"),
                depth_imbalance_1=self._feature("depth_imbalance_1"),
                liquidity_ahead_change_1m=self._feature(
                    self._ahead_depth_field(state.side),
                ),
            ),
        )
        self.failure_leg = state
        setup.details["latest_failure_leg"] = {
            **asdict(state),
            "decision": state.decision.value,
        }
        self._transition(
            setup.scenario_id,
            "FAILURE_INITIATIVE_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            "FAILURE_FROZEN"
            if state.decision is InitiativeDecision.WAITING
            else state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )
        if state.decision is InitiativeDecision.WAITING:
            return True
        if state.decision is InitiativeDecision.EXPIRED:
            self.diagnostics["candidate16_v2_failure_initiative_expired"] = int(
                self.diagnostics["candidate16_v2_failure_initiative_expired"],
            ) + 1
            self.pending = None
            self.failure_leg = None
            return True
        if state.decision is InitiativeDecision.INVALIDATED:
            self.diagnostics["candidate16_v2_failure_initiative_invalidated"] = int(
                self.diagnostics["candidate16_v2_failure_initiative_invalidated"],
            ) + 1
            self.diagnostics["candidate17_failure_reaccess_reroutes"] = int(
                self.diagnostics["candidate17_failure_reaccess_reroutes"],
            ) + 1
            details = dict(setup.details)
            self._arm_defense_memory(
                setup=setup,
                row=row,
                details=details,
                cause="CLEAN_FAILURE_REACCESSED_BEFORE_OPPOSITE_INITIATIVE",
                last_index=self.bar_index - 1,
            )
            if self.pending is not None and self.pending.branch == "DEFENSE_MEMORY":
                return self._process_defense_memory(row)
            return True

        boundary = state.failure_high if state.side > 0 else state.failure_low
        retest = FailureRetest(
            scenario_id=setup.scenario_id,
            side=state.side,
            boundary=boundary,
            parent_extreme=state.parent_extreme,
            atr=setup.atr,
            created_index=self.bar_index,
            last_index=self.bar_index,
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
        )
        details = {
            **setup.details,
            "candidate17_v2_branch": "FAILED_AUCTION_FIRST_RETEST",
            "confirmed_failure_leg": {
                **asdict(state),
                "decision": state.decision.value,
            },
            "retest_boundary": boundary,
            "retest_parent_extreme": state.parent_extreme,
        }
        self.failure_retest = retest
        self.pending = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="FAILURE_RETEST",
            side=setup.side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=retest.expires_index,
            sweep_extreme=setup.sweep_extreme,
            structure=boundary,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        self.failure_leg = None
        self.diagnostics["candidate16_v2_failure_initiatives"] = int(
            self.diagnostics["candidate16_v2_failure_initiatives"],
        ) + 1
        self.diagnostics["candidate17_v2_failure_retests_armed"] = int(
            self.diagnostics["candidate17_v2_failure_retests_armed"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "FAILURE_INITIATIVE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "FIRST_RETEST_ARMED",
            "INITIATIVE_IS_EVIDENCE_NOT_ENTRY;_WAIT_FOR_FIRST_RETEST",
            float(row["close"]),
            details,
        )
        return True

    def _process_failure_retest(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        state = self.failure_retest
        if setup is None or state is None:
            self._expire_pending(row, "MISSING_FAILURE_RETEST_STATE")
            return True
        if self.bar_index <= state.last_index:
            return True

        state = advance_failure_retest(
            state,
            RetestObservation(
                bar_index=self.bar_index,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                flow_15s=self._feature("flow_15s"),
                depth_imbalance_1=self._feature("depth_imbalance_1"),
            ),
            touch_tolerance_atr=self.config.acceptance_retrace_tolerance_atr,
            max_counterflow=self.config.acceptance_max_counterflow,
            min_close_location=self.config.acceptance_retest_close_location,
        )
        self.failure_retest = state
        setup.details["latest_failure_retest"] = {
            **asdict(state),
            "decision": state.decision.value,
        }
        self.diagnostics["candidate17_v2_failure_retest_observations"] = int(
            self.diagnostics["candidate17_v2_failure_retest_observations"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "FAILURE_RETEST_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            "FIRST_RETEST_ARMED"
            if state.decision is RetestDecision.WAITING
            else state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )
        if state.decision is RetestDecision.WAITING:
            return True
        if state.decision is RetestDecision.INVALIDATED:
            self.diagnostics["candidate17_v2_failure_retests_invalidated"] = int(
                self.diagnostics["candidate17_v2_failure_retests_invalidated"],
            ) + 1
            self.pending = None
            self.failure_retest = None
            return True
        if state.decision is RetestDecision.EXPIRED:
            self.diagnostics["candidate17_v2_failure_retests_expired"] = int(
                self.diagnostics["candidate17_v2_failure_retests_expired"],
            ) + 1
            self.pending = None
            self.failure_retest = None
            return True

        completed = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="REJECTION",
            side=setup.side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=setup.sweep_extreme,
            structure=state.boundary,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **setup.details,
                "candidate17_v2_entry": "FIRST_RETEST_HELD_AFTER_LATER_INITIATIVE",
                "confirmed_failure_retest": {
                    **asdict(state),
                    "decision": state.decision.value,
                },
            },
        )
        self.pending = completed
        self.failure_retest = None
        self.diagnostics["candidate17_v2_failure_retests_confirmed"] = int(
            self.diagnostics["candidate17_v2_failure_retests_confirmed"],
        ) + 1
        self._transition(
            completed.scenario_id,
            "FAILURE_RETEST_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            state.reason,
            float(row["close"]),
            completed.details,
        )
        self._submit_entry(completed, row)
        return True

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        """Require market invalidation distance to dominate expected execution cost."""
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            self._expire_pending(row, "INVALID_ATR_AT_ENTRY")
            return False
        side = setup.side
        entry = float(row["close"])
        if setup.branch == "REJECTION":
            stop = setup.sweep_extreme - side * self.config.stop_buffer_atr * atr
        elif side > 0:
            stop = min(
                setup.pool_level - self.config.stop_buffer_atr * atr,
                float(row["low"]) - 0.25 * self.config.stop_buffer_atr * atr,
            )
        else:
            stop = max(
                setup.pool_level + self.config.stop_buffer_atr * atr,
                float(row["high"]) + 0.25 * self.config.stop_buffer_atr * atr,
            )

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, slippage_rate)
        structural_price_risk = abs(entry - stop)
        execution_cost_component = planned_loss - structural_price_risk
        setup.details.update(
            {
                "structural_price_risk_per_unit": structural_price_risk,
                "expected_execution_cost_per_unit": execution_cost_component,
                "structure_to_cost_ratio": (
                    structural_price_risk / execution_cost_component
                    if execution_cost_component > 0.0
                    else math.inf
                ),
            },
        )
        if (
            not math.isfinite(planned_loss)
            or planned_loss <= 0.0
            or not math.isfinite(execution_cost_component)
            or execution_cost_component < 0.0
            or structural_price_risk < execution_cost_component
        ):
            self.diagnostics["candidate17_v2_cost_dominated_geometry_rejections"] = int(
                self.diagnostics["candidate17_v2_cost_dominated_geometry_rejections"],
            ) + 1
            self._expire_pending(
                row,
                "EXPECTED_EXECUTION_COST_EXCEEDS_STRUCTURAL_INVALIDATION_RISK",
            )
            return False
        return super()._submit_entry(setup, row)


__all__ = ["Candidate17V2Config", "Candidate17V2Strategy"]
