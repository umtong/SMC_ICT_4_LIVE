#!/usr/bin/env python3
"""Candidate 05 v39: positioning-reset gate for early sponsored CHoCH."""
from __future__ import annotations

import math

from positioning_reset_logic import completed_path_efficiency
from positioning_reset_logic import positioning_reset_supports_early_reversal
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_v18 import ExecutionConfirmedCancelStrategy
from strategy_v9 import ObservedEntryPathStrategy
from strategy_v26 import ScenarioValidEntryStrategy


class PositioningResetReversalStrategy(ScenarioValidEntryStrategy):
    """Route premature CHoCHs to observation instead of immediate participation.

    v39 changes one entry decision. An early coherent sponsored CHoCH may use the
    existing price-capped immediate order only when the sweep was preceded by a
    directional 30-minute path, the premium index was already normalizing in the
    proposed reversal direction, and 15-minute open interest is not materially
    expanding at CHoCH. Otherwise the unchanged v17 path waits for a causal
    retrace/breakaway response. Detector, target, stop, costs, 3% NAV sizing,
    Nautilus execution and pending-order lifecycle are inherited unchanged.

    The position manager also resolves one completed-bar ambiguity at an
    intermediate liquidity milestone. If the same completed bar touches both
    the milestone and its software-protected level, intrabar ordering is not
    guessed. A close beyond the consumed pool with reversal-side visible depth
    and failed aggressive counterflow is treated as acceptance, so the nearer
    software milestone is retired while the original exchange-side structural
    stop and live-liquidity target remain unchanged. If price and depth accept
    but aggressive flow is aligned with the position, protection begins only
    from the next completed bar. All other ambiguous bars retain the prior
    conservative flatten.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "positioning_reset_early_participation_pass": 0,
                "positioning_reset_early_participation_deferred": 0,
                "software_same_bar_counterflow_absorption_acceptances": 0,
                "software_same_bar_deferred_protection_arms": 0,
            },
        )

    def _path_efficiency_30m(self) -> float:
        rows = list(self.bars)
        if len(rows) < 31:
            return float("nan")
        closes = [float(row["close"]) for row in rows[-31:]]
        return completed_path_efficiency(closes)

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
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


__all__ = ["PositioningResetReversalStrategy"]
