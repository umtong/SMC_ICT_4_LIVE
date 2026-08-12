"""NautilusTrader execution adapter for public ``ZaratustraV5``.

The inherited execution shell supplies the four-symbol continuous account,
exchange contracts, latency, fees, adverse slippage, liquidation accounting,
one global entry/position slot, and exact current-NAV 3% planned-loss sizing.
This module adds only the source's multi-timeframe entry cadence and 10x-normalized
stop/trailing management.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import math

from router import FeatureObservation, ZARATUSTRA_STATE, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    zaratustra_variant: str = "level_both"
    zaratustra_startup_30m_candles: int = 30
    zaratustra_rsi_period: int = 14
    zaratustra_di_period: int = 14
    zaratustra_bb_period: int = 20
    zaratustra_source_leverage: float = 10.0
    zaratustra_source_stoploss: float = 0.296
    zaratustra_trailing_positive: float = 0.013
    zaratustra_trailing_offset: float = 0.071
    zaratustra_emergency_target_fraction: float = 0.50


class Candidate35Strategy(_ExecutionShell):
    """One-account source-faithful ZaratustraV5 execution policy."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=6_000)
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            picasso_bucket_minutes=5,
            picasso_precedence_mode=str(config.zaratustra_variant),
            picasso_adx_period=int(config.zaratustra_di_period),
            picasso_rsi_long_period=int(config.zaratustra_rsi_period),
            picasso_bb_long_period=int(config.zaratustra_bb_period),
            picasso_source_effective_leverage=float(
                config.zaratustra_source_leverage
            ),
            picasso_source_stoploss=float(config.zaratustra_source_stoploss),
            picasso_trailing_positive=float(
                config.zaratustra_trailing_positive
            ),
            picasso_trailing_offset=float(
                config.zaratustra_trailing_offset
            ),
            picasso_emergency_target_fraction=float(
                config.zaratustra_emergency_target_fraction
            ),
        )
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self._trail_active = False
        self._trail_best: float | None = None
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "external_source": (
                    "remiotore/ccxt-freqtrade:strategies/ZaratustraV5.py"
                ),
                "external_source_blob": (
                    "af0ba19353574b7bb60a983402014375917e1f15"
                ),
                "zaratustra_variant": str(config.zaratustra_variant),
                "source_timeframes_minutes": [5, 15, 30],
                "source_signals_before_execution_filters": 0,
                "funding_runway_rejections": 0,
                "cooldown_rejections": 0,
                "used_episode_rejections": 0,
                "zaratustra_trailing_activations": 0,
                "zaratustra_trailing_exits": 0,
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
                "complete_informative_candles_only": 1,
                "one_minute_trailing_detail": 1,
                "same_minute_trail_activation_and_hit_allowed": 0,
                "real_binance_ohlc_execution": 1,
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

        moment = datetime.fromtimestamp(
            ts_event / 1_000_000_000, tz=timezone.utc
        )
        if moment.minute % 5 != 4:
            return
        required_minutes = (
            int(self.config.zaratustra_startup_30m_candles) * 30
        )
        if any(
            len(self.bars[symbol]) < required_minutes for symbol in SYMBOLS
        ):
            return

        features = {
            symbol: FeatureObservation(
                int(self.bars[symbol][-1].ts_event), ready=True
            )
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS
            },
            features_by_symbol=features,
            config=self.route_config,
        )
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                family_counts[decision.state] = (
                    int(family_counts.get(decision.state, 0)) + 1
                )
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = int(
                        reason_counts.get(reason, 0)
                    ) + 1

        actionable = [
            decision for decision in decisions.values() if decision.actionable
        ]
        self.diagnostics["source_signals_before_execution_filters"] += len(
            actionable
        )
        unused = []
        for decision in actionable:
            key = (decision.symbol, decision.state, int(decision.episode_ts))
            if key in self.used_episode_keys:
                self.diagnostics["used_episode_rejections"] += 1
            else:
                unused.append(decision)
        unused.sort(
            key=lambda item: (
                -float(item.score),
                item.symbol,
                int(item.episode_ts),
            )
        )
        winner = unused[0] if unused else None
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if (
            self.minute_index - self.last_entry_minute
            < self.config.cooldown_minutes
        ):
            self.diagnostics["cooldown_rejections"] += 1
            return

        self.used_episode_keys.add(
            (winner.symbol, winner.state, int(winner.episode_ts))
        )
        self._trail_active = False
        self._trail_best = None
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-public-zaratustra-v5",
                    "source_variant": str(
                        self.route_config.picasso_precedence_mode
                    ),
                    "source_timeframes_minutes": [5, 15, 30],
                    "source_entry_is_level": str(
                        self.route_config.picasso_precedence_mode
                    ).startswith("level_"),
                    "source_has_no_roi_or_exit_signal": True,
                    "valid_real_ohlc_execution": True,
                }
            )

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") == ZARATUSTRA_STATE:
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", 0.0))
            bar = self.bars[self.current_symbol][-1]
            leverage = max(
                float(self.config.zaratustra_source_leverage), 1e-12
            )
            if side in (-1, 1) and math.isfinite(entry) and entry > 0.0:
                activation = (
                    float(self.config.zaratustra_trailing_offset) / leverage
                )
                distance = (
                    float(self.config.zaratustra_trailing_positive) / leverage
                )

                # The source fixed backtest checks trailing only on 5m OHLC.
                # Candidate 55 instead uses 1m bars and deliberately forbids a
                # trail activated by the current completed minute from also
                # being hit somewhere earlier inside that same minute.
                if self._trail_active and self._trail_best is not None:
                    if side > 0:
                        trailing_stop = self._trail_best * (1.0 - distance)
                        hit = float(bar.low) <= trailing_stop
                    else:
                        trailing_stop = self._trail_best * (1.0 + distance)
                        hit = float(bar.high) >= trailing_stop
                    if hit:
                        instrument_id = self.instrument_ids[
                            self.current_symbol
                        ]
                        self.cancel_all_orders(instrument_id)
                        self.close_all_positions(instrument_id)
                        self.diagnostics["zaratustra_trailing_exits"] += 1
                        self._event(
                            "PUBLIC_ZARATUSTRA_TRAILING_EXIT",
                            ts_event,
                            trailing_stop=trailing_stop,
                            best_price=self._trail_best,
                            source_leverage=leverage,
                            activation_fraction=activation,
                            trail_fraction=distance,
                        )
                        return

                favourable = (
                    float(bar.high) if side > 0 else float(bar.low)
                )
                move = side * (favourable - entry) / entry
                if not self._trail_active and move >= activation:
                    self._trail_active = True
                    self._trail_best = favourable
                    self.diagnostics[
                        "zaratustra_trailing_activations"
                    ] += 1
                    self._event(
                        "PUBLIC_ZARATUSTRA_TRAILING_ACTIVATED",
                        ts_event,
                        favourable_price=favourable,
                        source_leverage=leverage,
                        activation_fraction=activation,
                    )
                elif self._trail_active:
                    assert self._trail_best is not None
                    self._trail_best = (
                        max(self._trail_best, favourable)
                        if side > 0
                        else min(self._trail_best, favourable)
                    )
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._trail_active = False
        self._trail_best = None


__all__ = ["Candidate35Config", "Candidate35Strategy"]
