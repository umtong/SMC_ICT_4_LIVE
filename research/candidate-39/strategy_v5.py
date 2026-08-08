"""Candidate 39 V5 Nautilus adapter.

V5 keeps NautilusTrader execution/accounting and the project 3% current-NAV
risk budget, while routing three non-scalping, feature-informed auction families.
A pending passive order is cancelled immediately if its price state invalidates
before fill; it cannot wait for a stale retest after the structural stop/level
has already failed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import router_v5 as _router
import strategy_v4 as _v4

Candidate39Config = _v4.Candidate39Config
Candidate35Config = Candidate39Config


class Candidate39V5Strategy(_v4.Candidate39V4Strategy):
    ENTRY_VALIDITY_MINUTES = 20
    MIN_OPERATIONAL_HORIZON_MINUTES = 60
    CANDIDATE_ID = "candidate-39-feature-informed-trader-router-v5"

    def __init__(self, config: Candidate39Config) -> None:
        super().__init__(config)
        self.v5_config = _router.InformedRouterConfig()
        self.diagnostics.update(
            {
                "v5_completed_15m_decisions": 0,
                "v5_feature_stale_episodes": 0,
                "v5_warmup_rejections": 0,
                "v5_clock_mismatch_rejections": 0,
                "v5_operational_horizon_rejections": 0,
                "v5_pending_stop_invalidations": 0,
                "v5_pending_state_invalidations": 0,
                "v5_no_setup_episodes": 0,
                "v5_global_ambiguity_rejections": 0,
                "v5_consumed_episode_suppressions": 0,
                "v5_identity_rebindings": 0,
                "v5_family_counts": {},
                "v5_family_entries": {},
                "v5_state_repair": (
                    "initiative sponsorship vs liquidation climax; leverage flush "
                    "to flow-flip reacceptance; sponsored opening-range acceptance"
                ),
            }
        )

    def _episode_key(self, decision: _router.RouteDecision) -> tuple[str, str, object]:
        identity = decision.diagnostics.get("episode_key", int(decision.episode_ts))
        return (decision.symbol, decision.state, identity)

    def _pending_state_invalidated(self, symbol: str) -> tuple[bool, str]:
        scenario = self.current_scenario or {}
        diagnostics = scenario.get("diagnostics") or {}
        family = str(diagnostics.get("family", scenario.get("state", "")))
        side = int(scenario.get("side", 0))
        if side not in (-1, 1) or not self.bars[symbol]:
            return True, "INVALID_PENDING_STATE"
        latest = self.bars[symbol][-1]
        stop = float(scenario.get("stop", math.nan))
        if math.isfinite(stop) and _router.pending_setup_invalidated(latest, side, stop):
            return True, "STRUCTURAL_STOP_TOUCHED_BEFORE_FILL"

        level = math.nan
        # The setup has already asserted acceptance on the actionable side of
        # the level. A completed one-minute close back across that level before
        # the passive parent fills is a state failure, not harmless noise.
        tolerance = 0.0
        if family == "LIQUIDATION_FAILURE_REACCEPTANCE":
            level = float(diagnostics.get("attacked_level", math.nan))
        elif family == "OPENING_RANGE_ACCEPTANCE_RETEST":
            level = float(
                diagnostics.get(
                    "opening_range_high" if side > 0 else "opening_range_low",
                    math.nan,
                )
            )
        if math.isfinite(level):
            crossed = latest.close < level - tolerance if side > 0 else latest.close > level + tolerance
            if crossed:
                return True, "ACCEPTED_VALUE_LOST_BEFORE_PASSIVE_FILL"
        return False, ""

    def _bind_v4_identity(self, decision: _router.RouteDecision, ts_event: int) -> None:
        if not self.current_scenario:
            return
        state_machines = {
            "SPONSORED_FIRST_PULLBACK": "SPONSORED_INITIATIVE_TO_FIRST_CONTROLLED_PULLBACK",
            "LIQUIDATION_FAILURE_REACCEPTANCE": "LEVERAGE_FLUSH_TO_LEVEL_REACCEPTANCE_FLOW_FLIP",
            "OPENING_RANGE_ACCEPTANCE_RETEST": "SESSION_OPENING_RANGE_TO_SPONSORED_ACCEPTANCE_RETEST",
        }
        self.current_scenario.update(
            {
                "candidate": self.CANDIDATE_ID,
                "scenario_id": f"c39v5-{self.diagnostics['entry_submissions']:07d}",
                "candidate_version": 5,
                "state_machine": state_machines.get(decision.state, decision.state),
                "entry_validity_minutes": self.ENTRY_VALIDITY_MINUTES,
                "signal_horizon_minutes": 15,
                "intended_holding_horizon_minutes": [30, 240],
                "minimum_operational_horizon_minutes": self.MIN_OPERATIONAL_HORIZON_MINUTES,
                "source_derived": True,
                "same_event_reentry_allowed": False,
                "state_repair_applied": True,
            }
        )
        self.diagnostics["v5_identity_rebindings"] += 1
        entries = self.diagnostics["v5_family_entries"]
        entries[decision.state] = int(entries.get(decision.state, 0)) + 1
        self._event(
            "V5_DECISION_BOUND",
            ts_event,
            candidate=self.CANDIDATE_ID,
            state=decision.state,
            symbol=decision.symbol,
            side=decision.side,
            score=decision.score,
            episode_ts=decision.episode_ts,
            episode_key=decision.diagnostics.get("episode_key"),
            source_derived=True,
            non_scalping=True,
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)

        symbols = _v4._v2._base.SYMBOLS
        open_symbols = [
            symbol
            for symbol in symbols
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
            assert self.current_symbol is not None
            invalidated, reason = self._pending_state_invalidated(self.current_symbol)
            if invalidated:
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                key = (
                    "v5_pending_stop_invalidations"
                    if reason == "STRUCTURAL_STOP_TOUCHED_BEFORE_FILL"
                    else "v5_pending_state_invalidations"
                )
                self.diagnostics[key] += 1
                self._event(
                    "ENTRY_CANCELLED_BEFORE_FILL",
                    ts_event,
                    symbol=self.current_symbol,
                    reason=reason,
                )
                self._clear_trade_state()
                return
            if self.minute_index - self.entry_pending_minute > self.ENTRY_VALIDITY_MINUTES:
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="V5_PASSIVE_RETEST_NOT_FILLED_WITHIN_VALIDITY",
                    validity_minutes=self.ENTRY_VALIDITY_MINUTES,
                )
                self._clear_trade_state()
            return

        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 15 != 14:
            return
        if _router.minutes_to_next_funding(ts_event) < self.MIN_OPERATIONAL_HORIZON_MINUTES:
            self.diagnostics["v5_operational_horizon_rejections"] += 1
            return

        aggregates = {
            symbol: _v4._router.aggregate_completed_15m(tuple(self.bars[symbol]))
            for symbol in symbols
        }
        if any(
            len(items) < self.v5_config.price.min_completed_15m_bars
            for items in aggregates.values()
        ):
            self.diagnostics["v5_warmup_rejections"] += 1
            return
        latest_times = {items[-1].ts_event for items in aggregates.values()}
        if len(latest_times) != 1:
            self.diagnostics["v5_clock_mismatch_rejections"] += 1
            return
        completed_ts = next(iter(latest_times))
        if completed_ts <= self.v4_last_completed_15m_ts:
            return
        self.v4_last_completed_15m_ts = completed_ts

        confirmation_features = {
            symbol: self.features[symbol].observation(
                completed_ts,
                self.config.feature_max_age_seconds,
            )
            for symbol in symbols
        }
        if not all(item.ready for item in confirmation_features.values()):
            self.diagnostics["v5_feature_stale_episodes"] += 1
            return

        def feature_at(symbol: str, observed_ts: int) -> _router.FeatureObservation:
            return self.features[symbol].observation(
                observed_ts,
                self.config.feature_max_age_seconds,
            )

        self.diagnostics["quarter_hour_decisions"] += 1
        self.diagnostics["v5_completed_15m_decisions"] += 1
        winner, decisions = _router.route_informed_universe(
            minute_bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in symbols},
            confirmation_features_by_symbol=confirmation_features,
            feature_at=feature_at,
            config=self.v5_config,
        )
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            families = self.diagnostics["v5_family_counts"]
            families[decision.state] = int(families.get(decision.state, 0)) + 1

        if winner is None:
            if decisions:
                self.diagnostics["v5_global_ambiguity_rejections"] += 1
                self._consume_current_decisions(decisions)
                self._event(
                    "V5_NO_TRADE",
                    ts_event,
                    reason="OPPOSITE_SIDE_GLOBAL_AMBIGUITY",
                    candidates=[
                        {
                            "symbol": item.symbol,
                            "state": item.state,
                            "side": item.side,
                            "score": item.score,
                            "episode_key": item.diagnostics.get("episode_key"),
                        }
                        for item in decisions.values()
                    ],
                )
            else:
                self.diagnostics["v5_no_setup_episodes"] += 1
            self.diagnostics["unresolved_episodes"] += 1
            return

        key = self._episode_key(winner)
        if key in self.v4_consumed_episodes:
            self.diagnostics["v5_consumed_episode_suppressions"] += 1
            self._consume_current_decisions(decisions)
            self._event(
                "V5_NO_TRADE",
                ts_event,
                reason="CAUSAL_EPISODE_ALREADY_CONSUMED",
                symbol=winner.symbol,
                state=winner.state,
                episode_key=winner.diagnostics.get("episode_key"),
            )
            return
        self._consume_current_decisions(decisions)
        self._submit_decision(winner, ts_event)


Candidate39Strategy = Candidate39V5Strategy
Candidate35Strategy = Candidate39V5Strategy
