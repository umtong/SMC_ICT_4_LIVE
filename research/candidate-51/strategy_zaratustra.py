"""NautilusTrader execution adapter for the public ZaratustraV5 policy."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from router import FeatureObservation, ZARATUSTRA_STATE, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    zara_rsi_period: int = 14
    zara_di_period: int = 14
    zara_bb_period: int = 20
    zara_rsi_level: float = 50.0
    zara_di_level: float = 25.0
    zara_source_leverage: float = 10.0
    zara_source_stoploss: float = 0.296
    zara_trailing_positive: float = 0.013
    zara_trailing_offset: float = 0.071
    zara_emergency_target_fraction: float = 0.10


class Candidate35Strategy(_ExecutionShell):
    """One-slot four-symbol port of ZaratustraV5 with independent episodes."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            zara_rsi_period=int(config.zara_rsi_period),
            zara_di_period=int(config.zara_di_period),
            zara_bb_period=int(config.zara_bb_period),
            zara_rsi_level=float(config.zara_rsi_level),
            zara_di_level=float(config.zara_di_level),
            zara_source_leverage=float(config.zara_source_leverage),
            zara_source_stoploss=float(config.zara_source_stoploss),
            zara_trailing_positive=float(config.zara_trailing_positive),
            zara_trailing_offset=float(config.zara_trailing_offset),
            zara_emergency_target_fraction=float(config.zara_emergency_target_fraction),
        )
        self.diagnostics.setdefault("source_signals_before_execution_filters", 0)
        self.diagnostics.setdefault("funding_runway_rejections", 0)
        self.diagnostics.setdefault("cooldown_rejections", 0)
        self.diagnostics.setdefault("used_episode_rejections", 0)
        self.diagnostics.setdefault("zara_trailing_activations", 0)
        self.diagnostics.setdefault("zara_trailing_exits", 0)
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self._trail_active = False
        self._trail_best: float | None = None

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
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES",
                )
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % self.route_config.bucket_minutes != self.route_config.bucket_minutes - 1:
            return
        required_bars = 30 * max(
            self.route_config.zara_rsi_period + 3,
            self.route_config.zara_di_period + 3,
            self.route_config.zara_bb_period + 2,
        )
        if any(len(self.bars[symbol]) < required_bars for symbol in SYMBOLS):
            return

        # The public source is price-only.  Mark the just-closed candle as the
        # observation; no unrelated stale feature may suppress its signal.
        features = {
            symbol: FeatureObservation(int(self.bars[symbol][-1].ts_event), ready=True)
            for symbol in SYMBOLS
        }
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
        self._trail_active = False
        self._trail_best = None
        self._submit_decision(winner, ts_event)

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") == ZARATUSTRA_STATE:
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", 0.0))
            bar = self.bars[self.current_symbol][-1]
            if side in (-1, 1) and entry > 0.0:
                activation_fraction = (
                    self.route_config.zara_trailing_offset
                    / self.route_config.zara_source_leverage
                )
                trail_fraction = (
                    self.route_config.zara_trailing_positive
                    / self.route_config.zara_source_leverage
                )
                # Test an already-active stop against this minute first, then
                # update the favourable extreme.  This avoids same-bar path
                # assumptions while using 1m detail under the 5m source policy.
                if self._trail_active and self._trail_best is not None:
                    if side > 0:
                        stop = self._trail_best * (1.0 - trail_fraction)
                        hit = float(bar.low) <= stop
                    else:
                        stop = self._trail_best * (1.0 + trail_fraction)
                        hit = float(bar.high) >= stop
                    if hit:
                        instrument_id = self.instrument_ids[self.current_symbol]
                        self.cancel_all_orders(instrument_id)
                        self.close_all_positions(instrument_id)
                        self.diagnostics["zara_trailing_exits"] += 1
                        self._event(
                            "PUBLIC_ZARATUSTRA_V5_TRAILING_EXIT",
                            ts_event,
                            trailing_stop=stop,
                            best_price=self._trail_best,
                            activation_fraction=activation_fraction,
                            trail_fraction=trail_fraction,
                        )
                        return
                favourable = float(bar.high) if side > 0 else float(bar.low)
                move = side * (favourable - entry) / entry
                if not self._trail_active and move >= activation_fraction:
                    self._trail_active = True
                    self._trail_best = favourable
                    self.diagnostics["zara_trailing_activations"] += 1
                    self._event(
                        "PUBLIC_ZARATUSTRA_V5_TRAILING_ACTIVATED",
                        ts_event,
                        favourable_price=favourable,
                        activation_fraction=activation_fraction,
                    )
                elif self._trail_active:
                    assert self._trail_best is not None
                    if side > 0:
                        self._trail_best = max(self._trail_best, favourable)
                    else:
                        self._trail_best = min(self._trail_best, favourable)
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._trail_active = False
        self._trail_best = None


__all__ = ["Candidate35Config", "Candidate35Strategy"]
