"""Candidate 22: compressed opening-range expansion with first defended retest.

Candidate 19 remains unchanged and has priority when one of its causal auction
states is active.  Candidate 22 adds one independent opportunity family for
sessions in which the first fifteen completed one-minute bars form a range no
wider than the causal median of prior session-opening ranges.

The family deliberately separates evidence roles:

* breakout/state: range escape, directional trade flow and return, efficient
  price delivery, abnormal traded notional, and fresh OI expansion;
* retest/confirmation: the strictly later first touch must keep its body
  outside while tail flow, displayed-book imbalance and liquidity withdrawal
  defend the escaped boundary.

Only one expansion episode may be armed per four-hour session.  All execution,
fees, positions, portfolio accounting and NAV remain owned by NautilusTrader
through the inherited Candidate 18 FOK bracket and Candidate 05 runner.
"""
from __future__ import annotations

from collections import deque
from statistics import median
import math
from typing import Any

from nautilus_trader.model.data import Bar

from session_expansion_router import ExpansionDecision
from session_expansion_router import ExpansionRetest
from session_expansion_router import RetestObservation
from session_expansion_router import advance_expansion_retest
from session_expansion_router import expansion_breakout_side
from strategy_base import PendingSetup
from transmission_strategy import Candidate19Config
from transmission_strategy import Candidate19Strategy


class Candidate22Config(Candidate19Config, frozen=True):
    opening_range_bars: int = 15
    opening_range_history_sessions: int = 12
    opening_range_min_history: int = 4


