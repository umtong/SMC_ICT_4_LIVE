"""NautilusTrader execution adapter for the public Keltrader squeeze core."""
from __future__ import annotations

from collections import deque
from dataclasses import replace

from router import FeatureObservation, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    keltrader_signal_minutes: int = 240
    keltrader_atr_minutes: int = 60
    keltrader_bb_period: int = 19
    keltrader_bb_std: float = 2.47
    keltrader_kc_period: int = 17
    keltrader_kc_atr_mult: float = 2.38
    keltrader_indicator_atr_period: int = 14
    keltrader_momentum_period: int = 12
    keltrader_rsi_period: int = 14
    keltrader_rsi_overbought: float = 70.0
    keltrader_rsi_oversold: float = 30.0
    keltrader_volume_period: int = 20
    keltrader_min_volume_ratio: float = 1.0
    keltrader_min_squeeze_bars: int = 2
    keltrader_require_band_break: bool = False
    keltrader_stop_atr_mult: float = 3.45
    keltrader_target_atr_mult: float = 4.0


class Candidate35Strategy(_ExecutionShell):
    """One global slot, source bracket and day-trading time exit."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        required_minutes = max(
            int(config.keltrader_signal_minutes)
            * (
                int(config.keltrader_bb_period)
                + int(config.keltrader_momentum_period)
                + 10
            ),
            int(config.keltrader_atr_minutes)
            * (int(config.keltrader_indicator_atr_period) + 10),
        )
        self.bars = {
            symbol: deque(
                self.bars[symbol],
                maxlen=max(4_000, required_minutes),
            )
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            keltrader_signal_minutes=int(config.keltrader_signal_minutes),
            keltrader_atr_minutes=int(config.keltrader_atr_minutes),
            keltrader_bb_period=int(config.keltrader_bb_period),
            keltrader_bb_std=float(config.keltrader_bb_std),
            keltrader_kc_period=int(config.keltrader_kc_period),
            keltrader_kc_atr_mult=float(config.keltrader_kc_atr_mult),
            keltrader_indicator_atr_period=int(
                config.keltrader_indicator_atr_period
            ),
            keltrader_momentum_period=int(config.keltrader_momentum_period),
            keltrader_rsi_period=int(config.keltrader_rsi_period),
            keltrader_rsi_overbought=float(config.keltrader_rsi_overbought),
            keltrader_rsi_oversold=float(config.keltrader_rsi_oversold),
            keltrader_volume_period=int(config.keltrader_volume_period),
            keltrader_min_volume_ratio=float(
                config.keltrader_min_volume_ratio
            ),
            keltrader_min_squeeze_bars=int(
                config.keltrader_min_squeeze_bars
            ),
            keltrader_require_band_break=bool(
                config.keltrader_require_band_break
            ),
            keltrader_stop_atr_mult=float(config.keltrader_stop_atr_mult),
            keltrader_target_atr_mult=float(
                config.keltrader_target_atr_mult
            ),
        )
        self.used_episode_keys: set[tuple[str, int]] = set()
        self.diagnostics.update(
            {
                "external_source": "jicheolha/crypto-trading-bot",
                "keltrader_public_core_only": True,
                "keltrader_signal_minutes": int(
                    config.keltrader_signal_minutes
                ),
                "keltrader_atr_minutes": int(config.keltrader_atr_minutes),
                "keltrader_release_candidates": 0,
                "keltrader_used_episode_rejections": 0,
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

        signal_minutes = int(self.route_config.keltrader_signal_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % signal_minutes != signal_minutes - 1:
            return
        required_minutes = signal_minutes * max(
            int(self.route_config.keltrader_bb_period)
            + int(self.route_config.keltrader_momentum_period)
            + 3,
            int(self.route_config.keltrader_kc_period)
            + int(self.route_config.keltrader_indicator_atr_period)
            + 3,
            int(self.route_config.keltrader_volume_period) + 3,
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
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = (
                    int(family_counts.get(decision.state, 0)) + 1
                )
                key = (decision.symbol, int(decision.episode_ts))
                if key in self.used_episode_keys:
                    self.diagnostics[
                        "keltrader_used_episode_rejections"
                    ] += 1
                else:
                    candidates.append(decision)
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = (
                        int(reason_counts.get(reason, 0)) + 1
                    )
        self.diagnostics["keltrader_release_candidates"] += len(candidates)
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
                    "candidate": "candidate-51-public-keltrader-core",
                    "source_signal_minutes": signal_minutes,
                    "source_atr_minutes": int(
                        self.route_config.keltrader_atr_minutes
                    ),
                    "source_public_parameters": (
                        "BB19x2.47-KC17x2.38-squeeze2-SL3.45ATR-TP4ATR"
                    ),
                    "redacted_signal_rules_used": False,
                    "management": "source-bracket-plus-daytrading-timeout",
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
