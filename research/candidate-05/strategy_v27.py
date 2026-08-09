#!/usr/bin/env python3
"""Candidate 05 v27: delayed absorption after an unresolved liquidity access."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from delayed_rejection_logic import DELAYED_CHOCH_BARS
from delayed_rejection_logic import DELAYED_RESPONSE_BARS
from delayed_rejection_logic import delayed_access_is_material
from delayed_rejection_logic import delayed_rejection_response
from logic import confirmation_passes
from retrace_logic import pending_limit_invalidated
from retrace_logic import structural_stop
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_v26 import ScenarioValidEntryStrategy


@dataclass(slots=True)
class DelayedRejectionWatch:
    scenario_id: str
    detector_scenario_id: str
    side: int
    pool_kind: str
    source_pool_id: str
    boundary: float
    sweep_extreme: float
    structure: float
    atr: float
    created_index: int
    response_expires_index: int
    response_index: int
    choch_expires_index: int
    phase: str
    details: dict[str, Any]


class DelayedRejectionStrategy(ScenarioValidEntryStrategy):
    """Let unresolved pool access become a rejection only after later evidence.

    Candidate 05 previously made a binary decision on the access bar. A pool
    violation whose same-bar price, flow and book response disagreed was closed
    permanently. v27 keeps that detector outcome but adds a separate observer:

    * penetration and activity must already meet the unchanged sweep minima;
    * within the next completed three-minute flow state, price must reclaim the
      consumed pool while final-15-second aggressor flow turns toward reversal
      and current aggregate depth supports reversal;
    * after that delayed absorption, the unchanged four-bar CHoCH/displacement
      predicate must break the access bar's opposite extreme;
    * the resulting setup joins the existing v26 sponsored-CHoCH, confirmed
      retest, frozen live-liquidity target, scenario-valid order lifecycle,
      costs, slippage and 3% current-NAV sizing paths.

    Observers may coexist. The inherited one-executable-intent/one-position rule
    remains unchanged, so observation is parallel but execution is singular.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.delayed_rejection_counter = 0
        self.delayed_rejection_watches: dict[str, DelayedRejectionWatch] = {}
        self.diagnostics.update(
            {
                "delayed_rejection_material_accesses": 0,
                "delayed_rejection_watches": 0,
                "delayed_rejection_parallel_observers_max": 0,
                "delayed_rejection_responses": 0,
                "delayed_rejection_response_expiries": 0,
                "delayed_rejection_stop_invalidations": 0,
                "delayed_rejection_choch_confirmations": 0,
                "delayed_rejection_choch_expiries": 0,
                "delayed_rejection_slot_conflicts": 0,
                "delayed_rejection_submissions": 0,
                "delayed_rejection_closed": 0,
            },
        )

    def _transition(
        self,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        next_state: str,
        reason_code: str,
        reference_price: float,
        details: dict[str, Any],
    ) -> None:
        super()._transition(
            scenario_id,
            event_type,
            event_time_ns,
            observed_time_ns,
            next_state,
            reason_code,
            reference_price,
            details,
        )
        if event_type == "SWEEP_UNRESOLVED":
            self._observe_unresolved_access(
                detector_scenario_id=scenario_id,
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                reference_price=reference_price,
                details=details,
            )

    def _observe_unresolved_access(
        self,
        *,
        detector_scenario_id: str,
        event_time_ns: int,
        observed_time_ns: int,
        reference_price: float,
        details: dict[str, Any],
    ) -> None:
        if not delayed_access_is_material(
            penetration_atr=float(details.get("penetration_atr", float("nan"))),
            notional_burst=float(details.get("notional_burst", float("nan"))),
            minimum_penetration_atr=self.config.sweep_min_penetration_atr,
            minimum_notional_burst=self.config.sweep_min_notional_burst,
        ):
            return
        if not self.bars:
            return
        pool_kind = str(details.get("pool_kind", ""))
        if pool_kind not in {"HIGH", "LOW"}:
            return
        boundary = float(details.get("pool_level", float("nan")))
        atr = self._atr()
        if not math.isfinite(boundary) or not math.isfinite(atr) or atr <= 0.0:
            return

        row = self.bars[-1]
        side = -1 if pool_kind == "HIGH" else 1
        sweep_extreme = float(row["high"]) if pool_kind == "HIGH" else float(row["low"])
        structure = float(row["low"]) if side < 0 else float(row["high"])
        self.delayed_rejection_counter += 1
        scenario_id = f"dlr-{self.delayed_rejection_counter:07d}"
        watch_details = {
            **details,
            "detector_scenario_id": detector_scenario_id,
            "side": side,
            "access_open": float(row["open"]),
            "access_high": float(row["high"]),
            "access_low": float(row["low"]),
            "access_close": float(row["close"]),
            "sweep_extreme": sweep_extreme,
            "choch_structure": structure,
            "delayed_response_bars": DELAYED_RESPONSE_BARS,
            "delayed_choch_bars": DELAYED_CHOCH_BARS,
            "pattern_action": "OBSERVE_UNTIL_DELAYED_RECLAIM_THEN_CHOCH",
        }
        watch = DelayedRejectionWatch(
            scenario_id=scenario_id,
            detector_scenario_id=detector_scenario_id,
            side=side,
            pool_kind=pool_kind,
            source_pool_id=str(details.get("pool_id", "")),
            boundary=boundary,
            sweep_extreme=sweep_extreme,
            structure=structure,
            atr=atr,
            created_index=self.bar_index,
            response_expires_index=self.bar_index + DELAYED_RESPONSE_BARS,
            response_index=-1,
            choch_expires_index=-1,
            phase="WAIT_DELAYED_RESPONSE",
            details=watch_details,
        )
        self.delayed_rejection_watches[scenario_id] = watch
        self.diagnostics["delayed_rejection_material_accesses"] += 1
        self.diagnostics["delayed_rejection_watches"] += 1
        self.diagnostics["delayed_rejection_parallel_observers_max"] = max(
            int(self.diagnostics["delayed_rejection_parallel_observers_max"]),
            len(self.delayed_rejection_watches),
        )
        super()._transition(
            scenario_id,
            "DELAYED_REJECTION_WATCH_ARMED",
            event_time_ns,
            observed_time_ns,
            "WAIT_DELAYED_RESPONSE",
            "MATERIAL_LIQUIDITY_ACCESS_AWAITS_COMPLETED_RESPONSE",
            reference_price,
            watch_details,
        )

    def on_bar(self, bar: Any) -> None:
        super().on_bar(bar)
        if not self.bars or not self.delayed_rejection_watches:
            return
        row = self.bars[-1]
        ts = int(row["ts"])
        if not self._in_evaluation(ts):
            self._close_all_delayed_watches(
                row,
                "EVALUATION_ENDED_DURING_DELAYED_RESPONSE",
            )
            return
        if self._funding_blackout(ts):
            self._close_all_delayed_watches(
                row,
                "FUNDING_BLACKOUT_DURING_DELAYED_RESPONSE",
            )
            return
        if not self._features_ready(ts):
            return
        for scenario_id in list(self.delayed_rejection_watches):
            if scenario_id in self.delayed_rejection_watches:
                self._advance_delayed_rejection(scenario_id, row)

    def _advance_delayed_rejection(
        self,
        scenario_id: str,
        row: dict[str, float | int],
    ) -> None:
        watch = self.delayed_rejection_watches.get(scenario_id)
        if watch is None or self.bar_index <= watch.created_index:
            return
        stop = structural_stop(
            watch.sweep_extreme,
            watch.side,
            watch.atr,
            self.config.stop_buffer_atr,
        )
        if pending_limit_invalidated(
            side=watch.side,
            stop=stop,
            high=float(row["high"]),
            low=float(row["low"]),
        ):
            self.diagnostics["delayed_rejection_stop_invalidations"] += 1
            self._close_delayed_watch(
                scenario_id,
                row,
                "SWEEP_EXTREME_INVALIDATED_BEFORE_DELAYED_CHOCH",
            )
            return

        if watch.phase == "WAIT_DELAYED_RESPONSE":
            if delayed_rejection_response(
                side=watch.side,
                pool_kind=watch.pool_kind,
                boundary=watch.boundary,
                close=float(row["close"]),
                flow_15s=self._feature("flow_15s"),
                flow_60s=self._feature("flow_60s"),
                depth_imbalance=self._feature("depth_imbalance_1"),
            ):
                watch.phase = "WAIT_DELAYED_CHOCH"
                watch.response_index = self.bar_index
                watch.choch_expires_index = self.bar_index + DELAYED_CHOCH_BARS
                watch.details.update(
                    {
                        "delayed_response_index": self.bar_index,
                        "delayed_response_close": float(row["close"]),
                        "delayed_response_flow_15s": self._feature("flow_15s"),
                        "delayed_response_flow_60s": self._feature("flow_60s"),
                        "delayed_response_flow_3m": self._feature("flow_3m"),
                        "delayed_response_depth_imbalance_1": self._feature(
                            "depth_imbalance_1",
                        ),
                    },
                )
                self.diagnostics["delayed_rejection_responses"] += 1
                self._transition(
                    scenario_id,
                    "DELAYED_REJECTION_RESPONSE_CONFIRMED",
                    int(row["ts"]),
                    int(row["ts"]),
                    "WAIT_DELAYED_CHOCH",
                    "POOL_RECLAIMED_WITH_TAIL_FLOW_TURN_AND_CURRENT_DEPTH",
                    float(row["close"]),
                    dict(watch.details),
                )
                self._try_delayed_choch(watch, row)
                return
            if self.bar_index >= watch.response_expires_index:
                self.diagnostics["delayed_rejection_response_expiries"] += 1
                self._close_delayed_watch(
                    scenario_id,
                    row,
                    "THREE_MINUTE_DELAYED_RESPONSE_WINDOW_EXPIRED",
                )
            return

        if self.bar_index > watch.choch_expires_index:
            self.diagnostics["delayed_rejection_choch_expiries"] += 1
            self._close_delayed_watch(
                scenario_id,
                row,
                "DELAYED_REJECTION_CHOCH_WINDOW_EXPIRED",
            )
            return
        self._try_delayed_choch(watch, row)

    def _try_delayed_choch(
        self,
        watch: DelayedRejectionWatch,
        row: dict[str, float | int],
    ) -> None:
        atr = self._atr()
        if not confirmation_passes(
            side=watch.side,
            open_price=float(row["open"]),
            close_price=float(row["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            structure=watch.structure,
            atr=atr,
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            min_body_atr=self.config.rejection_confirm_body_atr,
            min_flow=self.config.rejection_confirm_flow_min,
            min_efficiency=self.config.rejection_confirm_efficiency_min,
            min_close_location=self.config.rejection_confirm_close_location,
        ):
            return

        scenario_id = watch.scenario_id
        self.diagnostics["delayed_rejection_choch_confirmations"] += 1
        self._transition(
            scenario_id,
            "DELAYED_REJECTION_CHOCH_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PATH_ELIGIBLE",
            "DELAYED_ABSORPTION_THEN_OPPOSITE_DISPLACEMENT",
            float(row["close"]),
            {
                **watch.details,
                "confirmation_close": float(row["close"]),
                "confirmation_delay_from_access": self.bar_index - watch.created_index,
                "confirmation_delay_from_response": self.bar_index - watch.response_index,
            },
        )
        if not self._delayed_entry_slot_idle():
            self.diagnostics["delayed_rejection_slot_conflicts"] += 1
            self._close_delayed_watch(
                scenario_id,
                row,
                "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_DELAYED_CHOCH",
            )
            return

        details = {
            **watch.details,
            "delayed_rejection": True,
            "confirmation_close": float(row["close"]),
            "confirmation_delay_bars": self.bar_index - watch.created_index,
        }
        setup = PendingSetup(
            scenario_id=scenario_id,
            branch="DELAYED_LIQUIDITY_REJECTION",
            side=watch.side,
            swept_kind=watch.pool_kind,
            pool_id=watch.source_pool_id,
            pool_level=watch.boundary,
            created_index=watch.created_index,
            expires_index=self.bar_index,
            sweep_extreme=watch.sweep_extreme,
            structure=watch.structure,
            atr=watch.atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.delayed_rejection_watches.pop(scenario_id, None)
        self.pending = setup
        handled = self._submit_entry(setup, row)
        if handled:
            self.diagnostics["delayed_rejection_submissions"] += 1
        elif self.pending is setup:
            self._expire_pending(
                row,
                "DELAYED_CHOCH_COULD_NOT_FORM_EXECUTABLE_PATH",
            )

    def _delayed_entry_slot_idle(self) -> bool:
        return (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not self.exit_pending
            and self.pending is None
            and self.armed_entry_path is None
        )

    def _close_delayed_watch(
        self,
        scenario_id: str,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        watch = self.delayed_rejection_watches.pop(scenario_id, None)
        if watch is None:
            return
        self.diagnostics["delayed_rejection_closed"] += 1
        self._transition(
            scenario_id,
            "DELAYED_REJECTION_WATCH_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            dict(watch.details),
        )

    def _close_all_delayed_watches(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        for scenario_id in list(self.delayed_rejection_watches):
            self._close_delayed_watch(scenario_id, row, reason)

    def on_stop(self) -> None:
        if self.bars:
            self._close_all_delayed_watches(
                self.bars[-1],
                "BACKTEST_ENDED_WITH_DELAYED_REJECTION_WATCH_OPEN",
            )
        super().on_stop()


__all__ = ["DelayedRejectionStrategy"]