class Candidate22Strategy(Candidate19Strategy):
    """Add compressed-session expansion without weakening Candidate 19."""

    _PENDING_BRANCH = "SESSION_EXPANSION_RETEST"

    def __init__(self, config: Candidate22Config) -> None:
        self.c22_retest: ExpansionRetest | None = None
        super().__init__(config=config)
        if config.opening_range_bars < 5:
            raise ValueError("opening_range_bars must be at least five")
        if config.opening_range_min_history < 1:
            raise ValueError("opening_range_min_history must be positive")
        if config.opening_range_history_sessions < config.opening_range_min_history:
            raise ValueError("opening range history cannot be shorter than minimum history")

        self.c22_session_key: int | None = None
        self.c22_session_bar_count = 0
        self.c22_opening_high = -math.inf
        self.c22_opening_low = math.inf
        self.c22_opening_fixed = False
        self.c22_opening_width_atr = float("nan")
        self.c22_opening_reference_atr = float("nan")
        self.c22_opening_compressed = False
        self.c22_session_consumed = False
        self.c22_scenario_counter = 0
        self.c22_range_history: deque[float] = deque(
            maxlen=config.opening_range_history_sessions,
        )
        self.diagnostics.update(
            {
                "candidate22_opening_ranges_fixed": 0,
                "candidate22_compressed_opening_ranges": 0,
                "candidate22_expansions_armed": 0,
                "candidate22_retest_observations": 0,
                "candidate22_retests_confirmed": 0,
                "candidate22_retests_invalidated": 0,
                "candidate22_retests_expired": 0,
                "candidate22_fok_entries": 0,
            },
        )

    @staticmethod
    def _bar_price(value: Any) -> float:
        method = getattr(value, "as_double", None)
        if callable(method):
            return float(method())
        return float(str(value).strip().split()[0].replace("_", "").replace(",", ""))

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.c22_retest = None

    def _reset_c22_session(self, session_key: int) -> None:
        self.c22_session_key = session_key
        self.c22_session_bar_count = 0
        self.c22_opening_high = -math.inf
        self.c22_opening_low = math.inf
        self.c22_opening_fixed = False
        self.c22_opening_width_atr = float("nan")
        self.c22_opening_reference_atr = float("nan")
        self.c22_opening_compressed = False
        self.c22_session_consumed = False

    def _close_stale_c22_retest(self, bar: Bar, session_key: int) -> None:
        if self.pending is None or self.pending.branch != self._PENDING_BRANCH:
            self.c22_retest = None
            return
        scenario_id = self.pending.scenario_id
        ts = int(bar.ts_event)
        self._transition(
            scenario_id,
            "SESSION_EXPANSION_RETEST_CLOSED",
            ts,
            ts,
            "CLOSED",
            "FOUR_HOUR_SESSION_ENDED_BEFORE_FIRST_RETEST",
            self._bar_price(bar.close),
            {
                **self.pending.details,
                "next_session_key": session_key,
            },
        )
        self.diagnostics["candidate22_retests_expired"] = int(
            self.diagnostics["candidate22_retests_expired"],
        ) + 1
        self.pending = None
        self.c22_retest = None

    def on_bar(self, bar: Bar) -> None:
        incoming_key = self._session_key(int(bar.ts_event))
        if self.c22_session_key is None:
            self._reset_c22_session(incoming_key)
        elif incoming_key != self.c22_session_key:
            self._close_stale_c22_retest(bar, incoming_key)
            self._reset_c22_session(incoming_key)

        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        self._observe_c22_opening_range(row)

        if self.c22_session_bar_count <= self.config.opening_range_bars:
            return
        if not self.c22_opening_fixed or not self.c22_opening_compressed:
            return
        if self.c22_session_consumed:
            return
        if self.pending is not None or self.entry_pending:
            return
        if not self.portfolio.is_flat(self.config.instrument_id):
            return
        if not self._in_evaluation(int(row["ts"])):
            return
        if self._funding_blackout(int(row["ts"])):
            return
        if not self._features_ready(int(row["ts"])):
            return
        if self.bar_index - self.last_entry_index < self.config.cooldown_bars:
            return

        side = expansion_breakout_side(
            opening_high=self.c22_opening_high,
            opening_low=self.c22_opening_low,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=self._atr(),
            flow_60s=self._feature("flow_60s"),
            ret_60s_bps=self._feature("ret_60s_bps"),
            efficiency_60s=self._feature("efficiency_60s"),
            notional_burst=self._feature("notional_burst"),
            oi_expanded=self._positioning_expanded(),
            min_progress_atr=self.config.router_acceptance_min_progress_atr,
            min_efficiency=self.config.router_acceptance_min_efficiency,
            min_close_location=self.config.router_acceptance_min_close_location,
        )
        if side == 0:
            return
        self._arm_c22_expansion(side=side, row=row)

    def _observe_c22_opening_range(self, row: dict[str, float | int]) -> None:
        self.c22_session_bar_count += 1
        if self.c22_session_bar_count > self.config.opening_range_bars:
            return
        self.c22_opening_high = max(self.c22_opening_high, float(row["high"]))
        self.c22_opening_low = min(self.c22_opening_low, float(row["low"]))
        if self.c22_session_bar_count != self.config.opening_range_bars:
            return

        atr = self._atr()
        width = self.c22_opening_high - self.c22_opening_low
        if not math.isfinite(atr) or atr <= 0.0 or width <= 0.0:
            return
        width_atr = width / atr
        prior = list(self.c22_range_history)
        reference = (
            float(median(prior))
            if len(prior) >= self.config.opening_range_min_history
            else float("nan")
        )
        compressed = math.isfinite(reference) and width_atr <= reference
        self.c22_opening_fixed = True
        self.c22_opening_width_atr = width_atr
        self.c22_opening_reference_atr = reference
        self.c22_opening_compressed = compressed
        self.c22_range_history.append(width_atr)
        self.diagnostics["candidate22_opening_ranges_fixed"] = int(
            self.diagnostics["candidate22_opening_ranges_fixed"],
        ) + 1
        if compressed:
            self.diagnostics["candidate22_compressed_opening_ranges"] = int(
                self.diagnostics["candidate22_compressed_opening_ranges"],
            ) + 1

    def _arm_c22_expansion(
        self,
        *,
        side: int,
        row: dict[str, float | int],
    ) -> None:
        if self.c22_session_key is None:
            raise RuntimeError("candidate22 session key is missing")
        self.c22_scenario_counter += 1
        scenario_id = f"sxe-{self.c22_scenario_counter:07d}"
        boundary = self.c22_opening_high if side > 0 else self.c22_opening_low
        opposite = self.c22_opening_low if side > 0 else self.c22_opening_high
        breakout_extreme = float(row["high"]) if side > 0 else float(row["low"])
        expires_index = self.bar_index + self.config.acceptance_retrace_bars
        state = ExpansionRetest(
            scenario_id=scenario_id,
            session_key=self.c22_session_key,
            side=side,
            boundary=boundary,
            opposite_boundary=opposite,
            breakout_index=self.bar_index,
            last_index=self.bar_index,
            expires_index=expires_index,
            breakout_extreme=breakout_extreme,
            max_counterflow=self.config.acceptance_max_counterflow,
            min_close_location=self.config.acceptance_retest_close_location,
        )
        details = {
            "candidate22_branch": "COMPRESSED_OPENING_RANGE_EXPANSION",
            "session_key": self.c22_session_key,
            "opening_range_bars": self.config.opening_range_bars,
            "opening_high": self.c22_opening_high,
            "opening_low": self.c22_opening_low,
            "opening_width_atr": self.c22_opening_width_atr,
            "prior_opening_width_median_atr": self.c22_opening_reference_atr,
            "side": side,
            "boundary": boundary,
            "opposite_boundary": opposite,
            "breakout_close": float(row["close"]),
            "breakout_extreme": breakout_extreme,
            "flow_60s": self._feature("flow_60s"),
            "ret_60s_bps": self._feature("ret_60s_bps"),
            "efficiency_60s": self._feature("efficiency_60s"),
            "notional_burst": self._feature("notional_burst"),
            "oi_change_5m": self._feature("oi_change_5m"),
            "metrics_age_seconds": self._feature("metrics_age_seconds"),
            "retest_expires_index": expires_index,
        }
        self.c22_retest = state
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch=self._PENDING_BRANCH,
            side=side,
            swept_kind="HIGH" if side > 0 else "LOW",
            pool_id=f"session-{self.c22_session_key}",
            pool_level=boundary,
            created_index=self.bar_index,
            expires_index=expires_index,
            sweep_extreme=breakout_extreme,
            structure=opposite,
            atr=self._atr(),
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        self.c22_session_consumed = True
        self.diagnostics["candidate22_expansions_armed"] = int(
            self.diagnostics["candidate22_expansions_armed"],
        ) + 1
        self._transition(
            scenario_id,
            "SESSION_EXPANSION_ARMED",
            int(row["ts"]),
            int(row["ts"]),
            "FIRST_RETEST_PENDING",
            "COMPRESSED_RANGE_ESCAPED_WITH_EFFICIENT_FLOW_AND_FRESH_OI",
            float(row["close"]),
            details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        if self.pending is not None and self.pending.branch == self._PENDING_BRANCH:
            return self._process_c22_retest(row)
        return super()._process_pending(row)

    def _process_c22_retest(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        state = self.c22_retest
        if setup is None or state is None:
            if setup is not None:
                self._transition(
                    setup.scenario_id,
                    "SESSION_EXPANSION_RETEST_CLOSED",
                    int(row["ts"]),
                    int(row["ts"]),
                    "CLOSED",
                    "MISSING_FROZEN_EXPANSION_STATE",
                    float(row["close"]),
                    setup.details,
                )
            self.pending = None
            self.c22_retest = None
            return True
        if self.bar_index <= state.last_index:
            return True

        try:
            state = advance_expansion_retest(
                state,
                RetestObservation(
                    bar_index=self.bar_index,
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    flow_15s=self._feature("flow_15s"),
                    depth_imbalance_1=self._feature("depth_imbalance_1"),
                    liquidity_ahead_change_1m=self._feature(
                        self._ahead_depth_field(state.side),
                    ),
                ),
            )
        except ValueError as exc:
            self._transition(
                setup.scenario_id,
                "SESSION_EXPANSION_RETEST_CLOSED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "NONFINITE_OR_INCONSISTENT_RETEST_OBSERVATION",
                float(row["close"]),
                {**setup.details, "error": str(exc)},
            )
            self.pending = None
            self.c22_retest = None
            return True

        self.c22_retest = state
        setup.details["latest_candidate22_retest"] = {
            "decision": state.decision.value,
            "reason": state.reason,
            "observations": state.observations,
            "last_index": state.last_index,
            "flow_15s": self._feature("flow_15s"),
            "depth_imbalance_1": self._feature("depth_imbalance_1"),
            "liquidity_ahead_change_1m": self._feature(
                self._ahead_depth_field(state.side),
            ),
        }
        self.diagnostics["candidate22_retest_observations"] = int(
            self.diagnostics["candidate22_retest_observations"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "SESSION_EXPANSION_RETEST_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            "FIRST_RETEST_PENDING"
            if state.decision is ExpansionDecision.WAITING
            else state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )
        if state.decision is ExpansionDecision.WAITING:
            return True
        if state.decision is ExpansionDecision.INVALIDATED:
            self.diagnostics["candidate22_retests_invalidated"] = int(
                self.diagnostics["candidate22_retests_invalidated"],
            ) + 1
            self.pending = None
            self.c22_retest = None
            return True
        if state.decision is ExpansionDecision.EXPIRED:
            self.diagnostics["candidate22_retests_expired"] = int(
                self.diagnostics["candidate22_retests_expired"],
            ) + 1
            self.pending = None
            self.c22_retest = None
            return True
        if state.decision is not ExpansionDecision.CONFIRMED:
            raise RuntimeError(f"unexpected expansion decision {state.decision!r}")

        completed = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="ACCEPTANCE",
            side=state.side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=state.boundary,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=state.breakout_extreme,
            structure=state.opposite_boundary,
            atr=self._atr(),
            hold_count=0,
            retrace_armed=True,
            details={
                **setup.details,
                "candidate22_branch": "COMPRESSED_EXPANSION_FIRST_RETEST_CONFIRMED",
                "confirmed_candidate22_retest": {
                    "decision": state.decision.value,
                    "reason": state.reason,
                    "observations": state.observations,
                },
            },
        )
        self.pending = completed
        self.c22_retest = None
        self.diagnostics["candidate22_retests_confirmed"] = int(
            self.diagnostics["candidate22_retests_confirmed"],
        ) + 1
        self._transition(
            completed.scenario_id,
            "SESSION_EXPANSION_RETEST_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            state.reason,
            float(row["close"]),
            completed.details,
        )
        submitted = super()._submit_entry(completed, row)
        if submitted:
            self.diagnostics["candidate22_fok_entries"] = int(
                self.diagnostics["candidate22_fok_entries"],
            ) + 1
        return True


__all__ = ["Candidate22Config", "Candidate22Strategy"]
