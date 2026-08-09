"""Candidate 51 timing adapter over the audited Candidate 35 execution shell.

Only the decision clock is changed: Candidate 51 evaluates completed data every
five minutes and waits for enough history for the 8h fan/Ichimoku context. Order,
fee, latency, bracket, risk, portfolio and continuous-NAV handling remain in the
reused base strategy.
"""
from __future__ import annotations

from datetime import datetime, timezone

from router import FeatureObservation, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Strategy(_ExecutionShell):
    """Five-minute causal router using the inherited single-account executor."""

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
                int(self.diagnostics["max_simultaneous_entry_intents"]),
                1,
            )
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event("ENTRY_EXPIRED", ts_event, reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES")
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 != 4:
            return
        required_bars = max(self.route_config.cloud_span_b, self.route_config.fan_slow) + 20
        if any(len(self.bars[symbol]) < required_bars for symbol in SYMBOLS):
            return

        features: dict[str, FeatureObservation] = {}
        for symbol in SYMBOLS:
            observation = self.features[symbol].observation(
                ts_event,
                self.config.feature_max_age_seconds,
            )
            features[symbol] = observation
        if not all(item.ready for item in features.values()):
            self.diagnostics["feature_stale_episodes"] += 1
            return

        self.diagnostics["quarter_hour_decisions"] += 1
        winner, decisions = route_universe(
            bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features_by_symbol=features,
            config=self.route_config,
        )
        reason_counts = self.diagnostics.setdefault("unresolved_reason_counts", {})
        family_counts = self.diagnostics.setdefault("actionable_family_counts", {})
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = int(family_counts.get(decision.state, 0)) + 1
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self._submit_decision(winner, ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
