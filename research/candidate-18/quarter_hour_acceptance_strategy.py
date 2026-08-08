"""Candidate 18 v9: quarter-hour sponsored acceptance with defended retest.

This is an independent continuation family, not a relaxed version of the v8
basis-fade reversal.  It reuses the validated Candidate 18 v7 native TradeTick
execution and changes only the causal market-state policy.
"""
from __future__ import annotations

from typing import Any

from local_twin_trigger_strategy import Candidate18Config as _V7Config
from local_twin_trigger_strategy import Candidate18Strategy as _V7Strategy
from quarter_hour_router import QuarterHourContext
from quarter_hour_router import QuarterHourThresholds
from quarter_hour_router import detect_opening_acceptance
from quarter_hour_router import evaluate_defended_retest
from strategy_base import PendingSetup


class Candidate18Config(_V7Config, frozen=True):
    quarter_hour_pre_range_bars: int = 3
    quarter_hour_retest_bars: int = 3
    quarter_hour_opening_burst_min: float = 1.0
    quarter_hour_opening_flow_min: float = 0.14
    quarter_hour_full_flow_min: float = 0.10
    quarter_hour_efficiency_min: float = 0.45
    quarter_hour_displacement_atr_min: float = 0.05
    quarter_hour_opening_close_location_min: float = 0.62
    quarter_hour_retest_tolerance_atr: float = 0.15
    quarter_hour_retest_close_location_min: float = 0.56


