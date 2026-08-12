"""NautilusTrader adapter for the frozen TrendRider trend-pullback branch."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import math
from typing import Any

from router import (
    PICASSO_STATE,
    _aggregate_complete,
    _ema,
    _macd,
    _rsi,
)
from strategy_base import SYMBOLS
from strategy_base import Candidate35Strategy as _ExecutionShell
from strategy_picasso import (
    Candidate35Config as _PicassoConfig,
    Candidate35Strategy as _PicassoStrategy,
)


class Candidate35Config(_PicassoConfig, frozen=True):
    trendrider_ema_fast: int = 9
    trendrider_ema_slow: int = 16
    trendrider_ema_regime_fast: int = 50
    trendrider_ema_regime_slow: int = 200
    trendrider_rsi_period: int = 16
    trendrider_adx_period: int = 14
    trendrider_volume_ema_period: int = 20
    trendrider_obv_ema_period: int = 20
    trendrider_rsi_pullback_low: float = 30.0
    trendrider_rsi_pullback_high: float = 65.0
    trendrider_adx_threshold: float = 18.0
    trendrider_volume_factor: float = 0.7
    trendrider_pullback_tolerance: float = 0.02
    trendrider_min_confidence: int = 5
    trendrider_stop_fraction: float = 0.06
    trendrider_emergency_objective_fraction: float = 0.229
    trendrider_trailing_positive: float = 0.03
    trendrider_trailing_offset: float = 0.05
    trendrider_roi_0: float = 0.229
    trendrider_roi_t1_minutes: int = 124
    trendrider_roi_t1: float = 0.136
    trendrider_roi_t2_minutes: int = 290
    trendrider_roi_t2: float = 0.044
    trendrider_roi_t3_minutes: int = 764
    trendrider_roi_t3: float = 0.0
    trendrider_rsi_exit: float = 78.0
    trendrider_early_loss_2h: float = -0.015
    trendrider_early_loss_4h: float = 0.0
    trendrider_early_loss_8h: float = 0.005
    trendrider_early_loss_16h: float = 0.010
    trendrider_round_trip_cost_fraction: float = 0.0021
    trendrider_history_minutes: int = 16_000


class Candidate35Strategy(_PicassoStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        if int(config.picasso_bucket_minutes) != 60:
            raise ValueError("TrendRider pullback source requires completed 1h candles")
        if int(config.trendrider_history_minutes) < 60 * (
            int(config.trendrider_ema_regime_slow) + 5
        ):
            raise ValueError("TrendRider history must retain the source EMA200 warmup")
        if not 0.0 < float(config.trendrider_stop_fraction) < 1.0:
            raise ValueError("TrendRider source stop must be a price fraction")
        if float(config.trendrider_round_trip_cost_fraction) < 0.0:
            raise ValueError("round-trip cost allowance must be non-negative")
        super().__init__(config)
        self.bars = {
            symbol: deque(
                self.bars[symbol], maxlen=int(config.trendrider_history_minutes)
            )
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            trendrider_ema_fast=int(config.trendrider_ema_fast),
            trendrider_ema_slow=int(config.trendrider_ema_slow),
            trendrider_ema_regime_fast=int(config.trendrider_ema_regime_fast),
            trendrider_ema_regime_slow=int(config.trendrider_ema_regime_slow),
            trendrider_rsi_period=int(config.trendrider_rsi_period),
            trendrider_adx_period=int(config.trendrider_adx_period),
            trendrider_volume_ema_period=int(config.trendrider_volume_ema_period),
            trendrider_obv_ema_period=int(config.trendrider_obv_ema_period),
            trendrider_rsi_pullback_low=float(config.trendrider_rsi_pullback_low),
            trendrider_rsi_pullback_high=float(config.trendrider_rsi_pullback_high),
            trendrider_adx_threshold=float(config.trendrider_adx_threshold),
            trendrider_volume_factor=float(config.trendrider_volume_factor),
            trendrider_pullback_tolerance=float(config.trendrider_pullback_tolerance),
            trendrider_min_confidence=int(config.trendrider_min_confidence),
            trendrider_stop_fraction=float(config.trendrider_stop_fraction),
            trendrider_emergency_objective_fraction=float(
                config.trendrider_emergency_objective_fraction
            ),
        )
        self._roi_schedule = tuple(
            sorted(
                (
                    (0, float(config.trendrider_roi_0)),
                    (
                        int(config.trendrider_roi_t1_minutes),
                        float(config.trendrider_roi_t1),
                    ),
                    (
                        int(config.trendrider_roi_t2_minutes),
                        float(config.trendrider_roi_t2),
                    ),
                    (
                        int(config.trendrider_roi_t3_minutes),
                        float(config.trendrider_roi_t3),
                    ),
                )
            )
        )
        self.diagnostics.update(
            {
                "candidate57_trendrider_pullback_long_v1": 1,
                "trendrider_external_source": "darkvolg/trendrider-strategy:TrendRiderStrategy.py",
                "trendrider_source_branch": "trend_pullback",
                "trendrider_other_public_entry_branches_imported": 0,
                "trendrider_private_layers_used": 0,
                "trendrider_daily_informative_filter_used": 0,
                "trendrider_parameter_grid_used": 0,
                "trendrider_roi_exits": 0,
                "trendrider_trailing_activations": 0,
                "trendrider_trailing_exits": 0,
                "trendrider_indicator_exits": 0,
                "trendrider_early_loss_cut_2h": 0,
                "trendrider_early_loss_cut_4h": 0,
                "trendrider_early_loss_cut_8h": 0,
                "trendrider_early_loss_cut_16h": 0,
                "trendrider_time_exit_24h": 0,
                "trendrider_entry_changed_after_results": 0,
                "trendrider_management_changed_after_results": 0,
            }
        )

    def _roi_profit_ratio(self, elapsed_minutes: int) -> float:
        result = float(self._roi_schedule[0][1])
        for minute, value in self._roi_schedule:
            if elapsed_minutes >= int(minute):
                result = float(value)
            else:
                break
        return result

    def _estimated_after_cost_return(self) -> float:
        scenario = self.current_scenario or {}
        entry = float(scenario.get("entry_reference", 0.0))
        if (
            self.current_symbol is None
            or not math.isfinite(entry)
            or entry <= 0.0
            or not self.bars[self.current_symbol]
        ):
            return math.nan
        close = float(self.bars[self.current_symbol][-1].close)
        return close / entry - 1.0 - float(
            self.config.trendrider_round_trip_cost_fraction
        )

    def _indicator_exit_signal(self) -> tuple[bool, dict[str, float | int | str]]:
        if self.current_symbol is None:
            return False, {}
        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )
        required = max(
            int(self.config.trendrider_ema_regime_slow) + 2,
            int(self.config.trendrider_ema_slow) + 2,
            int(self.config.trendrider_rsi_period) + 2,
            30,
        )
        if len(candles) < required:
            return False, {"trendrider_exit_ready": 0}
        closes = [float(candle.close) for candle in candles]
        fast = _ema(closes, int(self.config.trendrider_ema_fast))
        slow = _ema(closes, int(self.config.trendrider_ema_slow))
        ema_200 = _ema(closes, int(self.config.trendrider_ema_regime_slow))
        rsi = _rsi(closes, int(self.config.trendrider_rsi_period))
        macd_line, macd_signal = _macd(closes)
        hist = [
            float(left) - float(right)
            if math.isfinite(float(left)) and math.isfinite(float(right))
            else math.nan
            for left, right in zip(macd_line, macd_signal, strict=True)
        ]
        values = (
            fast[-1],
            fast[-2],
            slow[-1],
            slow[-2],
            ema_200[-1],
            ema_200[-2],
            rsi[-1],
            hist[-1],
            hist[-2],
        )
        if not all(math.isfinite(float(value)) for value in values):
            return False, {"trendrider_exit_ready": 0}
        close = closes[-1]
        prior_close = closes[-2]
        rsi_overbought = float(rsi[-1]) > float(self.config.trendrider_rsi_exit)
        bearish_cross = (
            float(fast[-1]) < float(slow[-1])
            and float(fast[-2]) >= float(slow[-2])
            and float(hist[-1]) < 0.0
            and float(rsi[-1]) > 50.0
        )
        trend_broken = (
            close < float(ema_200[-1]) * 0.99
            and prior_close >= float(ema_200[-2])
        )
        early_warning = (
            close < float(ema_200[-1]) * 0.995
            and float(rsi[-1]) > 72.0
            and float(hist[-1]) < float(hist[-2])
        )
        return bool(rsi_overbought or bearish_cross or trend_broken or early_warning), {
            "trendrider_exit_ready": 1,
            "trendrider_exit_close": close,
            "trendrider_exit_rsi": float(rsi[-1]),
            "trendrider_exit_fast_ema": float(fast[-1]),
            "trendrider_exit_slow_ema": float(slow[-1]),
            "trendrider_exit_ema200": float(ema_200[-1]),
            "trendrider_exit_macd_hist": float(hist[-1]),
            "trendrider_exit_rsi_overbought": int(rsi_overbought),
            "trendrider_exit_bearish_cross": int(bearish_cross),
            "trendrider_exit_trend_broken": int(trend_broken),
            "trendrider_exit_early_warning": int(early_warning),
        }

    def _close_trendrider(
        self,
        event_name: str,
        ts_event: int,
        diagnostics_key: str,
        **payload: Any,
    ) -> None:
        if self.current_symbol is None:
            return
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self.diagnostics[diagnostics_key] += 1
        self._event(event_name, ts_event, **payload)

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") != PICASSO_STATE:
            _ExecutionShell._manage_open_position(self, ts_event)
            return
        side = int(scenario.get("side", 0))
        entry = float(scenario.get("entry_reference", 0.0))
        if side != 1 or not math.isfinite(entry) or entry <= 0.0:
            _ExecutionShell._manage_open_position(self, ts_event)
            return
        bar = self.bars[self.current_symbol][-1]
        elapsed = max(0, self.minute_index - self.position_open_minute)
        if self._trail_best is None or not math.isfinite(float(self._trail_best)):
            self._trail_best = entry

        if self._trail_active:
            trailing_stop = float(self._trail_best) * (
                1.0 - float(self.config.trendrider_trailing_positive)
            )
            if float(bar.low) <= trailing_stop:
                self._close_trendrider(
                    "PUBLIC_TRENDRIDER_TRAILING_EXIT",
                    ts_event,
                    "trendrider_trailing_exits",
                    elapsed_minutes=elapsed,
                    trailing_stop=trailing_stop,
                    best_price=float(self._trail_best),
                )
                return

        roi_fraction = float(self._roi_profit_ratio(elapsed))
        roi_target = entry * (1.0 + roi_fraction)
        if float(bar.high) >= roi_target:
            self._close_trendrider(
                "PUBLIC_TRENDRIDER_ROI_EXIT",
                ts_event,
                "trendrider_roi_exits",
                elapsed_minutes=elapsed,
                roi_fraction=roi_fraction,
                roi_target=roi_target,
            )
            return

        source_exit, snapshot = self._indicator_exit_signal()
        if source_exit:
            self._close_trendrider(
                "PUBLIC_TRENDRIDER_INDICATOR_EXIT",
                ts_event,
                "trendrider_indicator_exits",
                elapsed_minutes=elapsed,
                **snapshot,
            )
            return

        net_return = self._estimated_after_cost_return()
        lifecycle: tuple[int, float, str, str] | None = None
        if elapsed >= 24 * 60:
            lifecycle = (
                24 * 60,
                math.inf,
                "PUBLIC_TRENDRIDER_TIME_EXIT_24H",
                "trendrider_time_exit_24h",
            )
        elif elapsed >= 16 * 60 and net_return < float(
            self.config.trendrider_early_loss_16h
        ):
            lifecycle = (
                16 * 60,
                float(self.config.trendrider_early_loss_16h),
                "PUBLIC_TRENDRIDER_EARLY_LOSS_CUT_16H",
                "trendrider_early_loss_cut_16h",
            )
        elif elapsed >= 8 * 60 and net_return < float(
            self.config.trendrider_early_loss_8h
        ):
            lifecycle = (
                8 * 60,
                float(self.config.trendrider_early_loss_8h),
                "PUBLIC_TRENDRIDER_EARLY_LOSS_CUT_8H",
                "trendrider_early_loss_cut_8h",
            )
        elif elapsed >= 4 * 60 and net_return < float(
            self.config.trendrider_early_loss_4h
        ):
            lifecycle = (
                4 * 60,
                float(self.config.trendrider_early_loss_4h),
                "PUBLIC_TRENDRIDER_EARLY_LOSS_CUT_4H",
                "trendrider_early_loss_cut_4h",
            )
        elif elapsed >= 2 * 60 and net_return < float(
            self.config.trendrider_early_loss_2h
        ):
            lifecycle = (
                2 * 60,
                float(self.config.trendrider_early_loss_2h),
                "PUBLIC_TRENDRIDER_EARLY_LOSS_CUT_2H",
                "trendrider_early_loss_cut_2h",
            )
        if lifecycle is not None:
            threshold_minute, threshold_return, event_name, diagnostics_key = lifecycle
            self._close_trendrider(
                event_name,
                ts_event,
                diagnostics_key,
                elapsed_minutes=elapsed,
                threshold_minutes=threshold_minute,
                threshold_after_cost_return=(
                    None if not math.isfinite(threshold_return) else threshold_return
                ),
                estimated_after_cost_return=(
                    net_return if math.isfinite(net_return) else None
                ),
            )
            return

        self._trail_best = max(float(self._trail_best), float(bar.high))
        favourable = float(self._trail_best) / entry - 1.0
        if (
            not self._trail_active
            and favourable >= float(self.config.trendrider_trailing_offset)
        ):
            self._trail_active = True
            self.diagnostics["trendrider_trailing_activations"] += 1
            self._event(
                "PUBLIC_TRENDRIDER_TRAILING_ACTIVATED",
                ts_event,
                elapsed_minutes=elapsed,
                favourable_fraction=favourable,
                activation_fraction=float(self.config.trendrider_trailing_offset),
                trail_fraction=float(self.config.trendrider_trailing_positive),
            )

        _ExecutionShell._manage_open_position(self, ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
