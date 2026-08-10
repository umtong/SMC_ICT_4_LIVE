"""NautilusTrader execution adapter for the public ``TheForce`` strategy.

The common shell owns the continuous four-symbol account, matching, realistic
fees/slippage, liquidation accounting, one global slot and current-NAV 3%
planned-loss sizing. This module adds only the source's completed-15m entry,
fixed stop, ROI ladder and completed-15m sell signal, plus the project day-trade
horizon/funding safety overlays.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import math

from router import (
    FORCE_STATE,
    FeatureObservation,
    route_universe,
    source_flags_for_bars,
)
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    force_startup_15m_candles: int = 30
    force_stop_fraction: float = 0.015
    force_roi_0: float = 0.012
    force_roi_15: float = 0.010
    force_roi_30: float = 0.005


class Candidate35Strategy(_ExecutionShell):
    """One-account exact source-long TheForce policy."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        if int(config.force_startup_15m_candles) != 30:
            raise ValueError("TheForce source startup must remain 30 candles")
        if abs(float(config.force_stop_fraction) - 0.015) > 1e-12:
            raise ValueError("TheForce source stop must remain 1.5%")
        if (
            abs(float(config.force_roi_0) - 0.012) > 1e-12
            or abs(float(config.force_roi_15) - 0.010) > 1e-12
            or abs(float(config.force_roi_30) - 0.005) > 1e-12
        ):
            raise ValueError("TheForce source ROI ladder changed")
        required_minutes = int(config.force_startup_15m_candles) * 15 + 120
        self.bars = {
            symbol: deque(
                self.bars[symbol], maxlen=max(12_500, required_minutes)
            )
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            picasso_bucket_minutes=15,
            picasso_precedence_mode="source_long",
            picasso_source_stoploss=float(config.force_stop_fraction),
            picasso_emergency_target_fraction=float(config.force_roi_0),
        )
        self.used_episode_keys: set[tuple[str, int]] = set()
        self._force_mfe_fraction = 0.0
        self._force_mae_fraction = 0.0
        self.diagnostics.update(
            {
                "candidate": "candidate-57",
                "family": "PUBLIC_THEFORCE_15M_MOMENTUM",
                "external_source": "PeetCrypto/freqtrade-stuff:TheForce.py",
                "external_source_blob": (
                    "af1c8ac097afda8caa620fe3539a62f96455614c"
                ),
                "source_side": "long",
                "source_timeframe_minutes": 15,
                "source_startup_candles": 30,
                "source_stop_fraction": float(config.force_stop_fraction),
                "source_roi_ladder": {
                    "0": float(config.force_roi_0),
                    "15": float(config.force_roi_15),
                    "30": float(config.force_roi_30),
                },
                "source_signals_before_execution_filters": 0,
                "force_collision_boundaries": 0,
                "force_competing_candidates": 0,
                "force_roi_exits": 0,
                "force_sell_signal_exits": 0,
                "funding_runway_rejections": 0,
                "cooldown_rejections": 0,
                "used_episode_rejections": 0,
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
                "complete_15m_candles_only": 1,
                "source_entry_level_semantics": 1,
                "source_short_symmetry_added": 0,
                "project_daytrade_overlay_max_hold_minutes": int(
                    config.max_hold_minutes
                ),
                "project_funding_safety_overlay": 1,
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
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1
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
        if moment.minute % 15 != 14:
            return
        required_minutes = int(self.config.force_startup_15m_candles) * 15
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
            key = (decision.symbol, int(decision.episode_ts))
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
        if len(unused) > 1:
            self.diagnostics["force_collision_boundaries"] += 1
            self.diagnostics["force_competing_candidates"] += len(unused) - 1
        winner = unused[0] if unused else None
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if (
            self.minute_index - self.last_entry_minute
            < int(self.config.cooldown_minutes)
        ):
            self.diagnostics["cooldown_rejections"] += 1
            return

        self.used_episode_keys.add((winner.symbol, int(winner.episode_ts)))
        self._force_mfe_fraction = 0.0
        self._force_mae_fraction = 0.0
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-57-public-theforce",
                    "family": "PUBLIC_THEFORCE_15M_MOMENTUM",
                    "source_file": "TheForce.py",
                    "source_blob": (
                        "af1c8ac097afda8caa620fe3539a62f96455614c"
                    ),
                    "source_timeframe_minutes": 15,
                    "source_side": "long",
                    "source_stop_fraction": float(
                        self.config.force_stop_fraction
                    ),
                    "source_roi_ladder": {
                        "0": float(self.config.force_roi_0),
                        "15": float(self.config.force_roi_15),
                        "30": float(self.config.force_roi_30),
                    },
                    "source_sell_signal_enabled": True,
                    "source_short_symmetry_added": False,
                    "project_daytrade_overlay_max_hold_minutes": int(
                        self.config.max_hold_minutes
                    ),
                    "force_mfe_underlying_fraction": 0.0,
                    "force_mae_underlying_fraction": 0.0,
                }
            )

    def _close_source_position(
        self, event_type: str, ts_event: int, **details: float | int | str
    ) -> None:
        if self.current_symbol is None:
            return
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event(event_type, ts_event, **details)

    def _roi_threshold(self, age_minutes: int) -> float:
        if age_minutes >= 30:
            return float(self.config.force_roi_30)
        if age_minutes >= 15:
            return float(self.config.force_roi_15)
        return float(self.config.force_roi_0)

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") == FORCE_STATE:
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", 0.0))
            bar = self.bars[self.current_symbol][-1]
            if side == 1 and math.isfinite(entry) and entry > 0.0:
                favourable_move = (float(bar.high) - entry) / entry
                adverse_move = (float(bar.low) - entry) / entry
                self._force_mfe_fraction = max(
                    self._force_mfe_fraction, favourable_move
                )
                self._force_mae_fraction = min(
                    self._force_mae_fraction, adverse_move
                )
                if self.current_scenario is not None:
                    self.current_scenario[
                        "force_mfe_underlying_fraction"
                    ] = self._force_mfe_fraction
                    self.current_scenario[
                        "force_mae_underlying_fraction"
                    ] = self._force_mae_fraction

                moment = datetime.fromtimestamp(
                    ts_event / 1_000_000_000, tz=timezone.utc
                )
                if moment.minute % 15 == 14:
                    age = max(
                        0, self.minute_index - self.position_open_minute
                    )
                    _, exit_flag, diagnostics = source_flags_for_bars(
                        tuple(self.bars[self.current_symbol])
                    )
                    if exit_flag:
                        self._close_source_position(
                            "PUBLIC_THEFORCE_SELL_SIGNAL_EXIT",
                            ts_event,
                            age_minutes=age,
                            fastk=float(diagnostics.get("fastk", math.nan)),
                            fastd=float(diagnostics.get("fastd", math.nan)),
                            macd=float(diagnostics.get("macd", math.nan)),
                        )
                        self.diagnostics["force_sell_signal_exits"] += 1
                        return
                    profit = (float(bar.close) - entry) / entry
                    roi = self._roi_threshold(age)
                    if profit >= roi:
                        self._close_source_position(
                            "PUBLIC_THEFORCE_ROI_EXIT",
                            ts_event,
                            age_minutes=age,
                            source_profit_fraction=profit,
                            roi_threshold=roi,
                        )
                        self.diagnostics["force_roi_exits"] += 1
                        return
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._force_mfe_fraction = 0.0
        self._force_mae_fraction = 0.0


__all__ = ["Candidate35Config", "Candidate35Strategy"]
