"""Candidate 18 v10: defended quarter-hour acceptance plus second-leg relaunch.

V9 entered immediately after a defended retest. Eight completed development
trades all stopped, and the structural stop was only a few dollars from entry,
so taker costs dominated the realized loss. V10 keeps the causal opening and
retest observations but requires a strictly later renewed auction leg. The
opposite pre-event range boundary becomes the stop anchor only after relaunch.
"""
from __future__ import annotations

from typing import Any

from quarter_hour_acceptance_strategy import Candidate18Config as _V9Config
from quarter_hour_acceptance_strategy import Candidate18Strategy as _V9Strategy
from quarter_hour_relaunch_logic import evaluate_second_leg_relaunch
from quarter_hour_router import QuarterHourContext
from quarter_hour_router import evaluate_defended_retest


class Candidate18Config(_V9Config, frozen=True):
    quarter_hour_relaunch_bars: int = 6
    quarter_hour_relaunch_buffer_atr: float = 0.02
    quarter_hour_relaunch_tail_flow_min: float = 0.10
    quarter_hour_relaunch_full_flow_min: float = 0.10
    quarter_hour_relaunch_efficiency_min: float = 0.20
    quarter_hour_relaunch_queue_min: float = 0.0


class Candidate18Strategy(_V9Strategy):
    """Enter only after a later auction leg departs from the defended retest."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        if not 1 <= config.quarter_hour_relaunch_bars <= 12:
            raise ValueError("quarter_hour_relaunch_bars must be in [1, 12]")
        if not 0.0 <= config.quarter_hour_relaunch_buffer_atr <= 0.50:
            raise ValueError(
                "quarter_hour_relaunch_buffer_atr must be in [0, 0.50]",
            )
        for name in (
            "quarter_hour_relaunch_tail_flow_min",
            "quarter_hour_relaunch_full_flow_min",
            "quarter_hour_relaunch_efficiency_min",
            "quarter_hour_relaunch_queue_min",
        ):
            value = float(getattr(config, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        self.diagnostics.update(
            {
                "candidate18_v10_contexts_armed": 0,
                "candidate18_v10_retests_armed": 0,
                "candidate18_v10_relaunch_confirmations": 0,
                "candidate18_v10_preentry_invalidations": 0,
                "candidate18_v10_retest_expiries": 0,
                "candidate18_v10_relaunch_expiries": 0,
                "candidate18_v10_wait_reasons": {},
                "candidate18_v10_routes": [],
            },
        )

    def _v10_route(self, payload: dict[str, Any]) -> None:
        routes = list(self.diagnostics["candidate18_v10_routes"])
        routes.append(payload)
        self.diagnostics["candidate18_v10_routes"] = routes

    def _v10_wait(self, reason: str) -> None:
        counts = dict(self.diagnostics["candidate18_v10_wait_reasons"])
        counts[reason] = int(counts.get(reason, 0)) + 1
        self.diagnostics["candidate18_v10_wait_reasons"] = counts

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        super()._detect_sweep(row, previous_close)
        setup = self.pending
        if setup is None or setup.branch != "QUARTER_HOUR_ACCEPTANCE":
            return
        setup.branch = "QUARTER_HOUR_RELAUNCH"
        setup.details.update(
            {
                "candidate18_version": "v10-quarter-hour-relaunch",
                "scenario_family": "QUARTER_HOUR_SECOND_LEG_RELAUNCH",
                "v10_phase": "AWAIT_RETEST",
            },
        )
        self.diagnostics["candidate18_v10_contexts_armed"] = int(
            self.diagnostics["candidate18_v10_contexts_armed"],
        ) + 1
        self._v10_route(
            {
                "scenario_id": setup.scenario_id,
                "decision": "AWAIT_DEFENDED_RETEST_THEN_SECOND_LEG",
                "side": setup.side,
                "event_time_ns": int(row["ts"]),
                "accepted_boundary": setup.details["boundary"],
                "opposite_boundary": setup.details["opposite_boundary"],
            },
        )
        self._transition(
            setup.scenario_id,
            "QUARTER_HOUR_RELAUNCH_CONTEXT_ARMED",
            int(row["ts"]),
            int(row["ts"]),
            "AWAIT_RETEST",
            "OPENING_ACCEPTANCE_REQUIRES_RETEST_AND_NEW_AUCTION_LEG",
            float(row["close"]),
            setup.details,
        )

    @staticmethod
    def _context(setup: Any) -> QuarterHourContext:
        return QuarterHourContext(
            side=setup.side,
            boundary=float(setup.details["boundary"]),
            opposite_boundary=float(setup.details["opposite_boundary"]),
            opening_extreme=float(setup.details["opening_extreme"]),
            atr=float(setup.details["opening_atr"]),
            opening_time_ns=int(setup.details["opening_time_ns"]),
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch != "QUARTER_HOUR_RELAUNCH":
            return super()._process_pending(row)

        phase = str(setup.details.get("v10_phase", "AWAIT_RETEST"))
        if phase == "AWAIT_RETEST":
            return self._process_retest(setup, row)
        if phase == "AWAIT_RELAUNCH":
            return self._process_relaunch(setup, row)
        self._expire_pending(row, "UNKNOWN_V10_PHASE")
        return True

    def _process_retest(
        self,
        setup: Any,
        row: dict[str, float | int],
    ) -> bool:
        if self.bar_index <= setup.created_index:
            return True
        if self.bar_index > setup.expires_index:
            self.diagnostics["candidate18_v10_retest_expiries"] = int(
                self.diagnostics["candidate18_v10_retest_expiries"],
            ) + 1
            self._v10_route(
                {
                    "scenario_id": setup.scenario_id,
                    "decision": "NO_TRADE_RETEST_EXPIRED",
                    "event_time_ns": int(row["ts"]),
                },
            )
            self._expire_pending(row, "QUARTER_HOUR_RETEST_WINDOW_EXPIRED")
            return True

        context = self._context(setup)
        decision = evaluate_defended_retest(
            context=context,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            tail_flow_15s=self._feature("flow_15s"),
            depth_imbalance_1=self._feature("depth_imbalance_1"),
            thresholds=self._quarter_hour_thresholds,
        )
        if decision.state == "INVALIDATED":
            self.diagnostics["candidate18_v10_preentry_invalidations"] = int(
                self.diagnostics["candidate18_v10_preentry_invalidations"],
            ) + 1
            self._v10_route(
                {
                    "scenario_id": setup.scenario_id,
                    "decision": "NO_TRADE_ACCEPTED_BOUNDARY_LOST_BEFORE_RETEST",
                    "reason": decision.reason,
                    "event_time_ns": int(row["ts"]),
                },
            )
            self._expire_pending(row, decision.reason)
            return True
        if decision.state != "CONFIRMED":
            self._v10_wait(decision.reason)
            return True

        setup.details.update(
            {
                "v10_phase": "AWAIT_RELAUNCH",
                "retest_time_ns": int(row["ts"]),
                "retest_bar_index": self.bar_index,
                "retest_tail_flow_15s": self._feature("flow_15s"),
                "retest_depth_imbalance_1": self._feature(
                    "depth_imbalance_1",
                ),
                "retest_high": float(row["high"]),
                "retest_low": float(row["low"]),
                "retest_close": float(row["close"]),
                "retest_delay_bars": self.bar_index - setup.created_index,
            },
        )
        setup.expires_index = (
            self.bar_index + self.config.quarter_hour_relaunch_bars
        )
        self.diagnostics["candidate18_v10_retests_armed"] = int(
            self.diagnostics["candidate18_v10_retests_armed"],
        ) + 1
        self._v10_route(
            {
                "scenario_id": setup.scenario_id,
                "decision": "DEFENDED_RETEST_ARMED_FOR_SECOND_LEG",
                "reason": decision.reason,
                "side": setup.side,
                "event_time_ns": int(row["ts"]),
                "expires_index": setup.expires_index,
            },
        )
        self._transition(
            setup.scenario_id,
            "DEFENDED_RETEST_ARMED_FOR_RELAUNCH",
            int(row["ts"]),
            int(row["ts"]),
            "AWAIT_RELAUNCH",
            "RETEST_IS_OBSERVATION_NOT_ENTRY_TRIGGER",
            float(row["close"]),
            setup.details,
        )
        return True

    def _process_relaunch(
        self,
        setup: Any,
        row: dict[str, float | int],
    ) -> bool:
        retest_bar_index = int(setup.details["retest_bar_index"])
        if self.bar_index <= retest_bar_index:
            return True
        if self.bar_index > setup.expires_index:
            self.diagnostics["candidate18_v10_relaunch_expiries"] = int(
                self.diagnostics["candidate18_v10_relaunch_expiries"],
            ) + 1
            self._v10_route(
                {
                    "scenario_id": setup.scenario_id,
                    "decision": "NO_TRADE_SECOND_LEG_EXPIRED",
                    "event_time_ns": int(row["ts"]),
                },
            )
            self._expire_pending(row, "SECOND_LEG_RELAUNCH_WINDOW_EXPIRED")
            return True

        context = self._context(setup)
        decision = evaluate_second_leg_relaunch(
            side=setup.side,
            atr=context.atr,
            accepted_boundary=context.boundary,
            opening_extreme=context.opening_extreme,
            retest_high=float(setup.details["retest_high"]),
            retest_low=float(setup.details["retest_low"]),
            close=float(row["close"]),
            tail_flow_15s=self._feature("flow_15s"),
            full_flow_60s=self._feature("flow_60s"),
            return_60s_bps=self._feature("ret_60s_bps"),
            efficiency_60s=self._feature("efficiency_60s"),
            depth_imbalance_1=self._feature("depth_imbalance_1"),
            buffer_atr=self.config.quarter_hour_relaunch_buffer_atr,
            tail_flow_min=self.config.quarter_hour_relaunch_tail_flow_min,
            full_flow_min=self.config.quarter_hour_relaunch_full_flow_min,
            efficiency_min=self.config.quarter_hour_relaunch_efficiency_min,
            queue_min=self.config.quarter_hour_relaunch_queue_min,
        )
        if decision.state == "INVALIDATED":
            self.diagnostics["candidate18_v10_preentry_invalidations"] = int(
                self.diagnostics["candidate18_v10_preentry_invalidations"],
            ) + 1
            self._v10_route(
                {
                    "scenario_id": setup.scenario_id,
                    "decision": "NO_TRADE_ACCEPTANCE_LOST_BEFORE_SECOND_LEG",
                    "reason": decision.reason,
                    "event_time_ns": int(row["ts"]),
                    "launch_level": decision.launch_level,
                },
            )
            self._expire_pending(row, decision.reason)
            return True
        if decision.state != "CONFIRMED":
            self._v10_wait(decision.reason)
            return True

        setup.details.update(
            {
                "relaunch_time_ns": int(row["ts"]),
                "relaunch_bar_index": self.bar_index,
                "relaunch_level": decision.launch_level,
                "relaunch_close": float(row["close"]),
                "relaunch_tail_flow_15s": self._feature("flow_15s"),
                "relaunch_full_flow_60s": self._feature("flow_60s"),
                "relaunch_return_60s_bps": self._feature("ret_60s_bps"),
                "relaunch_efficiency_60s": self._feature("efficiency_60s"),
                "relaunch_depth_imbalance_1": self._feature(
                    "depth_imbalance_1",
                ),
                "stop_anchor": "OPPOSITE_PRE_EVENT_RANGE_BOUNDARY",
            },
        )
        # The inherited bounded GTD execution uses pool_level as the
        # acceptance stop anchor. Repoint it to the opposite pre-event range
        # boundary so the stop belongs to the same second auction leg.
        setup.pool_level = context.opposite_boundary
        self.diagnostics["candidate18_v10_relaunch_confirmations"] = int(
            self.diagnostics["candidate18_v10_relaunch_confirmations"],
        ) + 1
        self._v10_route(
            {
                "scenario_id": setup.scenario_id,
                "decision": "ENTER_STRICTLY_LATER_SECOND_LEG_RELAUNCH",
                "reason": decision.reason,
                "side": setup.side,
                "event_time_ns": int(row["ts"]),
                "launch_level": decision.launch_level,
                "stop_anchor": context.opposite_boundary,
            },
        )
        self._transition(
            setup.scenario_id,
            "SECOND_LEG_RELAUNCH_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_READY",
            decision.reason,
            float(row["close"]),
            setup.details,
        )
        self._submit_entry(setup, row)
        return True


__all__ = ["Candidate18Config", "Candidate18Strategy"]
