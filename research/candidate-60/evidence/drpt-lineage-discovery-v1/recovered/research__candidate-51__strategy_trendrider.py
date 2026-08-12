"""NautilusTrader adapter for the public TrendRider v2.11 long policy."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import math

from router import (
    FeatureObservation,
    TRENDRIDER_STATE,
    route_universe,
    trendrider_exit_signal,
)
from router_picasso import _aggregate_complete
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    trendrider_bucket_minutes: int = 60
    trendrider_ema_fast: int = 9
    trendrider_ema_slow: int = 16
    trendrider_rsi_period: int = 16
    trendrider_rsi_pullback_low: float = 30.0
    trendrider_rsi_pullback_high: float = 65.0
    trendrider_rsi_bounce: float = 35.0
    trendrider_adx_threshold: float = 18.0
    trendrider_volume_factor: float = 0.70
    trendrider_rsi_exit: float = 78.0
    trendrider_min_confidence_normal: int = 5
    trendrider_min_confidence_bear: int = 6
    trendrider_stop_fraction: float = 0.06
    trendrider_remote_target_fraction: float = 0.229
    trendrider_trailing_activation: float = 0.05
    trendrider_trailing_distance: float = 0.03
    trendrider_roi_124m: float = 0.136
    trendrider_roi_290m: float = 0.044
    trendrider_roi_764m: float = 0.0


class Candidate35Strategy(_ExecutionShell):
    """Public long policy under one global entry/position slot."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        # 34.7 days retain enough completed minutes for the public 4h EMA200
        # confidence bonus while keeping the repeated hourly aggregation bounded.
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=50_000)
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            trendrider_bucket_minutes=int(config.trendrider_bucket_minutes),
            trendrider_ema_fast=int(config.trendrider_ema_fast),
            trendrider_ema_slow=int(config.trendrider_ema_slow),
            trendrider_rsi_period=int(config.trendrider_rsi_period),
            trendrider_rsi_pullback_low=float(
                config.trendrider_rsi_pullback_low
            ),
            trendrider_rsi_pullback_high=float(
                config.trendrider_rsi_pullback_high
            ),
            trendrider_rsi_bounce=float(config.trendrider_rsi_bounce),
            trendrider_adx_threshold=float(config.trendrider_adx_threshold),
            trendrider_volume_factor=float(config.trendrider_volume_factor),
            trendrider_rsi_exit=float(config.trendrider_rsi_exit),
            trendrider_min_confidence_normal=int(
                config.trendrider_min_confidence_normal
            ),
            trendrider_min_confidence_bear=int(
                config.trendrider_min_confidence_bear
            ),
            trendrider_stop_fraction=float(config.trendrider_stop_fraction),
            trendrider_remote_target_fraction=float(
                config.trendrider_remote_target_fraction
            ),
        )
        self.used_episode_keys: set[tuple[str, int]] = set()
        self.diagnostics.update(
            {
                "external_source": "darkvolg/trendrider-strategy",
                "external_source_version": "2.11.0-public-long-only",
                "external_performance_used_as_evidence": False,
                "source_parity_gap": (
                    "website/private system includes short and private inputs; "
                    "this run measures public OHLCV long policy only"
                ),
                "trendrider_hourly_decisions": 0,
                "trendrider_source_candidates": 0,
                "trendrider_confidence_rejections": 0,
                "trendrider_used_episode_rejections": 0,
                "trendrider_management_exits": {},
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
            }
        )

    def _submit_source_exit(self, ts_event: int, reason: str) -> None:
        if self.current_symbol is None or self.current_scenario is None:
            return
        if bool(self.current_scenario.get("trendrider_exit_pending")):
            return
        self.current_scenario["trendrider_exit_pending"] = True
        counts = self.diagnostics["trendrider_management_exits"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event(
            "TRENDRIDER_SOURCE_EXIT",
            ts_event,
            symbol=self.current_symbol,
            reason=reason,
        )

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None or self.current_scenario is None:
            super()._manage_open_position(ts_event)
            return
        scenario = self.current_scenario
        if bool(scenario.get("trendrider_exit_pending")):
            return
        bars = self.bars[self.current_symbol]
        if not bars:
            super()._manage_open_position(ts_event)
            return
        entry = scenario.get("actual_entry_fill") or scenario.get(
            "entry_reference"
        )
        try:
            entry_price = float(entry)
        except (TypeError, ValueError):
            entry_price = math.nan
        if not math.isfinite(entry_price) or entry_price <= 0.0:
            super()._manage_open_position(ts_event)
            return

        latest = bars[-1]
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

        # Public indicator exits are evaluated only when a full one-hour candle
        # has completed.  The order executes after that causal close.
        bucket = int(self.route_config.trendrider_bucket_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % bucket == bucket - 1:
            hours = _aggregate_complete(tuple(bars), bucket)
            reason = trendrider_exit_signal(hours, self.route_config)
            if reason:
                self._submit_source_exit(ts_event, reason)
                return

        # V4 public cascading early-loss exit.
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

        # Public minimal ROI ladder.  The remote bracket enforces the first
        # 22.9% target; lower time-dependent rungs are handled here.
        roi_threshold = float(self.route_config.trendrider_remote_target_fraction)
        if elapsed >= 764:
            roi_threshold = float(self.config.trendrider_roi_764m)
        elif elapsed >= 290:
            roi_threshold = float(self.config.trendrider_roi_290m)
        elif elapsed >= 124:
            roi_threshold = float(self.config.trendrider_roi_124m)
        if current_profit >= roi_threshold:
            self._submit_source_exit(ts_event, f"ROI_{roi_threshold:.3f}")
            return

        # Public 3% trailing stop activates only after the peak exceeds +5%.
        if (
            peak_profit >= float(self.config.trendrider_trailing_activation)
            and current_price
            <= peak * (1.0 - float(self.config.trendrider_trailing_distance))
        ):
            self._submit_source_exit(ts_event, "TRAILING_STOP_3PCT_AFTER_5PCT")
            return

        super()._manage_open_position(ts_event)

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

        bucket = int(self.route_config.trendrider_bucket_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % bucket != bucket - 1:
            return
        if any(len(self.bars[symbol]) < bucket * 205 for symbol in SYMBOLS):
            return

        observations = {
            symbol: FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        self.diagnostics["trendrider_hourly_decisions"] += 1
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
        candidates.sort(
            key=lambda item: (-float(item.score), item.symbol)
        )
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
                    "trendrider_peak_price": winner.entry_reference,
                    "trendrider_exit_pending": False,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
