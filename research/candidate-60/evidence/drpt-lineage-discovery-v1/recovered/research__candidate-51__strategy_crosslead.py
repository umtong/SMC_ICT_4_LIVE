"""NautilusTrader adapter for the causal BTC/ETH -> SOL/XRP lead-lag router."""
from __future__ import annotations

from collections import deque
from dataclasses import replace

from router import FeatureObservation, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    crosslead_mode: str = "seesaw"
    crosslead_bucket_minutes: int = 5
    crosslead_train_pairs: int = 288
    crosslead_min_pairs: int = 144
    crosslead_min_abs_beta: float = 0.10
    crosslead_min_abs_tstat: float = 2.00
    crosslead_min_shock_z: float = 2.00
    crosslead_min_predicted_bps: float = 30.0
    crosslead_atr_period: int = 14
    crosslead_stop_buffer_atr: float = 0.10
    crosslead_min_reward_r: float = 1.25


class Candidate35Strategy(_ExecutionShell):
    """One global account slot for a next-completed-bucket prediction."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        required_minutes = int(config.crosslead_bucket_minutes) * (
            int(config.crosslead_train_pairs)
            + int(config.crosslead_atr_period)
            + 20
        )
        self.bars = {
            symbol: deque(
                self.bars[symbol],
                maxlen=max(5_000, required_minutes),
            )
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            crosslead_mode=str(config.crosslead_mode),
            crosslead_bucket_minutes=int(config.crosslead_bucket_minutes),
            crosslead_train_pairs=int(config.crosslead_train_pairs),
            crosslead_min_pairs=int(config.crosslead_min_pairs),
            crosslead_min_abs_beta=float(config.crosslead_min_abs_beta),
            crosslead_min_abs_tstat=float(config.crosslead_min_abs_tstat),
            crosslead_min_shock_z=float(config.crosslead_min_shock_z),
            crosslead_min_predicted_bps=float(
                config.crosslead_min_predicted_bps
            ),
            crosslead_atr_period=int(config.crosslead_atr_period),
            crosslead_stop_buffer_atr=float(config.crosslead_stop_buffer_atr),
            crosslead_min_reward_r=float(config.crosslead_min_reward_r),
        )
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self.diagnostics.update(
            {
                "external_hypothesis": (
                    "cryptocurrency lead-lag and seesaw literature"
                ),
                "external_performance_used_as_evidence": False,
                "crosslead_mode": str(config.crosslead_mode),
                "crosslead_bucket_minutes": int(
                    config.crosslead_bucket_minutes
                ),
                "crosslead_relationship_decisions": 0,
                "crosslead_candidates": 0,
                "crosslead_used_episode_rejections": 0,
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
            }
        )

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
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES",
                )
                self._clear_trade_state()
            return

        if not (
            self.config.evaluation_start_ns
            <= ts_event
            <= self.config.evaluation_end_ns
        ):
            return

        bucket = int(self.route_config.crosslead_bucket_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % bucket != bucket - 1:
            return

        required_minutes = bucket * (
            int(self.route_config.crosslead_min_pairs)
            + int(self.route_config.crosslead_atr_period)
            + 4
        )
        if any(len(self.bars[symbol]) < required_minutes for symbol in SYMBOLS):
            return

        observations = {
            symbol: FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        self.diagnostics["crosslead_relationship_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol])
                for symbol in SYMBOLS
            },
            features_by_symbol=observations,
            config=self.route_config,
        )

        candidates = []
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        route_counts = self.diagnostics["route_counts"]
        for decision in decisions.values():
            route_counts[decision.state] = (
                int(route_counts.get(decision.state, 0)) + 1
            )
            if decision.actionable:
                family_counts[decision.state] = (
                    int(family_counts.get(decision.state, 0)) + 1
                )
                key = (
                    decision.state,
                    decision.symbol,
                    int(decision.episode_ts),
                )
                if key in self.used_episode_keys:
                    self.diagnostics[
                        "crosslead_used_episode_rejections"
                    ] += 1
                else:
                    candidates.append(decision)
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = (
                        int(reason_counts.get(reason, 0)) + 1
                    )

        self.diagnostics["crosslead_candidates"] += len(candidates)
        if not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return

        candidates.sort(
            key=lambda item: (-float(item.score), item.symbol, item.state)
        )
        winner = candidates[0]
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return

        key = (winner.state, winner.symbol, int(winner.episode_ts))
        self.used_episode_keys.add(key)
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-51-causal-crosslead",
                    "state_family": winner.state,
                    "leader_universe": "BTCUSDT+ETHUSDT",
                    "target_universe": "SOLUSDT+XRPUSDT",
                    "source_performance_used": False,
                    "training_contract": (
                        "target[t+1]~leader[t], completed-pairs-only, "
                        "two-half sign stability"
                    ),
                    "management": (
                        "predicted-next-bucket-objective-structural-stop-"
                        "five-minute-timeout"
                    ),
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
