"""NautilusTrader adapter for the live-effective Pasindu Supertrend policies."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import math
from typing import Sequence

from router import (
    BarObservation,
    FeatureObservation,
    PASINDU_CONTINUATION_STATE,
    PASINDU_FLIP_STATE,
    _supertrend,
    route_universe_aggregated,
)
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    pasindu_mode: str = "flip_only"
    pasindu_supertrend_period: int = 8
    pasindu_supertrend_multiplier: float = 2.0
    pasindu_adx_period: int = 14
    pasindu_adx_min: float = 18.0
    pasindu_confidence_min: float = 45.0
    pasindu_established_4h_bars: int = 3
    pasindu_continuation_lookback_1h: int = 8
    pasindu_trail_activate_atr: float = 2.0
    pasindu_trail_distance_atr: float = 2.5


def _completed_bucket(
    bars: Sequence[BarObservation],
    minutes: int,
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
            symbol: deque(self.bars[symbol], maxlen=300)
            for symbol in SYMBOLS
        }
        self.hour_bars = {
            symbol: deque(maxlen=500) for symbol in SYMBOLS
        }
        self.four_hour_bars = {
            symbol: deque(maxlen=300) for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            pasindu_mode=str(config.pasindu_mode),
            pasindu_supertrend_period=int(config.pasindu_supertrend_period),
            pasindu_supertrend_multiplier=float(
                config.pasindu_supertrend_multiplier
            ),
            pasindu_adx_period=int(config.pasindu_adx_period),
            pasindu_adx_min=float(config.pasindu_adx_min),
            pasindu_confidence_min=float(config.pasindu_confidence_min),
            pasindu_established_4h_bars=int(
                config.pasindu_established_4h_bars
            ),
            pasindu_continuation_lookback_1h=int(
                config.pasindu_continuation_lookback_1h
            ),
            pasindu_trail_activate_atr=float(
                config.pasindu_trail_activate_atr
            ),
            pasindu_trail_distance_atr=float(
                config.pasindu_trail_distance_atr
            ),
        )
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self.diagnostics.update(
            {
                "external_source": (
                    "PasinduUpendra/Binance-Futures-Trading"
                ),
                "external_performance_used_as_evidence": False,
                "pasindu_policy": str(config.pasindu_mode),
                "live_effective_supertrend_period": int(
                    config.pasindu_supertrend_period
                ),
                "live_effective_supertrend_multiplier": float(
                    config.pasindu_supertrend_multiplier
                ),
                "pasindu_hourly_decisions": 0,
                "pasindu_source_candidates": 0,
                "pasindu_used_episode_rejections": 0,
                "pasindu_management_exits": {},
                "pasindu_hour_bars_by_symbol": {},
                "pasindu_four_hour_bars_by_symbol": {},
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
            }
        )

    def _append_completed_caches(self, ts_event: int) -> None:
        minute_ordinal = int(ts_event // 60_000_000_000)
        for symbol in SYMBOLS:
            raw = tuple(self.bars[symbol])
            if minute_ordinal % 60 == 59:
                candle = _completed_bucket(raw, 60)
                if candle is not None and (
                    not self.hour_bars[symbol]
                    or self.hour_bars[symbol][-1].ts_event != candle.ts_event
                ):
                    self.hour_bars[symbol].append(candle)
            if minute_ordinal % 240 == 239:
                candle = _completed_bucket(raw, 240)
                if candle is not None and (
                    not self.four_hour_bars[symbol]
                    or self.four_hour_bars[symbol][-1].ts_event != candle.ts_event
                ):
                    self.four_hour_bars[symbol].append(candle)
        self.diagnostics["pasindu_hour_bars_by_symbol"] = {
            symbol: len(self.hour_bars[symbol]) for symbol in SYMBOLS
        }
        self.diagnostics["pasindu_four_hour_bars_by_symbol"] = {
            symbol: len(self.four_hour_bars[symbol]) for symbol in SYMBOLS
        }

    def _source_exit(self, ts_event: int, reason: str) -> None:
        if self.current_symbol is None or self.current_scenario is None:
            return
        if bool(self.current_scenario.get("pasindu_exit_pending")):
            return
        self.current_scenario["pasindu_exit_pending"] = True
        counts = self.diagnostics["pasindu_management_exits"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event(
            "PASINDU_SOURCE_EXIT",
            ts_event,
            symbol=self.current_symbol,
            reason=reason,
        )

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None or self.current_scenario is None:
            super()._manage_open_position(ts_event)
            return
        scenario = self.current_scenario
        if bool(scenario.get("pasindu_exit_pending")):
            return
        try:
            side = int(scenario.get("side", 0))
            atr = float(scenario.get("pasindu_atr_at_entry"))
            entry = float(
                scenario.get("actual_entry_fill")
                or scenario.get("entry_reference")
            )
        except (TypeError, ValueError):
            super()._manage_open_position(ts_event)
            return
        if side not in (-1, 1) or not math.isfinite(atr) or atr <= 0.0:
            super()._manage_open_position(ts_event)
            return
        latest = self.bars[self.current_symbol][-1]
        close = float(latest.close)
        if side > 0:
            extreme = max(
                float(scenario.get("pasindu_favorable_extreme") or entry),
                float(latest.high),
            )
            favorable = extreme - entry
            trail_level = extreme - float(
                self.route_config.pasindu_trail_distance_atr
            ) * atr
            crossed_trail = close <= trail_level
        else:
            extreme = min(
                float(scenario.get("pasindu_favorable_extreme") or entry),
                float(latest.low),
            )
            favorable = entry - extreme
            trail_level = extreme + float(
                self.route_config.pasindu_trail_distance_atr
            ) * atr
            crossed_trail = close >= trail_level
        scenario["pasindu_favorable_extreme"] = extreme
        active = bool(scenario.get("pasindu_trail_active"))
        if not active and favorable >= float(
            self.route_config.pasindu_trail_activate_atr
        ) * atr:
            active = True
            scenario["pasindu_trail_active"] = True
            scenario["pasindu_trail_activated_ts"] = int(ts_event)
        scenario["pasindu_trail_level"] = trail_level
        if active and crossed_trail:
            self._source_exit(ts_event, "ATR_TRAIL_2P5_AFTER_2P0")
            return

        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % 240 == 239:
            candles = tuple(self.four_hour_bars[self.current_symbol])
            if len(candles) >= 2:
                _, directions = _supertrend(
                    candles,
                    int(self.route_config.pasindu_supertrend_period),
                    float(self.route_config.pasindu_supertrend_multiplier),
                )
                valid = [
                    int(value) for value in directions
                    if math.isfinite(value)
                ]
                if valid and valid[-1] == -side:
                    self._source_exit(ts_event, "OPPOSING_4H_SUPERTREND")
                    return
        super()._manage_open_position(ts_event)

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._append_completed_caches(ts_event)
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
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % 60 != 59:
            return
        if any(
            len(self.hour_bars[symbol]) < 105
            or len(self.four_hour_bars[symbol]) < 105
            for symbol in SYMBOLS
        ):
            return

        self.diagnostics["quarter_hour_decisions"] += 1
        self.diagnostics["pasindu_hourly_decisions"] += 1
        _, decisions = route_universe_aggregated(
            hours_by_symbol={
                symbol: tuple(self.hour_bars[symbol])
                for symbol in SYMBOLS
            },
            four_hours_by_symbol={
                symbol: tuple(self.four_hour_bars[symbol])
                for symbol in SYMBOLS
            },
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
                self.diagnostics["pasindu_source_candidates"] += 1
                key = (
                    decision.state,
                    decision.symbol,
                    int(decision.episode_ts),
                )
                if key in self.used_episode_keys:
                    self.diagnostics[
                        "pasindu_used_episode_rejections"
                    ] += 1
                else:
                    candidates.append(decision)
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = (
                        int(reason_counts.get(reason, 0)) + 1
                    )
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
                    "candidate": "candidate-51-pasindu-live-supertrend",
                    "state_family": winner.state,
                    "source_policy": str(self.config.pasindu_mode),
                    "source_performance_used": False,
                    "live_supertrend_period": int(
                        self.route_config.pasindu_supertrend_period
                    ),
                    "live_supertrend_multiplier": float(
                        self.route_config.pasindu_supertrend_multiplier
                    ),
                    "pasindu_atr_at_entry": float(
                        winner.diagnostics["atr_at_entry"]
                    ),
                    "pasindu_trail_active": False,
                    "pasindu_favorable_extreme": winner.entry_reference,
                    "pasindu_exit_pending": False,
                    "management": (
                        "source-2R-bracket-opposite-4h-reversal-"
                        "2ATR-activation-2.5ATR-trail"
                    ),
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
