"""NautilusTrader execution adapter for the corrected public MBE2 strategy."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import math

from router import FeatureObservation, MBE2_STATE, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    mbe_bucket_minutes: int = 5
    mbe_variant: str = "pair_both"
    mbe_rsi_period: int = 14
    mbe_tema_period: int = 9
    mbe_bb_period: int = 20
    mbe_source_effective_leverage: float = 6.46
    mbe_source_stoploss: float = 0.22
    mbe_trailing_positive: float = 0.015
    mbe_trailing_offset: float = 0.025
    mbe_emergency_target_fraction: float = 0.20
    mbe_roi_0: float = 0.079
    mbe_roi_15: float = 0.047
    mbe_roi_41: float = 0.032
    mbe_roi_114: float = 0.110
    mbe_roi_180: float = 0.007
    mbe_roi_420: float = 0.001


class Candidate35Strategy(_ExecutionShell):
    """One global account running the source's active RSI/TEMA policy."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {symbol: deque(self.bars[symbol], maxlen=6_000) for symbol in SYMBOLS}
        self.route_config = replace(
            self.route_config,
            picasso_bucket_minutes=int(config.mbe_bucket_minutes),
            picasso_precedence_mode=str(config.mbe_variant),
            picasso_rsi_long_period=int(config.mbe_rsi_period),
            picasso_bb_long_period=int(config.mbe_bb_period),
            picasso_bb_short_period=int(config.mbe_tema_period),
            picasso_source_effective_leverage=float(config.mbe_source_effective_leverage),
            picasso_source_stoploss=float(config.mbe_source_stoploss),
            picasso_trailing_positive=float(config.mbe_trailing_positive),
            picasso_trailing_offset=float(config.mbe_trailing_offset),
            picasso_emergency_target_fraction=float(config.mbe_emergency_target_fraction),
        )
        self._roi_schedule = (
            (0, float(config.mbe_roi_0)),
            (15, float(config.mbe_roi_15)),
            (41, float(config.mbe_roi_41)),
            (114, float(config.mbe_roi_114)),
            (180, float(config.mbe_roi_180)),
            (420, float(config.mbe_roi_420)),
        )
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self._trail_active = False
        self._trail_best: float | None = None
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "external_source": "remiotore/ccxt-freqtrade:strategies/myshortingstrategiembe2.py",
                "external_source_blob": "d312e07abc99ffd5631a992fc67a4e97a8768c0a",
                "mbe_variant": str(config.mbe_variant),
                "mbe_bucket_minutes": int(config.mbe_bucket_minutes),
                "source_signals_before_execution_filters": 0,
                "funding_runway_rejections": 0,
                "cooldown_rejections": 0,
                "used_episode_rejections": 0,
                "mbe_trailing_activations": 0,
                "mbe_trailing_exits": 0,
                "mbe_roi_exits": 0,
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
                "heikin_ashi_ohlc_overwrite_rejected": 1,
                "real_binance_ohlc_execution": 1,
                "dead_v2_buy_sell_columns_ignored": 1,
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
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES",
                )
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return

        bucket_minutes = int(self.route_config.picasso_bucket_minutes)
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % bucket_minutes != bucket_minutes - 1:
            return
        if any(len(self.bars[symbol]) < bucket_minutes * 140 for symbol in SYMBOLS):
            return

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
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
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
        unused.sort(key=lambda item: (-float(item.score), item.symbol, int(item.episode_ts)))
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
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before and self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-public-mbe2-corrected-execution",
                    "source_variant": str(self.route_config.picasso_precedence_mode),
                    "source_bucket_minutes": bucket_minutes,
                    "valid_real_ohlc_execution": True,
                }
            )

    def _roi_profit_ratio(self, elapsed_minutes: int) -> float:
        result = self._roi_schedule[0][1]
        for minute, value in self._roi_schedule:
            if elapsed_minutes >= minute:
                result = value
            else:
                break
        return float(result)

    def _scenario_leverage(self) -> float:
        scenario = self.current_scenario or {}
        diagnostics = scenario.get("diagnostics", {})
        if isinstance(diagnostics, dict):
            value = diagnostics.get("source_effective_leverage")
            try:
                number = float(value)
                if math.isfinite(number) and number > 0.0:
                    return number
            except (TypeError, ValueError):
                pass
        return max(float(self.config.mbe_source_effective_leverage), 1.0)

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") == MBE2_STATE:
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", 0.0))
            bar = self.bars[self.current_symbol][-1]
            leverage = self._scenario_leverage()
            if side in (-1, 1) and math.isfinite(entry) and entry > 0.0:
                activation = float(self.config.mbe_trailing_offset) / leverage
                distance = float(self.config.mbe_trailing_positive) / leverage

                # Conservative causal ordering: a trail activated by the current
                # minute cannot also be hit inside that already-completed minute.
                if self._trail_active and self._trail_best is not None:
                    if side > 0:
                        trailing_stop = self._trail_best * (1.0 - distance)
                        hit = float(bar.low) <= trailing_stop
                    else:
                        trailing_stop = self._trail_best * (1.0 + distance)
                        hit = float(bar.high) >= trailing_stop
                    if hit:
                        instrument_id = self.instrument_ids[self.current_symbol]
                        self.cancel_all_orders(instrument_id)
                        self.close_all_positions(instrument_id)
                        self.diagnostics["mbe_trailing_exits"] += 1
                        self._event(
                            "PUBLIC_MBE2_TRAILING_EXIT",
                            ts_event,
                            trailing_stop=trailing_stop,
                            best_price=self._trail_best,
                            source_leverage=leverage,
                            activation_fraction=activation,
                            trail_fraction=distance,
                        )
                        return

                elapsed = max(0, self.minute_index - self.position_open_minute)
                roi_profit_ratio = self._roi_profit_ratio(elapsed)
                if roi_profit_ratio > 0.0:
                    roi_fraction = roi_profit_ratio / leverage
                    roi_target = entry * (1.0 + side * roi_fraction)
                    hit = (
                        float(bar.high) >= roi_target
                        if side > 0
                        else float(bar.low) <= roi_target
                    )
                    if hit:
                        instrument_id = self.instrument_ids[self.current_symbol]
                        self.cancel_all_orders(instrument_id)
                        self.close_all_positions(instrument_id)
                        self.diagnostics["mbe_roi_exits"] += 1
                        self._event(
                            "PUBLIC_MBE2_ROI_EXIT",
                            ts_event,
                            elapsed_minutes=elapsed,
                            source_roi_profit_ratio=roi_profit_ratio,
                            underlying_roi_fraction=roi_fraction,
                            roi_target=roi_target,
                            source_leverage=leverage,
                        )
                        return

                favourable = float(bar.high) if side > 0 else float(bar.low)
                move = side * (favourable - entry) / entry
                if not self._trail_active and move >= activation:
                    self._trail_active = True
                    self._trail_best = favourable
                    self.diagnostics["mbe_trailing_activations"] += 1
                    self._event(
                        "PUBLIC_MBE2_TRAILING_ACTIVATED",
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
