"""Candidate 39 V4 Nautilus adapter for trader-derived day-trading scenarios.

V4 deliberately leaves the V2/V3 OI router untouched.  It reuses only the
four-asset NautilusTrader execution/accounting shell and replaces the decision
boundary with two price-auction scenarios reconstructed from concrete trader
methods:

* first controlled pullback after multi-hour initiative;
* failed prior-day/session level attack, reacceptance, and later retest.

The strategy consumes only completed 15-minute evidence, submits at most one
passive bracket across BTC/ETH/SOL/XRP, risks current NAV x 3%, and never chases
an episode after its first common-clock evaluation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import router_v4 as _router
import strategy as _v2

Candidate39Config = _v2.Candidate39Config
Candidate35Config = Candidate39Config


class Candidate39V4Strategy(_v2.Candidate39Strategy):
    """One continuous account routed by two independent non-scalping families."""

    ENTRY_VALIDITY_MINUTES = 20
    CANDIDATE_ID = "candidate-39-trader-derived-auction-router-v4"

    def __init__(self, config: Candidate39Config) -> None:
        super().__init__(config)
        self.v4_config = _router.TraderDerivedConfig()
        self.v4_consumed_episodes: set[tuple[str, str, int]] = set()
        self.v4_last_completed_15m_ts = -1
        self.diagnostics.update(
            {
                "v4_completed_15m_decisions": 0,
                "v4_warmup_rejections": 0,
                "v4_clock_mismatch_rejections": 0,
                "v4_no_setup_episodes": 0,
                "v4_global_ambiguity_rejections": 0,
                "v4_consumed_episode_suppressions": 0,
                "v4_first_pullback_candidates": 0,
                "v4_failed_level_candidates": 0,
                "v4_first_pullback_entries": 0,
                "v4_failed_level_entries": 0,
                "v4_identity_rebindings": 0,
                "v4_family_counts": {},
                "v4_source_policy": (
                    "Rounders/Raschke/Shannon first-pullback continuation plus "
                    "CryptoCred/Raschke/domestic-HTF failed-level reacceptance"
                ),
            }
        )

    def _episode_key(self, decision: _router.RouteDecision) -> tuple[str, str, int]:
        return (decision.symbol, decision.state, int(decision.episode_ts))

    def _consume_current_decisions(
        self,
        decisions: dict[str, _router.RouteDecision],
    ) -> None:
        for decision in decisions.values():
            self.v4_consumed_episodes.add(self._episode_key(decision))

    def _bind_v4_identity(
        self,
        decision: _router.RouteDecision,
        ts_event: int,
    ) -> None:
        if not self.current_scenario:
            return
        self.current_scenario.update(
            {
                "candidate": self.CANDIDATE_ID,
                "scenario_id": f"c39v4-{self.diagnostics['entry_submissions']:07d}",
                "candidate_version": 4,
                "state_machine": (
                    "MULTI_HOUR_INITIATIVE_TO_FIRST_CONTROLLED_PULLBACK"
                    if decision.state == "FIRST_PULLBACK_CONTINUATION"
                    else "TIME_ANCHORED_LEVEL_ATTACK_TO_REACCEPTANCE_RETEST"
                ),
                "entry_validity_minutes": self.ENTRY_VALIDITY_MINUTES,
                "signal_horizon_minutes": 15,
                "intended_holding_horizon_minutes": [30, 240],
                "source_derived": True,
                "same_event_reentry_allowed": False,
            }
        )
        self.diagnostics["v4_identity_rebindings"] += 1
        if decision.state == "FIRST_PULLBACK_CONTINUATION":
            self.diagnostics["v4_first_pullback_entries"] += 1
        elif decision.state == "FAILED_LEVEL_REACCEPTANCE":
            self.diagnostics["v4_failed_level_entries"] += 1
        self._event(
            "V4_DECISION_BOUND",
            ts_event,
            candidate=self.CANDIDATE_ID,
            state=decision.state,
            symbol=decision.symbol,
            side=decision.side,
            score=decision.score,
            episode_ts=decision.episode_ts,
            source_derived=True,
            non_scalping=True,
        )

    def _submit_decision(
        self,
        decision: _router.RouteDecision,
        ts_event: int,
    ) -> None:
        was_pending = bool(self.entry_pending)
        before_submissions = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(decision, ts_event)
        submitted = (
            not was_pending
            and self.entry_pending
            and int(self.diagnostics["entry_submissions"]) > before_submissions
        )
        if submitted:
            self._bind_v4_identity(decision, ts_event)

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        """Advance one common minute and evaluate only newly completed 15m bars."""
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)

        open_symbols = [
            symbol
            for symbol in _v2._base.SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]),
            len(open_symbols),
        )
        if len(open_symbols) > 1:
            self.diagnostics["global_position_violations"] += 1
            for symbol in open_symbols:
                self.cancel_all_orders(self.instrument_ids[symbol])
                self.close_all_positions(self.instrument_ids[symbol])
            return
        if open_symbols:
            self.current_symbol = open_symbols[0]
            self._manage_open_position(ts_event)
            return

        if self.entry_pending:
            self.diagnostics["max_simultaneous_entry_intents"] = max(
                int(self.diagnostics["max_simultaneous_entry_intents"]),
                1,
            )
            if (
                self.minute_index - self.entry_pending_minute
                > self.ENTRY_VALIDITY_MINUTES
            ):
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="V4_PASSIVE_RETEST_NOT_FILLED_WITHIN_VALIDITY",
                    validity_minutes=self.ENTRY_VALIDITY_MINUTES,
                )
                self._clear_trade_state()
            return

        if not (
            self.config.evaluation_start_ns
            <= ts_event
            <= self.config.evaluation_end_ns
        ):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return

        moment = datetime.fromtimestamp(
            ts_event / 1_000_000_000,
            tz=timezone.utc,
        )
        # Binance one-minute timestamps are UTC aligned.  At :14/:29/:44/:59
        # the just-finished UTC 15-minute bucket is complete.  Aggregation below
        # independently verifies all 15 constituent bars.
        if moment.minute % 15 != 14:
            return

        aggregates = {
            symbol: _router.aggregate_completed_15m(tuple(self.bars[symbol]))
            for symbol in _v2._base.SYMBOLS
        }
        if any(
            len(items) < self.v4_config.min_completed_15m_bars
            for items in aggregates.values()
        ):
            self.diagnostics["v4_warmup_rejections"] += 1
            return
        latest_times = {items[-1].ts_event for items in aggregates.values()}
        if len(latest_times) != 1:
            self.diagnostics["v4_clock_mismatch_rejections"] += 1
            return
        completed_ts = next(iter(latest_times))
        if completed_ts <= self.v4_last_completed_15m_ts:
            return
        self.v4_last_completed_15m_ts = completed_ts
        self.diagnostics["quarter_hour_decisions"] += 1
        self.diagnostics["v4_completed_15m_decisions"] += 1

        winner, decisions = _router.route_trader_derived_universe(
            minute_bars_by_symbol={
                symbol: tuple(self.bars[symbol])
                for symbol in _v2._base.SYMBOLS
            },
            config=self.v4_config,
        )

        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            families = self.diagnostics["v4_family_counts"]
            families[decision.state] = int(families.get(decision.state, 0)) + 1
            if decision.state == "FIRST_PULLBACK_CONTINUATION":
                self.diagnostics["v4_first_pullback_candidates"] += 1
            elif decision.state == "FAILED_LEVEL_REACCEPTANCE":
                self.diagnostics["v4_failed_level_candidates"] += 1

        if winner is None:
            if decisions:
                self.diagnostics["v4_global_ambiguity_rejections"] += 1
                self._consume_current_decisions(decisions)
                self._event(
                    "V4_NO_TRADE",
                    ts_event,
                    reason="OPPOSITE_SIDE_GLOBAL_AMBIGUITY",
                    candidates=[
                        {
                            "symbol": item.symbol,
                            "state": item.state,
                            "side": item.side,
                            "score": item.score,
                            "episode_ts": item.episode_ts,
                        }
                        for item in decisions.values()
                    ],
                )
            else:
                self.diagnostics["v4_no_setup_episodes"] += 1
            self.diagnostics["unresolved_episodes"] += 1
            return

        key = self._episode_key(winner)
        if key in self.v4_consumed_episodes:
            self.diagnostics["v4_consumed_episode_suppressions"] += 1
            self._consume_current_decisions(decisions)
            self._event(
                "V4_NO_TRADE",
                ts_event,
                reason="CAUSAL_EPISODE_ALREADY_CONSUMED",
                symbol=winner.symbol,
                state=winner.state,
                episode_ts=winner.episode_ts,
            )
            return

        # Every candidate generated by this common clock is consumed now,
        # including non-selected symbols.  It cannot be recycled next quarter
        # hour to inflate trade count or chase a stale auction.
        self._consume_current_decisions(decisions)
        self._submit_decision(winner, ts_event)


Candidate39Strategy = Candidate39V4Strategy
Candidate35Strategy = Candidate39V4Strategy
