"""Efficient completed-bar Nautilus adapter for public TrendRider v2.11.

This subclasses the source-management adapter and changes only data plumbing:
1m observations are causally accumulated once into 1h/4h/1d candles.  The same
public signals and exits are then evaluated from those caches rather than
rebuilding every timeframe from the entire minute history each hour.
"""
from __future__ import annotations

from collections import deque
import math
from typing import Sequence

import strategy_trendrider as _base
from router import (
    BarObservation,
    FeatureObservation,
    TRENDRIDER_STATE,
    route_universe_aggregated,
    trendrider_exit_signal,
)
from strategy_base import SYMBOLS

Candidate35Config = _base.Candidate35Config


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


class Candidate35Strategy(_base.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        # Only the latest day of minutes is needed to form the next daily bar.
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=1_500)
            for symbol in SYMBOLS
        }
        self.hour_bars = {
            symbol: deque(maxlen=400) for symbol in SYMBOLS
        }
        self.four_hour_bars = {
            symbol: deque(maxlen=260) for symbol in SYMBOLS
        }
        self.day_bars = {
            symbol: deque(maxlen=260) for symbol in SYMBOLS
        }
        self.diagnostics.update(
            {
                "trendrider_cached_completed_bars": True,
                "trendrider_hour_bars_by_symbol": {},
                "trendrider_four_hour_bars_by_symbol": {},
                "trendrider_day_bars_by_symbol": {},
            }
        )

    def _append_completed_caches(self, ts_event: int) -> None:
        minute_ns = 60_000_000_000
        minute_ordinal = int(ts_event // minute_ns)
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
            if minute_ordinal % 1_440 == 1_439:
                candle = _completed_bucket(raw, 1_440)
                if candle is not None and (
                    not self.day_bars[symbol]
                    or self.day_bars[symbol][-1].ts_event != candle.ts_event
                ):
                    self.day_bars[symbol].append(candle)
        self.diagnostics["trendrider_hour_bars_by_symbol"] = {
            symbol: len(self.hour_bars[symbol]) for symbol in SYMBOLS
        }
        self.diagnostics["trendrider_four_hour_bars_by_symbol"] = {
            symbol: len(self.four_hour_bars[symbol]) for symbol in SYMBOLS
        }
        self.diagnostics["trendrider_day_bars_by_symbol"] = {
            symbol: len(self.day_bars[symbol]) for symbol in SYMBOLS
        }

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None or self.current_scenario is None:
            _base._ExecutionShell._manage_open_position(self, ts_event)
            return
        scenario = self.current_scenario
        if bool(scenario.get("trendrider_exit_pending")):
            return
        raw = self.bars[self.current_symbol]
        if not raw:
            _base._ExecutionShell._manage_open_position(self, ts_event)
            return
        entry = scenario.get("actual_entry_fill") or scenario.get(
            "entry_reference"
        )
        try:
            entry_price = float(entry)
        except (TypeError, ValueError):
            entry_price = math.nan
        if not math.isfinite(entry_price) or entry_price <= 0.0:
            _base._ExecutionShell._manage_open_position(self, ts_event)
            return

        latest = raw[-1]
        current_price = float(latest.close)
        peak = max(
            float(scenario.get("trendrider_peak_price") or entry_price),
            float(latest.high),
        )
        scenario["trendrider_peak_price"] = peak
        current_profit = current_price / entry_price - 1.0
        peak_profit = peak / entry_price - 1.0
        elapsed = max(0, self.minute_index - self.position_open_minute)
        scenario.update(
            {
                "trendrider_current_profit": current_profit,
                "trendrider_peak_profit": peak_profit,
                "trendrider_elapsed_minutes": elapsed,
            }
        )

        bucket = int(self.route_config.trendrider_bucket_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % bucket == bucket - 1:
            reason = trendrider_exit_signal(
                tuple(self.hour_bars[self.current_symbol]),
                self.route_config,
            )
            if reason:
                self._submit_source_exit(ts_event, reason)
                return

        if elapsed >= 120 and current_profit < -0.015:
            self._submit_source_exit(ts_event, "EARLY_LOSS_CUT_2H")
            return
        if elapsed >= 240 and current_profit < 0.0:
            self._submit_source_exit(ts_event, "EARLY_LOSS_CUT_4H")
            return
        if elapsed >= 480 and current_profit < 0.005:
            self._submit_source_exit(ts_event, "EARLY_LOSS_CUT_8H")
            return
        if elapsed >= 960 and current_profit < 0.01:
            self._submit_source_exit(ts_event, "EARLY_LOSS_CUT_16H")
            return
        if elapsed >= 1_440:
            self._submit_source_exit(ts_event, "TIME_EXIT_24H")
            return

        roi_threshold = float(
            self.route_config.trendrider_remote_target_fraction
        )
        if elapsed >= 764:
            roi_threshold = float(self.config.trendrider_roi_764m)
        elif elapsed >= 290:
            roi_threshold = float(self.config.trendrider_roi_290m)
        elif elapsed >= 124:
            roi_threshold = float(self.config.trendrider_roi_124m)
        if current_profit >= roi_threshold:
            self._submit_source_exit(ts_event, f"ROI_{roi_threshold:.3f}")
            return

        if (
            peak_profit >= float(self.config.trendrider_trailing_activation)
            and current_price
            <= peak * (1.0 - float(self.config.trendrider_trailing_distance))
        ):
            self._submit_source_exit(
                ts_event,
                "TRAILING_STOP_3PCT_AFTER_5PCT",
            )
            return

        _base._ExecutionShell._manage_open_position(self, ts_event)

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
        bucket = int(self.route_config.trendrider_bucket_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % bucket != bucket - 1:
            return
        if any(len(self.hour_bars[symbol]) < 206 for symbol in SYMBOLS):
            return

        observations = {
            symbol: FeatureObservation(
                observed_time_ns=int(self.hour_bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        del observations  # Aggregated facade consumes completed bars directly.
        self.diagnostics["quarter_hour_decisions"] += 1
        self.diagnostics["trendrider_hourly_decisions"] += 1
        _, decisions = route_universe_aggregated(
            hours_by_symbol={
                symbol: tuple(self.hour_bars[symbol]) for symbol in SYMBOLS
            },
            four_hours_by_symbol={
                symbol: tuple(self.four_hour_bars[symbol])
                for symbol in SYMBOLS
            },
            days_by_symbol={
                symbol: tuple(self.day_bars[symbol]) for symbol in SYMBOLS
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
                self.diagnostics["trendrider_source_candidates"] += 1
                key = (decision.symbol, int(decision.episode_ts))
                if key in self.used_episode_keys:
                    self.diagnostics[
                        "trendrider_used_episode_rejections"
                    ] += 1
                else:
                    candidates.append(decision)
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = (
                        int(reason_counts.get(reason, 0)) + 1
                    )
                    if reason == "TRENDRIDER_CONFIDENCE_REJECTED":
                        self.diagnostics[
                            "trendrider_confidence_rejections"
                        ] += 1

        if not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return
        candidates.sort(key=lambda item: (-float(item.score), item.symbol))
        winner = candidates[0]
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return

        key = (winner.symbol, int(winner.episode_ts))
        self.used_episode_keys.add(key)
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-51-public-trendrider-v211",
                    "state_family": TRENDRIDER_STATE,
                    "source_entry_tag": winner.diagnostics.get("entry_tag"),
                    "source_confidence": winner.diagnostics.get("confidence"),
                    "source_regime": winner.diagnostics.get("regime"),
                    "source_performance_used": False,
                    "risk_geometry": "public-fixed-six-percent-hard-stop",
                    "management": (
                        "public-roi-trailing-indicator-cascade-24h"
                    ),
                    "evaluation_path": "cached-completed-1h-4h-1d",
                    "trendrider_peak_price": winner.entry_reference,
                    "trendrider_exit_pending": False,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