class Candidate18Strategy(_V7Strategy):
    """Trade only a completed quarter-hour acceptance and later defended retest."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        if not 2 <= config.quarter_hour_pre_range_bars <= 12:
            raise ValueError("quarter_hour_pre_range_bars must be in [2, 12]")
        if not 1 <= config.quarter_hour_retest_bars <= 8:
            raise ValueError("quarter_hour_retest_bars must be in [1, 8]")
        self._quarter_hour_thresholds = QuarterHourThresholds(
            opening_burst_min=config.quarter_hour_opening_burst_min,
            opening_flow_min=config.quarter_hour_opening_flow_min,
            full_flow_min=config.quarter_hour_full_flow_min,
            efficiency_min=config.quarter_hour_efficiency_min,
            displacement_atr_min=config.quarter_hour_displacement_atr_min,
            opening_close_location_min=(
                config.quarter_hour_opening_close_location_min
            ),
            retest_tolerance_atr=config.quarter_hour_retest_tolerance_atr,
            retest_close_location_min=(
                config.quarter_hour_retest_close_location_min
            ),
        )
        self.diagnostics.update(
            {
                "candidate18_v9_quarter_hour_bars": 0,
                "candidate18_v9_opening_acceptances": 0,
                "candidate18_v9_long_acceptances": 0,
                "candidate18_v9_short_acceptances": 0,
                "candidate18_v9_retest_confirmations": 0,
                "candidate18_v9_retest_invalidations": 0,
                "candidate18_v9_retest_expiries": 0,
                "candidate18_v9_retest_wait_reasons": {},
                "candidate18_v9_routes": [],
            },
        )

    def _route_record(self, payload: dict[str, Any]) -> None:
        routes = list(self.diagnostics["candidate18_v9_routes"])
        routes.append(payload)
        self.diagnostics["candidate18_v9_routes"] = routes

    def _wait_reason(self, reason: str) -> None:
        counts = dict(self.diagnostics["candidate18_v9_retest_wait_reasons"])
        counts[reason] = int(counts.get(reason, 0)) + 1
        self.diagnostics["candidate18_v9_retest_wait_reasons"] = counts

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        del previous_close
        required = self.config.quarter_hour_pre_range_bars
        bars = list(self.bars)
        if len(bars) < required + 1:
            return
        current_ts = int(row["ts"])
        prior = bars[-(required + 1) : -1]
        context = detect_opening_acceptance(
            ts_event_ns=current_ts,
            prior_highs=[float(item["high"]) for item in prior],
            prior_lows=[float(item["low"]) for item in prior],
            opening_high=float(row["high"]),
            opening_low=float(row["low"]),
            opening_close=float(row["close"]),
            atr=self._atr(),
            opening_flow_10s=self._feature("flow_open_10s"),
            opening_notional_burst_10s=self._feature(
                "notional_open_10s_burst",
            ),
            full_flow_60s=self._feature("flow_60s"),
            return_60s_bps=self._feature("ret_60s_bps"),
            efficiency_60s=self._feature("efficiency_60s"),
            thresholds=self._quarter_hour_thresholds,
        )
        # The pure detector includes the clock condition.  Count only actual
        # quarter-hour observations which reached the strategy with fresh data.
        from quarter_hour_router import is_utc_quarter_hour

        if is_utc_quarter_hour(current_ts):
            self.diagnostics["candidate18_v9_quarter_hour_bars"] = int(
                self.diagnostics["candidate18_v9_quarter_hour_bars"],
            ) + 1
        if context is None:
            return

        self.scenario_counter += 1
        scenario_id = f"c18qh-{self.scenario_counter:07d}"
        side = context.side
        details = {
            "candidate18_version": "v9-quarter-hour-acceptance",
            "scenario_family": "QUARTER_HOUR_SPONSORED_ACCEPTANCE",
            "side": side,
            "boundary": context.boundary,
            "opposite_boundary": context.opposite_boundary,
            "opening_extreme": context.opening_extreme,
            "opening_time_ns": context.opening_time_ns,
            "opening_atr": context.atr,
            "opening_flow_10s": self._feature("flow_open_10s"),
            "opening_notional_burst_10s": self._feature(
                "notional_open_10s_burst",
            ),
            "full_flow_60s": self._feature("flow_60s"),
            "return_60s_bps": self._feature("ret_60s_bps"),
            "efficiency_60s": self._feature("efficiency_60s"),
            "pre_range_bars": required,
        }
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="QUARTER_HOUR_ACCEPTANCE",
            side=side,
            swept_kind=(
                "QUARTER_HOUR_PRE_RANGE_HIGH"
                if side > 0
                else "QUARTER_HOUR_PRE_RANGE_LOW"
            ),
            pool_id=f"quarter-hour-{context.opening_time_ns}",
            pool_level=context.boundary,
            created_index=self.bar_index,
            expires_index=(
                self.bar_index + self.config.quarter_hour_retest_bars
            ),
            sweep_extreme=context.opening_extreme,
            structure=context.boundary,
            atr=context.atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        self.diagnostics["candidate18_v9_opening_acceptances"] = int(
            self.diagnostics["candidate18_v9_opening_acceptances"],
        ) + 1
        direction_key = (
            "candidate18_v9_long_acceptances"
            if side > 0
            else "candidate18_v9_short_acceptances"
        )
        self.diagnostics[direction_key] = int(self.diagnostics[direction_key]) + 1
        self._route_record(
            {
                "scenario_id": scenario_id,
                "decision": "AWAIT_STRICTLY_LATER_DEFENDED_RETEST",
                "side": side,
                "event_time_ns": current_ts,
                "boundary": context.boundary,
            },
        )
        self._transition(
            scenario_id,
            "QUARTER_HOUR_ACCEPTANCE_DETECTED",
            current_ts,
            current_ts,
            "AWAIT_RETEST",
            "OPENING_10S_SPONSORSHIP_AND_COMPLETED_1M_ACCEPTANCE",
            float(row["close"]),
            details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch != "QUARTER_HOUR_ACCEPTANCE":
            return super()._process_pending(row)
        if self.bar_index <= setup.created_index:
            return True
        if self.bar_index > setup.expires_index:
            self.diagnostics["candidate18_v9_retest_expiries"] = int(
                self.diagnostics["candidate18_v9_retest_expiries"],
            ) + 1
            self._route_record(
                {
                    "scenario_id": setup.scenario_id,
                    "decision": "NO_TRADE_RETEST_EXPIRED",
                    "event_time_ns": int(row["ts"]),
                },
            )
            self._expire_pending(row, "QUARTER_HOUR_RETEST_WINDOW_EXPIRED")
            return True

        context = QuarterHourContext(
            side=setup.side,
            boundary=float(setup.details["boundary"]),
            opposite_boundary=float(setup.details["opposite_boundary"]),
            opening_extreme=float(setup.details["opening_extreme"]),
            atr=float(setup.details["opening_atr"]),
            opening_time_ns=int(setup.details["opening_time_ns"]),
        )
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
            self.diagnostics["candidate18_v9_retest_invalidations"] = int(
                self.diagnostics["candidate18_v9_retest_invalidations"],
            ) + 1
            self._route_record(
                {
                    "scenario_id": setup.scenario_id,
                    "decision": "NO_TRADE_ACCEPTED_BOUNDARY_LOST",
                    "reason": decision.reason,
                    "event_time_ns": int(row["ts"]),
                },
            )
            self._expire_pending(row, decision.reason)
            return True
        if decision.state != "CONFIRMED":
            self._wait_reason(decision.reason)
            return True

        setup.details.update(
            {
                "retest_time_ns": int(row["ts"]),
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
        self.diagnostics["candidate18_v9_retest_confirmations"] = int(
            self.diagnostics["candidate18_v9_retest_confirmations"],
        ) + 1
        self._route_record(
            {
                "scenario_id": setup.scenario_id,
                "decision": "ENTER_DEFENDED_QUARTER_HOUR_ACCEPTANCE",
                "reason": decision.reason,
                "side": setup.side,
                "event_time_ns": int(row["ts"]),
            },
        )
        self._transition(
            setup.scenario_id,
            "DEFENDED_RETEST_CONFIRMED",
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
