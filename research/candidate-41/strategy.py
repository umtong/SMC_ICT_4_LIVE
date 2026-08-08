"""Candidate 41 adapter over Candidate 39's verified Nautilus execution policy.

Candidate 35/39 retain data ingestion, one-account arbitration, realistic
execution costs, 3% current-NAV risk sizing, bracket orders and accounting.
Candidate 41 replaces only the router clock and causal-episode policy.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
BASE39 = HERE.parent / "candidate-39"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import router as _candidate41_router

# Candidate 39 imports ``router`` by module name. Bind that name before loading
# its execution adapter so all decisions come from Candidate 41.
sys.modules["router"] = _candidate41_router
_spec = importlib.util.spec_from_file_location(
    "_candidate41_reused_candidate39_strategy",
    BASE39 / "strategy.py",
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load reused strategy from {BASE39 / 'strategy.py'}")
_base39 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _base39
_spec.loader.exec_module(_base39)

Candidate41Config = _base39.Candidate39Config
Candidate35Config = Candidate41Config  # reused BacktestNode runner contract
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class Candidate41Strategy(_base39.Candidate39Strategy):
    """One global slot; six-minute confirmation; one order per causal episode."""

    def __init__(self, config: Candidate41Config) -> None:
        super().__init__(config)
        self._episode_fifo: deque[str] = deque()
        self._episode_seen: set[str] = set()
        self._episode_memory_limit = 4_096
        self.diagnostics.update(
            {
                "duplicate_causal_episode_rejections": 0,
                "candidate41_scenario_bindings": 0,
            }
        )

    def _remember_episode(self, episode_id: str) -> None:
        if episode_id in self._episode_seen:
            return
        if len(self._episode_fifo) >= self._episode_memory_limit:
            self._episode_seen.discard(self._episode_fifo.popleft())
        self._episode_fifo.append(episode_id)
        self._episode_seen.add(episode_id)

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)

        open_symbols = [
            symbol
            for symbol in SYMBOLS
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
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1
            )
            validity = int(self.route_config.entry_validity_minutes)
            if self.minute_index - self.entry_pending_minute > validity:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="CAUSAL_RETEST_NOT_FILLED_BEFORE_VALIDITY_END",
                    validity_minutes=validity,
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
        if moment.minute % 15 != self.route_config.response_bars - 1:
            return
        required = max(
            self.route_config.context_bars
            + self.route_config.prior_bars
            + self.route_config.response_bars,
            self.route_config.atr_period
            + self.route_config.prior_bars
            + self.route_config.response_bars
            + 1,
        )
        if any(len(self.bars[symbol]) < required for symbol in SYMBOLS):
            return

        interactions: dict[str, _candidate41_router.FeatureObservation] = {}
        confirmations: dict[str, _candidate41_router.FeatureObservation] = {}
        for symbol in SYMBOLS:
            rows = list(self.bars[symbol])
            interaction_ts = rows[-self.route_config.response_bars].ts_event
            confirmation_ts = rows[-1].ts_event
            interactions[symbol] = self.features[symbol].observation(
                interaction_ts, self.config.feature_max_age_seconds
            )
            confirmations[symbol] = self.features[symbol].observation(
                confirmation_ts, self.config.feature_max_age_seconds
            )
        if not all(item.ready for item in interactions.values()):
            self.diagnostics["feature_stale_episodes"] += 1
            return
        if not all(item.ready for item in confirmations.values()):
            self.diagnostics["confirmation_feature_stale_episodes"] += 1
            return

        self.diagnostics["quarter_hour_decisions"] += 1
        winner, decisions = _candidate41_router.route_universe(
            bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features_by_symbol=interactions,
            confirmation_features_by_symbol=confirmations,
            config=self.route_config,
        )
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if not decision.actionable and decision.reasons:
                reasons = self.diagnostics["unresolved_reason_counts"]
                reason = str(decision.reasons[0])
                reasons[reason] = int(reasons.get(reason, 0)) + 1
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self._submit_decision(winner, ts_event)

    def _submit_decision(
        self,
        decision: _candidate41_router.RouteDecision,
        ts_event: int,
    ) -> None:
        episode_id = (
            f"{decision.symbol}:"
            f"{decision.diagnostics.get('causal_episode_id', decision.episode_ts)}"
        )
        if episode_id in self._episode_seen:
            self.diagnostics["duplicate_causal_episode_rejections"] += 1
            self._event(
                "DUPLICATE_CAUSAL_EPISODE_REJECTED",
                ts_event,
                symbol=decision.symbol,
                state=decision.state,
                causal_episode_id=episode_id,
            )
            return

        submissions_before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) == submissions_before:
            return

        self._remember_episode(episode_id)
        scenario = self.current_scenario
        if scenario is None:
            return
        scenario.update(
            {
                "scenario_id": f"c41-{submissions_before + 1:07d}",
                "candidate": "candidate-41-leadership-failed-reentry-router",
                "causal_episode_id": episode_id,
                "entry_validity_minutes": int(self.route_config.entry_validity_minutes),
                "signal_horizon_minutes": int(
                    self.route_config.prior_bars + self.route_config.response_bars
                ),
            }
        )
        self.diagnostics["candidate41_scenario_bindings"] += 1
        self._event("CANDIDATE41_SCENARIO_BOUND", ts_event, **scenario)


Candidate35Strategy = Candidate41Strategy
