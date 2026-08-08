"""Candidate 09 v48: quarter-hour algorithmic order flow and later POC retest.

The first ten seconds of the boundary minute define directional algorithmic
context.  The remaining completed minute must show an efficient price response
and directional traded-value POC.  The signal minute owns no immediate order;
its first later POC retest owns entry, its opposite extreme owns invalidation,
and the next unconsumed completed-auction pool owns the target.

The exact clock placebo moves the otherwise identical observation to minutes
07, 22, 37 and 52.  All signal, state, entry, stop, target, cost, risk and
NautilusTrader rules remain fixed.
"""
from __future__ import annotations

import math

from nautilus_trader.model.data import Bar

from strategy_base import PendingSetup
from strategy_v35 import Candidate16Config as _Candidate35Config
from strategy_v35 import Candidate16Strategy as _Candidate35Strategy


_MINUTE_NS = 60_000_000_000


class Candidate16Config(_Candidate35Config, frozen=True):
    candidate48_clock_offset_minutes: int = 0
    candidate48_retest_timeout_bars: int = 4


class Candidate16Strategy(_Candidate35Strategy):
    """Clock-phase context, completed response, and later execution."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        if config.candidate48_clock_offset_minutes not in (0, 7):
            raise ValueError("v48 allows only quarter-hour openings or the +7m placebo")
        self.diagnostics.update(
            {
                "candidate48_clock_minutes_observed": 0,
                "candidate48_first10_contexts": 0,
                "candidate48_price_responses": 0,
                "candidate48_poc_retests_armed": 0,
                "candidate48_poc_retest_entries": 0,
                "candidate48_poc_retest_timeouts": 0,
                "candidate48_signal_invalidations": 0,
                "candidate48_no_natural_objective": 0,
                "candidate48_clock_offset_minutes": (
                    config.candidate48_clock_offset_minutes
                ),
            }
        )

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        """Completed-auction pools are objectives only in this independent family."""
        del row, previous_close

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        self._candidate48_maybe_arm_signal()

    def _candidate48_clock_match(self, ts_event: int) -> bool:
        open_minute = ts_event // _MINUTE_NS - 1
        return int(open_minute % 15) == self.config.candidate48_clock_offset_minutes

    def _candidate48_maybe_arm_signal(self) -> None:
        if not self.bars:
            return
        row = self.bars[-1]
        ts_event = int(row["ts"])
        if not self._candidate48_clock_match(ts_event):
            return
        self.diagnostics["candidate48_clock_minutes_observed"] = int(
            self.diagnostics["candidate48_clock_minutes_observed"]
        ) + 1
        if not self.portfolio.is_flat(self.config.instrument_id):
            return
        if self.entry_pending or self.pending is not None:
            return
        if not self._in_evaluation(ts_event) or self._funding_blackout(ts_event):
            return
        if not self._features_ready(ts_event):
            return
        if self.bar_index - self.last_entry_index < self.config.cooldown_bars:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return

        imbalance = self._feature("first10_order_imbalance")
        first10_notional = self._feature("first10_notional")
        first10_vwap = self._feature("first10_vwap")
        first10_trades = self._feature("first10_trade_count")
        if not all(
            math.isfinite(value)
            for value in (imbalance, first10_notional, first10_vwap, first10_trades)
        ):
            return
        if (
            first10_notional <= 0.0
            or first10_trades <= 0.0
            or abs(imbalance) < self.config.acceptance_flow_min
        ):
            return
        direction = 1 if imbalance > 0.0 else -1
        self.diagnostics["candidate48_first10_contexts"] = int(
            self.diagnostics["candidate48_first10_contexts"]
        ) + 1

        open_price = float(row["open"])
        close = float(row["close"])
        span = max(float(row["high"]) - float(row["low"]), 1e-12)
        body_atr = direction * (close - open_price) / atr
        efficiency = self._feature("efficiency_60s")
        poc = self._feature("footprint_poc_price")
        close_location = (
            (close - float(row["low"])) / span
            if direction > 0
            else (float(row["high"]) - close) / span
        )
        response_pass = (
            math.isfinite(efficiency)
            and math.isfinite(poc)
            and body_atr >= self.config.acceptance_close_atr
            and efficiency >= self.config.acceptance_efficiency_min
            and direction * (poc - open_price) > 0.0
            and close_location >= self.config.acceptance_close_location
        )
        if not response_pass:
            return
        self.diagnostics["candidate48_price_responses"] = int(
            self.diagnostics["candidate48_price_responses"]
        ) + 1

        self.scenario_counter += 1
        scenario_id = f"qh48-{self.scenario_counter:07d}"
        stop_anchor = float(row["low"]) if direction > 0 else float(row["high"])
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="QUARTER_HOUR_POC_RETEST",
            side=direction,
            swept_kind="HIGH" if direction > 0 else "LOW",
            pool_id=f"clock-{ts_event}",
            pool_level=stop_anchor,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.candidate48_retest_timeout_bars,
            sweep_extreme=(
                float(row["high"]) if direction > 0 else float(row["low"])
            ),
            structure=poc,
            atr=atr,
            hold_count=0,
            retrace_armed=True,
            details={
                "candidate48_clock_offset_minutes": (
                    self.config.candidate48_clock_offset_minutes
                ),
                "candidate48_signal_ts_ns": ts_event,
                "candidate48_direction": direction,
                "candidate48_first10_order_imbalance": imbalance,
                "candidate48_first10_notional": first10_notional,
                "candidate48_first10_trade_count": first10_trades,
                "candidate48_first10_vwap": first10_vwap,
                "candidate48_signal_open": open_price,
                "candidate48_signal_high": float(row["high"]),
                "candidate48_signal_low": float(row["low"]),
                "candidate48_signal_close": close,
                "candidate48_signal_body_atr": body_atr,
                "candidate48_signal_efficiency": efficiency,
                "candidate48_signal_poc": poc,
                "candidate48_signal_close_location": close_location,
            },
        )
        self.diagnostics["candidate48_poc_retests_armed"] = int(
            self.diagnostics["candidate48_poc_retests_armed"]
        ) + 1
        self._transition(
            scenario_id,
            "QUARTER_HOUR_ALGORITHMIC_FLOW_CONFIRMED",
            ts_event - _MINUTE_NS,
            ts_event,
            "SIGNAL_POC_RETEST_ARMED",
            "FIRST10_FLOW_CONTEXT_AND_COMPLETED_MINUTE_PRICE_RESPONSE",
            poc,
            self.pending.details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None or setup.branch != "QUARTER_HOUR_POC_RETEST":
            return super()._process_pending(row)
        if self.bar_index > setup.expires_index:
            self.diagnostics["candidate48_poc_retest_timeouts"] = int(
                self.diagnostics["candidate48_poc_retest_timeouts"]
            ) + 1
            self._expire_pending(row, "QUARTER_HOUR_POC_RETEST_EXPIRED")
            return False
        if self.bar_index <= setup.created_index:
            return True

        side = setup.side
        close = float(row["close"])
        signal_low = float(setup.details["candidate48_signal_low"])
        signal_high = float(setup.details["candidate48_signal_high"])
        invalid = close < signal_low if side > 0 else close > signal_high
        if invalid:
            self.diagnostics["candidate48_signal_invalidations"] = int(
                self.diagnostics["candidate48_signal_invalidations"]
            ) + 1
            self._expire_pending(row, "QUARTER_HOUR_SIGNAL_EXTREME_INVALIDATED")
            return False

        poc = float(setup.details["candidate48_signal_poc"])
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return True
        touched = (
            float(row["low"])
            <= poc + self.config.acceptance_retrace_tolerance_atr * atr
            if side > 0
            else float(row["high"])
            >= poc - self.config.acceptance_retrace_tolerance_atr * atr
        )
        held = side * (close - poc) >= 0.0
        tail_flow = side * self._feature("flow_15s")
        span = max(float(row["high"]) - float(row["low"]), 1e-12)
        close_location = (
            (close - float(row["low"])) / span
            if side > 0
            else (float(row["high"]) - close) / span
        )
        if not (
            touched
            and held
            and tail_flow >= -self.config.acceptance_max_counterflow
            and close_location >= self.config.acceptance_retest_close_location
        ):
            return True

        setup.branch = "ACCEPTANCE"
        setup.details.update(
            {
                "candidate48_retest_ts_ns": int(row["ts"]),
                "candidate48_retest_tail_flow": tail_flow,
                "candidate48_retest_close_location": close_location,
            }
        )
        self.diagnostics["candidate48_poc_retest_entries"] = int(
            self.diagnostics["candidate48_poc_retest_entries"]
        ) + 1
        before = int(self.diagnostics.get("candidate16_natural_objective_rejections", 0))
        submitted = self._submit_entry(setup, row)
        after = int(self.diagnostics.get("candidate16_natural_objective_rejections", 0))
        if after > before:
            self.diagnostics["candidate48_no_natural_objective"] = int(
                self.diagnostics["candidate48_no_natural_objective"]
            ) + 1
        return submitted


__all__ = ["Candidate16Config", "Candidate16Strategy"]
