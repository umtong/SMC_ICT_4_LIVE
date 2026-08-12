"""NautilusTrader execution for causal intraday jump reversion."""
from __future__ import annotations

from collections import deque
from dataclasses import replace

from router import FeatureObservation, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    jump_timeframe_minutes: int = 120
    jump_threshold_sigma: float = 2.0
    jump_volatility_window: int = 36
    jump_min_absolute_return: float = 0.0
    jump_terminal_atr_period: int = 14
    jump_stop_atr_multiple: float = 1.0
    jump_min_stop_fraction: float = 0.0015
    jump_emergency_target_fraction: float = 0.20


class Candidate35Strategy(_ExecutionShell):
    """One global slot; source exit is exactly one equal-length period later."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        required = (
            int(config.jump_timeframe_minutes)
            * (int(config.jump_volatility_window) + 4)
            + 200
        )
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=max(2_000, required))
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            jump_timeframe_minutes=int(config.jump_timeframe_minutes),
            jump_threshold_sigma=float(config.jump_threshold_sigma),
            jump_volatility_window=int(config.jump_volatility_window),
            jump_min_absolute_return=float(config.jump_min_absolute_return),
            jump_terminal_atr_period=int(config.jump_terminal_atr_period),
            jump_stop_atr_multiple=float(config.jump_stop_atr_multiple),
            jump_min_stop_fraction=float(config.jump_min_stop_fraction),
            jump_emergency_target_fraction=float(config.jump_emergency_target_fraction),
        )
        self.used_episode_keys: set[tuple[str, int]] = set()
        self.diagnostics.update(
            {
                "external_source": "De Nicola 2021 On the Intraday Behavior of Bitcoin",
                "jump_timeframe_minutes": int(config.jump_timeframe_minutes),
                "jump_threshold_sigma": float(config.jump_threshold_sigma),
                "jump_volatility_window": int(config.jump_volatility_window),
                "jump_source_candidates": 0,
                "jump_used_episode_rejections": 0,
                "jump_time_exit_contract": int(config.jump_timeframe_minutes),
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

        timeframe = int(self.route_config.jump_timeframe_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % timeframe != timeframe - 1:
            return
        required = timeframe * (int(self.route_config.jump_volatility_window) + 2)
        if any(len(self.bars[symbol]) < required for symbol in SYMBOLS):
            return

        observations = {
            symbol: FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol])
                for symbol in SYMBOLS
            },
            features_by_symbol=observations,
            config=self.route_config,
        )
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        candidates = []
        for decision in decisions.values():
            route_counts = self.diagnostics["route_counts"]
            route_counts[decision.state] = (
                int(route_counts.get(decision.state, 0)) + 1
            )
            if decision.actionable:
                family_counts[decision.state] = (
                    int(family_counts.get(decision.state, 0)) + 1
                )
                key = (decision.symbol, int(decision.episode_ts))
                if key in self.used_episode_keys:
                    self.diagnostics["jump_used_episode_rejections"] += 1
                else:
                    candidates.append(decision)
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = (
                        int(reason_counts.get(reason, 0)) + 1
                    )
        self.diagnostics["jump_source_candidates"] += len(candidates)
        if not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return
        candidates.sort(key=lambda item: (-item.score, item.symbol))
        winner = candidates[0]
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return
        self.used_episode_keys.add((winner.symbol, int(winner.episode_ts)))
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-51-academic-jump-reversion",
                    "source_holding_minutes": timeframe,
                    "source_jump_threshold_sigma": float(
                        self.route_config.jump_threshold_sigma
                    ),
                    "source_volatility_window": int(
                        self.route_config.jump_volatility_window
                    ),
                    "management": "source-one-equal-time-unit-plus-hard-stop",
                    "risk_geometry": "terminal-minute-extreme-plus-causal-atr-buffer",
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
