"""NautilusTrader adapter for the causal online 15-minute Markov family."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from typing import Sequence

from router import BarObservation, MARKOV_STATE, OnlineMarkovRouter
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    markov_sequence_length: int = 2
    markov_min_transition_count: int = 10
    markov_min_direction_probability: float = 0.80
    markov_atr_period: int = 18
    markov_volume_period: int = 24
    markov_price_state_threshold: float = 0.774325295833127
    markov_volume_high_multiplier: float = 2.96381982151409
    markov_volume_low_multiplier: float = 0.76691565071116
    markov_stop_atr: float = 3.2935543511297
    markov_target_atr: float = 2.04204584013095
    markov_long_only: bool = False


def _completed_bucket(
    bars: Sequence[BarObservation], minutes: int
) -> BarObservation | None:
    if minutes <= 0 or len(bars) < minutes:
        return None
    sample = list(bars)[-minutes:]
    minute_ns = 60_000_000_000
    timestamps = [int(bar.ts_event) for bar in sample]
    if any(
        timestamps[index] - timestamps[index - 1] != minute_ns
        for index in range(1, len(timestamps))
    ):
        return None
    phase = timestamps[-1] % minute_ns
    ordinal = (timestamps[-1] - phase) // minute_ns
    if ordinal % minutes != minutes - 1:
        return None
    return BarObservation(
        ts_event=timestamps[-1],
        open=float(sample[0].open),
        high=max(float(bar.high) for bar in sample),
        low=min(float(bar.low) for bar in sample),
        close=float(sample[-1].close),
        volume=sum(max(0.0, float(bar.volume)) for bar in sample),
    )


class Candidate35Strategy(_ExecutionShell):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=300) for symbol in SYMBOLS
        }
        self.fifteen_bars = {symbol: deque(maxlen=4_000) for symbol in SYMBOLS}
        self.route_config = replace(
            self.route_config,
            markov_sequence_length=int(config.markov_sequence_length),
            markov_min_transition_count=int(config.markov_min_transition_count),
            markov_min_direction_probability=float(config.markov_min_direction_probability),
            markov_atr_period=int(config.markov_atr_period),
            markov_volume_period=int(config.markov_volume_period),
            markov_price_state_threshold=float(config.markov_price_state_threshold),
            markov_volume_high_multiplier=float(config.markov_volume_high_multiplier),
            markov_volume_low_multiplier=float(config.markov_volume_low_multiplier),
            markov_stop_atr=float(config.markov_stop_atr),
            markov_target_atr=float(config.markov_target_atr),
            markov_long_only=bool(config.markov_long_only),
        )
        self.markov = OnlineMarkovRouter(self.route_config, SYMBOLS)
        self.used_episode_keys: set[tuple[str, int]] = set()
        self.diagnostics.update(
            {
                "external_source": "jehumtine/markov-chain-trading-strategy",
                "external_performance_used_as_evidence": False,
                "source_validity_repairs": [
                    "completed-right-labeled-15m-bars",
                    "no-backfill",
                    "online-transition-update-only-after-next-state",
                    "one-continuous-nautilus-account",
                    "full-project-cost-and-risk-model",
                ],
                "markov_sequence_length": int(config.markov_sequence_length),
                "markov_min_transition_count": int(config.markov_min_transition_count),
                "markov_min_direction_probability": float(config.markov_min_direction_probability),
                "markov_long_only": bool(config.markov_long_only),
                "markov_15m_bars_by_symbol": {},
                "markov_observations_by_symbol": {},
                "markov_learned_transitions_by_symbol": {},
                "markov_source_candidates": 0,
                "markov_used_episode_rejections": 0,
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
            }
        )

    def _advance_models(self, ts_event: int):
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % 15 != 14:
            return None
        decisions = {}
        for symbol in SYMBOLS:
            candle = _completed_bucket(tuple(self.bars[symbol]), 15)
            if candle is None:
                continue
            if self.fifteen_bars[symbol] and self.fifteen_bars[symbol][-1].ts_event == candle.ts_event:
                continue
            self.fifteen_bars[symbol].append(candle)
            decisions[symbol] = self.markov.advance(symbol, tuple(self.fifteen_bars[symbol]))
        self.diagnostics["markov_15m_bars_by_symbol"] = {
            symbol: len(self.fifteen_bars[symbol]) for symbol in SYMBOLS
        }
        self.diagnostics["markov_observations_by_symbol"] = dict(self.markov.observations)
        self.diagnostics["markov_learned_transitions_by_symbol"] = dict(self.markov.learned_transitions)
        return decisions if len(decisions) == len(SYMBOLS) else None

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        decisions = self._advance_models(ts_event)
        self._record_equity(ts_event)

        open_symbols = [
            symbol for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]), len(open_symbols)
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
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event("ENTRY_EXPIRED", ts_event, reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES")
                self._clear_trade_state()
            return
        if decisions is None:
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return

        self.diagnostics["quarter_hour_decisions"] += 1
        candidates = []
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        route_counts = self.diagnostics["route_counts"]
        for decision in decisions.values():
            route_counts[decision.state] = int(route_counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = int(family_counts.get(decision.state, 0)) + 1
                self.diagnostics["markov_source_candidates"] += 1
                key = (decision.symbol, int(decision.episode_ts))
                if key in self.used_episode_keys:
                    self.diagnostics["markov_used_episode_rejections"] += 1
                else:
                    candidates.append(decision)
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
        if not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return
        candidates.sort(key=lambda item: (-float(item.score), item.symbol))
        winner = candidates[0]
        key = (winner.symbol, int(winner.episode_ts))
        self.used_episode_keys.add(key)
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before and self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-51-causal-markov-v26",
                    "state_family": MARKOV_STATE,
                    "source_performance_used": False,
                    "sequence_length": int(self.config.markov_sequence_length),
                    "transition_sample_count": int(winner.diagnostics["sample_count"]),
                    "selected_probability": float(winner.diagnostics["selected_probability"]),
                    "markov_atr_at_entry": float(winner.diagnostics["atr_at_entry"]),
                    "management": "source-ATR-bracket-plus-project-daytrade-exit",
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
