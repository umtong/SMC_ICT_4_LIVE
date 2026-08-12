"""NautilusTrader execution adapter for the public Picasso RSI/BB/MACD bot."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import math

from router import FeatureObservation, PICASSO_STATE, _aggregate_complete, _atr, _ema, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    picasso_bucket_minutes: int = 15
    picasso_precedence_mode: str = "exact"
    picasso_adx_period: int = 14
    picasso_rsi_long_period: int = 22
    picasso_rsi_short_period: int = 17
    picasso_bb_long_period: int = 16
    picasso_bb_short_period: int = 20
    picasso_volume_long_period: int = 38
    picasso_volume_short_period: int = 20
    picasso_adx_long_min_1: float = 5.7
    picasso_adx_long_max_1: float = 6.5
    picasso_adx_long_min_2: float = 20.9
    picasso_adx_long_max_2: float = 50.7
    picasso_adx_short_min_1: float = 9.9
    picasso_adx_short_max_1: float = 21.4
    picasso_adx_short_min_2: float = 30.3
    picasso_adx_short_max_2: float = 50.8
    picasso_source_effective_leverage: float = 5.0
    picasso_source_stoploss: float = 0.317
    picasso_trailing_positive: float = 0.012
    picasso_trailing_offset: float = 0.030
    picasso_emergency_target_fraction: float = 0.10
    picasso_roi_0: float = 0.184
    picasso_roi_416: float = 0.140
    picasso_roi_933: float = 0.073
    picasso_roi_1982: float = 0.0
    picasso_atr_period: int = 20
    picasso_ema_long_exit: int = 91
    picasso_ema_short_exit: int = 147
    picasso_atr_long_multiple: float = 3.8
    picasso_atr_short_multiple: float = 5.0
    picasso_volume_long_exit: int = 19
    picasso_volume_short_exit: int = 41


class Candidate35Strategy(_ExecutionShell):
    """One global slot with exact or corrected public source precedence."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {symbol: deque(self.bars[symbol], maxlen=6_000) for symbol in SYMBOLS}
        self.route_config = replace(
            self.route_config,
            picasso_bucket_minutes=int(config.picasso_bucket_minutes),
            picasso_precedence_mode=str(config.picasso_precedence_mode),
            picasso_adx_period=int(config.picasso_adx_period),
            picasso_rsi_long_period=int(config.picasso_rsi_long_period),
            picasso_rsi_short_period=int(config.picasso_rsi_short_period),
            picasso_bb_long_period=int(config.picasso_bb_long_period),
            picasso_bb_short_period=int(config.picasso_bb_short_period),
            picasso_volume_long_period=int(config.picasso_volume_long_period),
            picasso_volume_short_period=int(config.picasso_volume_short_period),
            picasso_adx_long_min_1=float(config.picasso_adx_long_min_1),
            picasso_adx_long_max_1=float(config.picasso_adx_long_max_1),
            picasso_adx_long_min_2=float(config.picasso_adx_long_min_2),
            picasso_adx_long_max_2=float(config.picasso_adx_long_max_2),
            picasso_adx_short_min_1=float(config.picasso_adx_short_min_1),
            picasso_adx_short_max_1=float(config.picasso_adx_short_max_1),
            picasso_adx_short_min_2=float(config.picasso_adx_short_min_2),
            picasso_adx_short_max_2=float(config.picasso_adx_short_max_2),
            picasso_source_effective_leverage=float(config.picasso_source_effective_leverage),
            picasso_source_stoploss=float(config.picasso_source_stoploss),
            picasso_trailing_positive=float(config.picasso_trailing_positive),
            picasso_trailing_offset=float(config.picasso_trailing_offset),
            picasso_emergency_target_fraction=float(config.picasso_emergency_target_fraction),
        )
        self._roi_schedule = (
            (0, float(config.picasso_roi_0)),
            (416, float(config.picasso_roi_416)),
            (933, float(config.picasso_roi_933)),
            (1982, float(config.picasso_roi_1982)),
        )
        self.diagnostics.update({
            "external_source": "syuraj/freq-test:picasso_RSI_BB_MACD_Dec_2023_15m_3_rls.py",
            "picasso_precedence_mode": str(config.picasso_precedence_mode),
            "picasso_bucket_minutes": int(config.picasso_bucket_minutes),
            "source_signals_before_execution_filters": 0,
            "funding_runway_rejections": 0,
            "cooldown_rejections": 0,
            "used_episode_rejections": 0,
            "picasso_trailing_activations": 0,
            "picasso_trailing_exits": 0,
            "picasso_roi_exits": 0,
            "picasso_source_signal_exits": 0,
            "unresolved_reason_counts": {},
            "actionable_family_counts": {},
        })
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self._trail_active = False
        self._trail_best: float | None = None

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        open_symbols = [symbol for symbol in SYMBOLS if not self.portfolio.is_flat(self.instrument_ids[symbol])]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]), len(open_symbols))
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
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1)
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event("ENTRY_EXPIRED", ts_event, reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES")
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        bucket_minutes = int(self.route_config.picasso_bucket_minutes)
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % bucket_minutes != bucket_minutes - 1:
            return
        required_candles = max(65, self.route_config.picasso_volume_long_period + 25,
            self.route_config.picasso_volume_short_period + 25, self.route_config.picasso_adx_period * 2 + 8)
        if any(len(self.bars[symbol]) < bucket_minutes * required_candles for symbol in SYMBOLS):
            return
        features = {symbol: FeatureObservation(int(self.bars[symbol][-1].ts_event), ready=True) for symbol in SYMBOLS}
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features_by_symbol=features, config=self.route_config)
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
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before and self.current_scenario is not None:
            self.current_scenario.update({
                "candidate": "candidate-51-public-picasso",
                "source_precedence_mode": str(self.route_config.picasso_precedence_mode),
                "source_bucket_minutes": bucket_minutes,
            })

    def _roi_profit_ratio(self, elapsed_minutes: int) -> float:
        result = self._roi_schedule[0][1]
        for minute, value in self._roi_schedule:
            if elapsed_minutes >= minute:
                result = value
            else:
                break
        return result

    def _source_exit_signal(self) -> tuple[bool, dict[str, float | int]]:
        if self.current_symbol is None or self.current_scenario is None:
            return False, {}
        side = int(self.current_scenario.get("side", 0))
        candles = _aggregate_complete(tuple(self.bars[self.current_symbol]), int(self.route_config.picasso_bucket_minutes))
        required = max(int(self.config.picasso_ema_short_exit) + 2,
            int(self.config.picasso_ema_long_exit) + 2, int(self.config.picasso_volume_short_exit) + 2,
            int(self.config.picasso_volume_long_exit) + 2, int(self.config.picasso_atr_period) + 2)
        if len(candles) < required:
            return False, {}
        closes = [float(candle.close) for candle in candles]
        volumes = [float(candle.volume) for candle in candles]
        ema_l = _ema(closes, int(self.config.picasso_ema_long_exit))[-1]
        ema_s = _ema(closes, int(self.config.picasso_ema_short_exit))[-1]
        atr = _atr(candles, int(self.config.picasso_atr_period))[-1]
        long_period, short_period = int(self.config.picasso_volume_long_exit), int(self.config.picasso_volume_short_exit)
        volume_long = sum(volumes[-long_period-1:-1]) / long_period
        volume_short = sum(volumes[-short_period-1:-1]) / short_period
        close, volume = closes[-1], volumes[-1]
        if not all(math.isfinite(value) for value in (ema_l, ema_s, atr)):
            return False, {}
        long_exit = side > 0 and close < ema_l - float(self.config.picasso_atr_long_multiple) * atr and volume > volume_long
        short_exit = side < 0 and close > ema_s + float(self.config.picasso_atr_short_multiple) * atr and volume > volume_short
        return bool(long_exit or short_exit), {
            "side": side, "close": close, "volume": volume, "ema_long": float(ema_l),
            "ema_short": float(ema_s), "atr": float(atr),
            "volume_mean_long_exit": volume_long, "volume_mean_short_exit": volume_short,
        }

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") == PICASSO_STATE:
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", 0.0))
            bar = self.bars[self.current_symbol][-1]
            leverage = max(float(self.route_config.picasso_source_effective_leverage), 1e-12)
            if side in (-1, 1) and math.isfinite(entry) and entry > 0.0:
                activation = float(self.route_config.picasso_trailing_offset) / leverage
                distance = float(self.route_config.picasso_trailing_positive) / leverage
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
                        self.diagnostics["picasso_trailing_exits"] += 1
                        self._event("PUBLIC_PICASSO_TRAILING_EXIT", ts_event,
                            trailing_stop=trailing_stop, best_price=self._trail_best,
                            activation_fraction=activation, trail_fraction=distance)
                        return
                elapsed = max(0, self.minute_index - self.position_open_minute)
                roi_profit_ratio = self._roi_profit_ratio(elapsed)
                if roi_profit_ratio > 0.0:
                    roi_fraction = roi_profit_ratio / leverage
                    roi_target = entry * (1.0 + side * roi_fraction)
                    hit = float(bar.high) >= roi_target if side > 0 else float(bar.low) <= roi_target
                    if hit:
                        instrument_id = self.instrument_ids[self.current_symbol]
                        self.cancel_all_orders(instrument_id)
                        self.close_all_positions(instrument_id)
                        self.diagnostics["picasso_roi_exits"] += 1
                        self._event("PUBLIC_PICASSO_ROI_EXIT", ts_event,
                            elapsed_minutes=elapsed, source_roi_profit_ratio=roi_profit_ratio,
                            underlying_roi_fraction=roi_fraction, roi_target=roi_target)
                        return
                source_exit, details = self._source_exit_signal()
                if source_exit:
                    instrument_id = self.instrument_ids[self.current_symbol]
                    self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
                    self.diagnostics["picasso_source_signal_exits"] += 1
                    self._event("PUBLIC_PICASSO_SOURCE_EXIT", ts_event, **details)
                    return
                favourable = float(bar.high) if side > 0 else float(bar.low)
                move = side * (favourable - entry) / entry
                if not self._trail_active and move >= activation:
                    self._trail_active = True
                    self._trail_best = favourable
                    self.diagnostics["picasso_trailing_activations"] += 1
                    self._event("PUBLIC_PICASSO_TRAILING_ACTIVATED", ts_event,
                        favourable_price=favourable, activation_fraction=activation)
                elif self._trail_active:
                    assert self._trail_best is not None
                    self._trail_best = max(self._trail_best, favourable) if side > 0 else min(self._trail_best, favourable)
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._trail_active = False
        self._trail_best = None


__all__ = ["Candidate35Config", "Candidate35Strategy"]
