#!/usr/bin/env python3
"""Candidate 05 v39: positioning-reset gate for early sponsored CHoCH."""
from __future__ import annotations

import math
from typing import Any

from inventory_repricing_logic import QH_CONTEXT_MAX_AGE_BARS
from inventory_repricing_logic import QH_CONTEXT_MIN_AGE_BARS
from positioning_reset_logic import completed_path_efficiency
from positioning_reset_logic import positioning_reset_supports_early_reversal
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v18 import ExecutionConfirmedCancelStrategy
from strategy_v9 import ArmedEntryPath
from strategy_v9 import ObservedEntryPathStrategy
from strategy_v26 import ScenarioValidEntryStrategy


COUNTER_CONTEXT_ROTATION_MIN_TARGET_PROGRESS = 1.0 / 3.0
TWO_TO_ONE_FLOW_IMBALANCE = 1.0 / 3.0
COUNTER_CONTEXT_REACCEPTANCE_EFFICIENCY_MIN = 0.20


class PositioningResetReversalStrategy(ScenarioValidEntryStrategy):
    """Route premature CHoCHs to observation instead of immediate participation.

    v39 changes one entry decision. An early coherent sponsored CHoCH may use the
    existing price-capped immediate order only when the sweep was preceded by a
    directional 30-minute path, the premium index was already normalizing in the
    proposed reversal direction, and 15-minute open interest is not materially
    expanding at CHoCH. Otherwise the unchanged v17 path waits for a causal
    retrace/breakaway response. Detector, target, stop, costs, 3% NAV sizing,
    Nautilus execution and pending-order lifecycle are inherited unchanged.

    The position manager resolves one completed-bar ambiguity at an intermediate
    liquidity milestone. If the same completed bar touches both the milestone
    and its software-protected level, intrabar ordering is not guessed. A close
    beyond the consumed pool with reversal-side visible depth and failed
    aggressive counterflow is treated as acceptance, so the nearer software
    milestone is retired while the original exchange-side structural stop and
    live-liquidity target remain unchanged. If price and depth accept but
    aggressive flow is aligned with the position, protection begins only from
    the next completed bar. All other ambiguous bars retain the prior
    conservative flatten.

    A position opened against an already accepted quarter-hour repricing state is
    treated as a bounded counter-context rotation, not a new directional regime.
    Once that rotation has completed at least one third of its frozen path to the
    actual liquidity target, a completed close back through its CHoCH reference
    with two-to-one adverse one- and three-minute aggressive flow and efficient
    adverse repricing invalidates the rotation. This is an evidence exit; entry,
    target, structural stop, costs, sizing and exchange-side bracket are not
    changed.

    Evidence exit does not create a new independent opportunity. The original
    parent auction remains unresolved until its frozen target, structural stop,
    day-trade horizon, funding boundary or evaluation boundary completes. No new
    detector is allowed to acquire the single execution slot during that causal
    remainder, preventing repeated entries into one liquidation/repricing chain.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.counter_context_choch_reference = float("nan")
        self.counter_context_target = float("nan")
        self.counter_context_entry = float("nan")
        self.counter_context_max_target_progress = 0.0
        self.counter_context_rotation_active = False
        self.counter_context_created_ts = 0

        self.counter_context_parent_lock_active = False
        self.counter_context_parent_lock_side = 0
        self.counter_context_parent_lock_target = float("nan")
        self.counter_context_parent_lock_stop = float("nan")
        self.counter_context_parent_lock_horizon_index = -1
        self.counter_context_parent_lock_scenario_id: str | None = None
        self.counter_context_parent_lock_last_checked_index = -1
        self.counter_context_parent_lock_resolved_index = -1

        self.diagnostics.update(
            {
                "positioning_reset_early_participation_pass": 0,
                "positioning_reset_early_participation_deferred": 0,
                "software_same_bar_counterflow_absorption_acceptances": 0,
                "software_same_bar_deferred_protection_arms": 0,
                "counter_context_rotations_opened": 0,
                "counter_context_rotation_failures": 0,
                "counter_context_parent_event_locks_armed": 0,
                "counter_context_parent_event_lock_bars": 0,
                "counter_context_parent_event_locks_resolved": 0,
            },
        )

    def _path_efficiency_30m(self) -> float:
        rows = list(self.bars)
        if len(rows) < 31:
            return float("nan")
        closes = [float(row["close"]) for row in rows[-31:]]
        return completed_path_efficiency(closes)

    def _counter_context_parent_event_blocks_detection(
        self,
        row: dict[str, float | int],
    ) -> bool:
        # Even after a lock resolves, the resolving completed bar cannot also be
        # reused as a new entry event.
        if self.counter_context_parent_lock_resolved_index == self.bar_index:
            return True
        if not self.counter_context_parent_lock_active:
            return False
        if self.counter_context_parent_lock_last_checked_index == self.bar_index:
            return True
        self.counter_context_parent_lock_last_checked_index = self.bar_index

        side = self.counter_context_parent_lock_side
        target = self.counter_context_parent_lock_target
        stop = self.counter_context_parent_lock_stop
        target_completed = (
            float(row["high"]) >= target
            if side > 0
            else float(row["low"]) <= target
        )
        stop_completed = (
            float(row["low"]) <= stop
            if side > 0
            else float(row["high"]) >= stop
        )
        horizon_completed = (
            self.counter_context_parent_lock_horizon_index >= 0
            and self.bar_index > self.counter_context_parent_lock_horizon_index
        )
        boundary_completed = (
            self._funding_blackout(int(row["ts"]))
            or not self._in_evaluation(int(row["ts"]))
        )
        if (
            target_completed
            or stop_completed
            or horizon_completed
            or boundary_completed
        ):
            self.counter_context_parent_lock_active = False
            self.counter_context_parent_lock_resolved_index = self.bar_index
            self.diagnostics["counter_context_parent_event_locks_resolved"] += 1
            return True

        self.diagnostics["counter_context_parent_event_lock_bars"] += 1
        return True

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        if self._counter_context_parent_event_blocks_detection(row):
            return
        previous_scenario = None if self.pending is None else self.pending.scenario_id
        super()._detect_sweep(row, previous_close)
        setup = self.pending
        if setup is None or setup.scenario_id == previous_scenario:
            return
        setup.details.update(
            {
                "sweep_premium_change_5m": self._feature("premium_change_5m"),
                "sweep_path_efficiency_30m": self._path_efficiency_30m(),
            },
        )

    def _early_sponsored_participation_allowed(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        flow_3m: float,
    ) -> bool:
        if not super()._early_sponsored_participation_allowed(
            setup,
            row,
            flow_3m,
        ):
            return False
        allowed = positioning_reset_supports_early_reversal(
            side=setup.side,
            sweep_premium_change_5m=float(
                setup.details.get("sweep_premium_change_5m", float("nan")),
            ),
            sweep_path_efficiency_30m=float(
                setup.details.get("sweep_path_efficiency_30m", float("nan")),
            ),
            choch_oi_change_15m=self._feature("oi_change_15m"),
        )
        key = (
            "positioning_reset_early_participation_pass"
            if allowed
            else "positioning_reset_early_participation_deferred"
        )
        self.diagnostics[key] += 1
        return allowed

    def _submit_price_capped_bracket(
        self,
        *,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
        entry_price: Any,
        stop_price: Any,
        target_price: Any,
        sizing_entry: float,
        planned_loss: float,
        target_source: str,
        target_r: float,
        branch: str,
        event_type: str,
        reason: str,
        expires_index: int,
        entry_tag: str,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        submitted = super()._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=sizing_entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=target_r,
            branch=branch,
            event_type=event_type,
            reason=reason,
            expires_index=expires_index,
            entry_tag=entry_tag,
            extra=extra,
        )
        if submitted:
            self.counter_context_choch_reference = float(armed.choch_close)
            self.counter_context_target = _as_float(target_price)
            self.counter_context_entry = float("nan")
            self.counter_context_max_target_progress = 0.0
            self.counter_context_rotation_active = False
            self.counter_context_created_ts = 0
        return submitted

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        if self.entry_cancel_requested:
            return
        actual_entry = self._actual_average_entry(event)
        self.counter_context_entry = (
            actual_entry
            if math.isfinite(actual_entry) and actual_entry > 0.0
            else self.entry_limit
        )
        context = getattr(self, "quarter_context", None)
        if context is None:
            return
        age = self.bar_index - int(context.created_index)
        active = (
            bool(context.accepted)
            and QH_CONTEXT_MIN_AGE_BARS <= age <= QH_CONTEXT_MAX_AGE_BARS
            and int(context.direction) == -int(self.entry_side)
        )
        if not active:
            return
        if not all(
            math.isfinite(value)
            for value in (
                self.counter_context_entry,
                self.counter_context_choch_reference,
                self.counter_context_target,
            )
        ):
            return
        target_distance = self.entry_side * (
            self.counter_context_target - self.counter_context_entry
        )
        if target_distance <= 0.0:
            return
        self.counter_context_rotation_active = True
        self.counter_context_created_ts = int(context.created_ts)
        self.diagnostics["counter_context_rotations_opened"] += 1

    def _counter_context_rotation_failed(
        self,
        row: dict[str, float | int],
    ) -> bool:
        if not self.counter_context_rotation_active:
            return False
        side = int(self.entry_side)
        target_distance = side * (
            self.counter_context_target - self.counter_context_entry
        )
        if side not in (-1, 1) or target_distance <= 0.0:
            return False

        favorable_price = (
            float(row["high"]) if side > 0 else float(row["low"])
        )
        progress = side * (
            favorable_price - self.counter_context_entry
        ) / target_distance
        self.counter_context_max_target_progress = max(
            self.counter_context_max_target_progress,
            progress,
        )
        if (
            self.counter_context_max_target_progress
            < COUNTER_CONTEXT_ROTATION_MIN_TARGET_PROGRESS
        ):
            return False

        choch_reaccepted = side * (
            float(row["close"]) - self.counter_context_choch_reference
        ) < 0.0
        directional_flow_60s = side * self._feature("flow_60s")
        directional_flow_3m = side * self._feature("flow_3m")
        efficiency = self._feature("efficiency_60s")
        return (
            choch_reaccepted
            and directional_flow_60s <= -TWO_TO_ONE_FLOW_IMBALANCE
            and directional_flow_3m <= -TWO_TO_ONE_FLOW_IMBALANCE
            and efficiency >= COUNTER_CONTEXT_REACCEPTANCE_EFFICIENCY_MIN
        )

    def _arm_counter_context_parent_event_lock(self) -> None:
        horizon = self.pending_scenario_horizon_index
        if horizon < 0:
            horizon = self.bar_index + self.config.max_hold_bars
        self.counter_context_parent_lock_active = True
        self.counter_context_parent_lock_side = int(self.entry_side)
        self.counter_context_parent_lock_target = float(self.counter_context_target)
        self.counter_context_parent_lock_stop = float(self.entry_stop)
        self.counter_context_parent_lock_horizon_index = int(horizon)
        self.counter_context_parent_lock_scenario_id = self.current_scenario_id
        self.counter_context_parent_lock_last_checked_index = -1
        self.counter_context_parent_lock_resolved_index = -1
        self.diagnostics["counter_context_parent_event_locks_armed"] += 1

    def _manage_open_position(
        self,
        row: dict[str, float | int],
    ) -> None:
        # The v18 cancellation-race state remains authoritative. The explicit
        # class call is used only for that armed branch; otherwise this method
        # resolves the v12 same-bar ambiguity and delegates to the same v9
        # position manager that v12 normally calls.
        if self.cancel_race_exit_armed:
            ExecutionConfirmedCancelStrategy._manage_open_position(self, row)
            return
        if self.exit_pending:
            return

        if self._counter_context_rotation_failed(row):
            self.diagnostics["counter_context_rotation_failures"] += 1
            self._arm_counter_context_parent_event_lock()
            self._flatten_at_software_invalidation(
                row,
                event_type="COUNTER_CONTEXT_ROTATION_FAILED",
                reason="CHOCH_REACCEPTED_AFTER_ONE_THIRD_TARGET_PATH_WITH_BROAD_ADVERSE_FLOW",
            )
            return

        side = self.entry_side
        if self.protection_armed:
            invalidated = (
                float(row["low"]) <= self.protection_stop
                if side > 0
                else float(row["high"]) >= self.protection_stop
            )
            if invalidated:
                self.diagnostics["software_protection_exits"] += 1
                self._flatten_at_software_invalidation(
                    row,
                    event_type="INTERMEDIATE_LIQUIDITY_REACCEPTED",
                    reason="CONSUMED_MONETIZABLE_POOL_REACCEPTED_AFTER_CONFIRMATION",
                )
                return

        if (
            not self.protection_armed
            and self.protection_pool_id is not None
            and math.isfinite(self.protection_milestone)
            and math.isfinite(self.protection_stop)
        ):
            touched = (
                float(row["high"]) >= self.protection_milestone
                if side > 0
                else float(row["low"]) <= self.protection_milestone
            )
            if touched:
                also_invalidated = (
                    float(row["low"]) <= self.protection_stop
                    if side > 0
                    else float(row["high"]) >= self.protection_stop
                )
                if also_invalidated:
                    directional_depth = side * self._feature("depth_imbalance_1")
                    directional_flow = side * self._feature("flow_60s")
                    closed_beyond = side * (
                        float(row["close"]) - self.protection_milestone
                    ) >= 0.0

                    # Intrabar order is unresolved. A completed close beyond the
                    # consumed pool, reversal-side displayed depth and aggressive
                    # counterflow which failed to move price back together form
                    # an observable acceptance state. Retire only the nearer
                    # software milestone; the original structural bracket is
                    # untouched.
                    if (
                        closed_beyond
                        and directional_depth >= 0.10
                        and directional_flow <= 0.0
                    ):
                        self.diagnostics[
                            "software_same_bar_counterflow_absorption_acceptances"
                        ] += 1
                        if self.current_scenario_id is not None:
                            self._transition(
                                self.current_scenario_id,
                                "INTERMEDIATE_LIQUIDITY_ACCEPTED",
                                int(row["ts"]),
                                int(row["ts"]),
                                "POSITION_OPEN",
                                "COUNTERFLOW_ABSORBED_BEYOND_CONSUMED_LIQUIDITY",
                                float(row["close"]),
                                {
                                    **self._protection_details(),
                                    "directional_flow_60s": directional_flow,
                                    "directional_depth_imbalance_1": directional_depth,
                                    "completed_close": float(row["close"]),
                                },
                            )
                        self._clear_protection_state()
                    elif closed_beyond and directional_depth >= 0.10:
                        # Price and depth accepted, but aligned aggressive flow
                        # can be climax. Start protection on the next completed
                        # bar instead of replaying the ambiguous current bar in
                        # an optimistic order.
                        self.protection_armed = True
                        self.protection_armed_index = self.bar_index
                        self.diagnostics["software_protection_arms"] += 1
                        self.diagnostics[
                            "software_same_bar_deferred_protection_arms"
                        ] += 1
                        if self.current_scenario_id is not None:
                            self._transition(
                                self.current_scenario_id,
                                "INTERMEDIATE_LIQUIDITY_SOFTWARE_DEFENDED",
                                int(row["ts"]),
                                int(row["ts"]),
                                "POSITION_OPEN",
                                "CLOSE_AND_DEPTH_ACCEPTED_BUT_ALIGNED_FLOW_RETAINS_PROTECTION",
                                self.protection_stop,
                                {
                                    **self._protection_details(),
                                    "directional_flow_60s": directional_flow,
                                    "directional_depth_imbalance_1": directional_depth,
                                },
                            )
                    else:
                        self.diagnostics["software_same_bar_failures"] += 1
                        self._flatten_at_software_invalidation(
                            row,
                            event_type="MILESTONE_FAILED_IN_SAME_COMPLETED_BAR",
                            reason="CONSUMED_LIQUIDITY_AND_REACCEPTANCE_ORDER_WAS_AMBIGUOUS",
                        )
                        return
                else:
                    self.protection_armed = True
                    self.protection_armed_index = self.bar_index
                    self.diagnostics["software_protection_arms"] += 1
                    if self.current_scenario_id is not None:
                        self._transition(
                            self.current_scenario_id,
                            "INTERMEDIATE_LIQUIDITY_SOFTWARE_DEFENDED",
                            int(row["ts"]),
                            int(row["ts"]),
                            "POSITION_OPEN",
                            "FIRST_MONETIZABLE_POOL_CONSUMED_WITH_PROTECTED_LEVEL_INTACT",
                            self.protection_stop,
                            self._protection_details(),
                        )

        ObservedEntryPathStrategy._manage_open_position(self, row)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.counter_context_choch_reference = float("nan")
        self.counter_context_target = float("nan")
        self.counter_context_entry = float("nan")
        self.counter_context_max_target_progress = 0.0
        self.counter_context_rotation_active = False
        self.counter_context_created_ts = 0


__all__ = ["PositioningResetReversalStrategy"]
