"""Candidate 09 v49: direct medium-horizon quarter-hour order-flow trade.

The first ten seconds of a quarter-hour opening supply directional algorithmic
flow context.  The completed minute must not reverse through the first-ten-
second VWAP.  That completed bar starts a four-hour auction leg and owns entry,
its opposite extreme owns invalidation, and the next unconsumed completed-
auction pool owns the target.  Funding/evaluation boundaries remain mandatory.

The exact placebo shifts the otherwise identical event to minutes 07, 22, 37
and 52.  No POC-retest requirement is used, so this candidate directly tests the
medium-horizon mechanism rather than V48's selective execution interpretation.
"""
from __future__ import annotations

import math

from nautilus_trader.model.data import Bar

from strategy_base import PendingSetup
from strategy_v35 import Candidate16Config as _Candidate35Config
from strategy_v35 import Candidate16Strategy as _Candidate35Strategy


_MINUTE_NS = 60_000_000_000


class Candidate16Config(_Candidate35Config, frozen=True):
    candidate49_clock_offset_minutes: int = 0


class Candidate16Strategy(_Candidate35Strategy):
    """Boundary flow context, completed retention, and direct horizon entry."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        if config.candidate49_clock_offset_minutes not in (0, 7):
            raise ValueError("v49 allows only true quarter-hours or the +7m placebo")
        self.diagnostics.update(
            {
                "candidate49_clock_minutes_observed": 0,
                "candidate49_first10_contexts": 0,
                "candidate49_vwap_retention_passes": 0,
                "candidate49_vwap_retention_blocks": 0,
                "candidate49_entries_attempted": 0,
                "candidate49_entries_submitted": 0,
                "candidate49_no_natural_objective": 0,
                "candidate49_clock_offset_minutes": (
                    config.candidate49_clock_offset_minutes
                ),
            }
        )

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        """Completed-auction pools remain targets, not entry contexts."""
        del row, previous_close

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        self._candidate49_maybe_submit()

    def _candidate49_clock_match(self, ts_event: int) -> bool:
        open_minute = ts_event // _MINUTE_NS - 1
        return int(open_minute % 15) == self.config.candidate49_clock_offset_minutes

    def _candidate49_maybe_submit(self) -> None:
        if not self.bars:
            return
        row = self.bars[-1]
        ts_event = int(row["ts"])
        if not self._candidate49_clock_match(ts_event):
            return
        self.diagnostics["candidate49_clock_minutes_observed"] = int(
            self.diagnostics["candidate49_clock_minutes_observed"]
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
        self.diagnostics["candidate49_first10_contexts"] = int(
            self.diagnostics["candidate49_first10_contexts"]
        ) + 1

        close = float(row["close"])
        retained = direction * (close - first10_vwap) >= 0.0
        if not retained:
            self.diagnostics["candidate49_vwap_retention_blocks"] = int(
                self.diagnostics["candidate49_vwap_retention_blocks"]
            ) + 1
            return
        self.diagnostics["candidate49_vwap_retention_passes"] = int(
            self.diagnostics["candidate49_vwap_retention_passes"]
        ) + 1

        signal_low = float(row["low"])
        signal_high = float(row["high"])
        stop_anchor = signal_low if direction > 0 else signal_high
        self.scenario_counter += 1
        scenario_id = f"qh49-{self.scenario_counter:07d}"
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="ACCEPTANCE",
            side=direction,
            swept_kind="HIGH" if direction > 0 else "LOW",
            pool_id=f"clock-{ts_event}",
            pool_level=stop_anchor,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=signal_high if direction > 0 else signal_low,
            structure=first10_vwap,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details={
                "candidate49_clock_offset_minutes": (
                    self.config.candidate49_clock_offset_minutes
                ),
                "candidate49_signal_ts_ns": ts_event,
                "candidate49_direction": direction,
                "candidate49_first10_order_imbalance": imbalance,
                "candidate49_first10_notional": first10_notional,
                "candidate49_first10_trade_count": first10_trades,
                "candidate49_first10_vwap": first10_vwap,
                "candidate49_signal_open": float(row["open"]),
                "candidate49_signal_high": signal_high,
                "candidate49_signal_low": signal_low,
                "candidate49_signal_close": close,
                "candidate49_vwap_retained": retained,
                "candidate49_horizon_minutes": self.config.max_hold_bars,
            },
        )
        self.diagnostics["candidate49_entries_attempted"] = int(
            self.diagnostics["candidate49_entries_attempted"]
        ) + 1
        self._transition(
            scenario_id,
            "QUARTER_HOUR_MEDIUM_HORIZON_ENTRY_EVALUATION",
            ts_event - _MINUTE_NS,
            ts_event,
            "ENTRY_EVALUATION",
            "FIRST10_IMBALANCE_RETAINED_THROUGH_COMPLETED_MINUTE",
            close,
            self.pending.details,
        )
        before = int(self.diagnostics.get("candidate16_natural_objective_rejections", 0))
        if self._submit_entry(self.pending, row):
            self.diagnostics["candidate49_entries_submitted"] = int(
                self.diagnostics["candidate49_entries_submitted"]
            ) + 1
        after = int(self.diagnostics.get("candidate16_natural_objective_rejections", 0))
        if after > before:
            self.diagnostics["candidate49_no_natural_objective"] = int(
                self.diagnostics["candidate49_no_natural_objective"]
            ) + 1


__all__ = ["Candidate16Config", "Candidate16Strategy"]
