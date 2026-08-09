"""Candidate 51 standalone causal SMAOffsetV2 adapter over NautilusTrader."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from router import FeatureObservation, SMA_OFFSET_STATE, route_universe, sma_offset_exit_ready
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    """Execution config extended only with source-policy parameters."""

    sma_offset_low: float = 0.960
    sma_offset_high: float = 1.012
    sma_stop_min_fraction: float = 0.0075
    sma_stop_max_fraction: float = 0.1000
    sma_stop_atr_buffer: float = 0.50


class Candidate35Strategy(_ExecutionShell):
    """Public SMAOffsetV2 policy with one-use causal episodes and one global slot."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            sma_offset_low=float(config.sma_offset_low),
            sma_offset_high=float(config.sma_offset_high),
            sma_stop_min_fraction=float(config.sma_stop_min_fraction),
            sma_stop_max_fraction=float(config.sma_stop_max_fraction),
            sma_stop_atr_buffer=float(config.sma_stop_atr_buffer),
        )
        self.diagnostics.setdefault("sma_offset_exit_submissions", 0)
        self.diagnostics.setdefault("source_signals_before_execution_filters", 0)
        self.diagnostics.setdefault("funding_runway_rejections", 0)
        self.diagnostics.setdefault("cooldown_rejections", 0)
        self.diagnostics.setdefault("used_episode_rejections", 0)
        self.used_episode_keys: set[tuple[str, str, int]] = set()

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
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1,
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
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % self.route_config.bucket_minutes != self.route_config.bucket_minutes - 1:
            return
        required_bars = max(
            self.route_config.bucket_minutes * (self.route_config.sma_offset_period + 4),
            60 * (self.route_config.sma_trend_slow + 3),
        )
        if any(len(self.bars[symbol]) < required_bars for symbol in SYMBOLS):
            return

        features: dict[str, FeatureObservation] = {
            symbol: self.features[symbol].observation(ts_event, self.config.feature_max_age_seconds)
            for symbol in SYMBOLS
        }
        if not all(item.ready for item in features.values()):
            self.diagnostics["feature_stale_episodes"] += 1
            return

        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
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

        actionable = [decision for decision in decisions.values() if decision.actionable]
        self.diagnostics["source_signals_before_execution_filters"] += len(actionable)
        unused = []
        for decision in actionable:
            key = (decision.symbol, decision.state, int(decision.episode_ts))
            if key in self.used_episode_keys:
                self.diagnostics["used_episode_rejections"] += 1
            else:
                unused.append(decision)
        unused.sort(key=lambda item: (-item.score, item.symbol))
        winner = unused[0] if unused else None
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            self.diagnostics["cooldown_rejections"] += 1
            return
        self.used_episode_keys.add((winner.symbol, winner.state, int(winner.episode_ts)))
        self._submit_decision(winner, ts_event)

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") == SMA_OFFSET_STATE:
            moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
            if moment.minute % self.route_config.bucket_minutes == self.route_config.bucket_minutes - 1:
                source_exit, exit_details = sma_offset_exit_ready(
                    tuple(self.bars[self.current_symbol]), self.route_config,
                )
                if source_exit:
                    instrument_id = self.instrument_ids[self.current_symbol]
                    self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
                    self.diagnostics["sma_offset_exit_submissions"] = int(
                        self.diagnostics.get("sma_offset_exit_submissions", 0)
                    ) + 1
                    self._event("PUBLIC_SMA_OFFSET_V2_EXIT", ts_event, **exit_details)
                    return
        super()._manage_open_position(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
