"""Project execution adapter for the public Slope-is-Dope one-hour strategy.

NautilusTrader, continuous NAV, one-slot execution and current-NAV 3% risk
sizing are inherited from the existing public-strategy shell.  This module adds
only the externally published Slope-is-Dope parameters, literal exits, ROI and
Freqtrade trailing semantics.
"""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Any

from router import (
    PICASSO_STATE,
    RouteConfig,
    _aggregate_complete,
    _sma,
)
from strategy_base import Candidate35Strategy as _ExecutionShell
from strategy_picasso import (
    Candidate35Config as _PicassoConfig,
    Candidate35Strategy as _PicassoStrategy,
)


class Candidate35Config(_PicassoConfig, frozen=True):
    slope_trigger_mode: str = "level"
    slope_side_mode: str = "both"
    slope_adx_period: int = 14
    slope_rsi_period: int = 10
    slope_market_ma_period: int = 97
    slope_fast_ma_period: int = 16
    slope_slow_ma_period: int = 57
    slope_adx_long: float = 39.0
    slope_adx_short: float = 20.0
    slope_close_shift_long: int = 6
    slope_close_shift_short: int = 9
    slope_exit_rolling_long: int = 9
    slope_exit_rolling_short: int = 9
    slope_source_effective_leverage: float = 2.0
    slope_source_stoploss: float = 0.289
    slope_trailing_positive: float = 0.010
    slope_trailing_offset: float = 0.021
    slope_trailing_only_offset_is_reached: bool = True
    slope_roi_0: float = 0.283
    slope_roi_t1_minutes: int = 132
    slope_roi_t1: float = 0.160
    slope_roi_t2_minutes: int = 548
    slope_roi_t2: float = 0.071
    slope_roi_t3_minutes: int = 961
    slope_roi_t3: float = 0.0
    slope_emergency_target_fraction: float = 0.20


class Candidate35Strategy(_PicassoStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        if int(config.picasso_bucket_minutes) != 60:
            raise ValueError("public Slope-is-Dope source requires completed 1h candles")
        if str(config.slope_trigger_mode).strip().lower() not in {"level", "edge"}:
            raise ValueError("unsupported Slope-is-Dope trigger mode")
        if str(config.slope_side_mode).strip().lower() not in {
            "both",
            "long",
            "short",
        }:
            raise ValueError("unsupported Slope-is-Dope side mode")
        if float(config.slope_source_effective_leverage) <= 0.0:
            raise ValueError("source effective leverage must be positive")
        if float(config.slope_source_stoploss) <= 0.0:
            raise ValueError("source stoploss profit ratio must be positive")
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            slope_trigger_mode=str(config.slope_trigger_mode),
            slope_side_mode=str(config.slope_side_mode),
            slope_adx_period=int(config.slope_adx_period),
            slope_rsi_period=int(config.slope_rsi_period),
            slope_market_ma_period=int(config.slope_market_ma_period),
            slope_fast_ma_period=int(config.slope_fast_ma_period),
            slope_slow_ma_period=int(config.slope_slow_ma_period),
            slope_adx_long=float(config.slope_adx_long),
            slope_adx_short=float(config.slope_adx_short),
            slope_close_shift_long=int(config.slope_close_shift_long),
            slope_close_shift_short=int(config.slope_close_shift_short),
            slope_source_effective_leverage=float(
                config.slope_source_effective_leverage
            ),
            slope_source_stoploss=float(config.slope_source_stoploss),
            slope_emergency_target_fraction=float(
                config.slope_emergency_target_fraction
            ),
        )
        self._roi_schedule = tuple(
            sorted(
                (
                    (0, float(config.slope_roi_0)),
                    (
                        int(config.slope_roi_t1_minutes),
                        float(config.slope_roi_t1),
                    ),
                    (
                        int(config.slope_roi_t2_minutes),
                        float(config.slope_roi_t2),
                    ),
                    (
                        int(config.slope_roi_t3_minutes),
                        float(config.slope_roi_t3),
                    ),
                ),
                reverse=True,
            )
        )
        self.diagnostics.update(
            {
                "candidate57_slope_source_adapter": 1,
                "slope_trigger_mode": str(config.slope_trigger_mode),
                "slope_side_mode": str(config.slope_side_mode),
                "slope_source_effective_leverage": float(
                    config.slope_source_effective_leverage
                ),
                "slope_source_stoploss": float(config.slope_source_stoploss),
                "slope_trailing_only_offset_is_reached": int(
                    bool(config.slope_trailing_only_offset_is_reached)
                ),
                "slope_trailing_activations": 0,
                "slope_default_trailing_exits": 0,
                "slope_positive_trailing_exits": 0,
                "slope_roi_exits": 0,
                "slope_zero_roi_exits": 0,
                "slope_source_signal_exits": 0,
                "slope_source_entry_changed": 0,
                "slope_source_exit_changed": 0,
                "slope_source_management_changed": 0,
            }
        )

    def _source_exit_signal(self) -> tuple[bool, dict[str, float | int]]:
        if self.current_symbol is None or self.current_scenario is None:
            return False, {}
        side = int(self.current_scenario.get("side", 0))
        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )
        long_window = int(self.config.slope_exit_rolling_long)
        short_window = int(self.config.slope_exit_rolling_short)
        required = max(
            int(self.config.slope_fast_ma_period),
            int(self.config.slope_slow_ma_period),
            long_window + 1,
            short_window + 1,
        ) + 1
        if len(candles) < required:
            return False, {"source_exit_ready": 0, "source_exit_candles": len(candles)}

        closes = [float(candle.close) for candle in candles]
        lows = [float(candle.low) for candle in candles]
        fast = _sma(closes, int(self.config.slope_fast_ma_period))[-1]
        slow = _sma(closes, int(self.config.slope_slow_ma_period))[-1]
        close = closes[-1]
        prior_low_long = min(lows[-long_window - 1 : -1])
        prior_low_short = min(lows[-short_window - 1 : -1])
        if not all(
            math.isfinite(value)
            for value in (fast, slow, close, prior_low_long, prior_low_short)
        ):
            return False, {"source_exit_ready": 0}

        if side > 0:
            ma_exit = float(fast) < float(slow)
            rolling_exit = close < prior_low_long
        elif side < 0:
            ma_exit = float(fast) > float(slow)
            # Literal public source: short compares against a rolling minimum low.
            rolling_exit = close > prior_low_short
        else:
            return False, {"source_exit_ready": 0}
        return bool(ma_exit or rolling_exit), {
            "source_exit_ready": 1,
            "source_exit_side": side,
            "source_exit_close": close,
            "source_exit_fast_ma": float(fast),
            "source_exit_slow_ma": float(slow),
            "source_exit_prior_low_long": prior_low_long,
            "source_exit_prior_low_short": prior_low_short,
            "source_exit_ma_condition": int(ma_exit),
            "source_exit_rolling_low_condition": int(rolling_exit),
        }

    def _close_slope_position(
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
        if side not in (-1, 1) or not math.isfinite(entry) or entry <= 0.0:
            _ExecutionShell._manage_open_position(self, ts_event)
            return
        bar = self.bars[self.current_symbol][-1]
        leverage = max(float(self.config.slope_source_effective_leverage), 1e-12)
        activation = float(self.config.slope_trailing_offset) / leverage
        positive_distance = float(self.config.slope_trailing_positive) / leverage
        default_distance = float(self.config.slope_source_stoploss) / leverage
        only_after_offset = bool(
            self.config.slope_trailing_only_offset_is_reached
        )
        if self._trail_best is None or not math.isfinite(float(self._trail_best)):
            self._trail_best = entry

        trailing_stop: float | None = None
        positive_trail = bool(self._trail_active)
        if positive_trail:
            trailing_stop = float(self._trail_best) * (
                1.0 - side * positive_distance
            )
        elif not only_after_offset:
            trailing_stop = float(self._trail_best) * (
                1.0 - side * default_distance
            )
        if trailing_stop is not None:
            hit = (
                float(bar.low) <= trailing_stop
                if side > 0
                else float(bar.high) >= trailing_stop
            )
            if hit:
                key = (
                    "slope_positive_trailing_exits"
                    if positive_trail
                    else "slope_default_trailing_exits"
                )
                self._close_slope_position(
                    "PUBLIC_SLOPE_POSITIVE_TRAILING_EXIT"
                    if positive_trail
                    else "PUBLIC_SLOPE_DEFAULT_TRAILING_EXIT",
                    ts_event,
                    key,
                    trailing_stop=trailing_stop,
                    best_price=float(self._trail_best),
                    activation_fraction=activation,
                    trail_fraction=(
                        positive_distance if positive_trail else default_distance
                    ),
                )
                return

        elapsed = max(0, self.minute_index - self.position_open_minute)
        roi_profit_ratio = float(self._roi_profit_ratio(elapsed))
        roi_fraction = roi_profit_ratio / leverage
        roi_target = entry * (1.0 + side * roi_fraction)
        roi_hit = (
            float(bar.high) >= roi_target
            if side > 0
            else float(bar.low) <= roi_target
        )
        if roi_hit:
            self._close_slope_position(
                "PUBLIC_SLOPE_ROI_EXIT",
                ts_event,
                "slope_roi_exits",
                elapsed_minutes=elapsed,
                roi_profit_ratio=roi_profit_ratio,
                roi_fraction=roi_fraction,
                roi_target=roi_target,
            )
            if roi_profit_ratio <= 0.0:
                self.diagnostics["slope_zero_roi_exits"] += 1
            return

        source_exit, snapshot = self._source_exit_signal()
        if source_exit:
            self._close_slope_position(
                "PUBLIC_SLOPE_SOURCE_SIGNAL_EXIT",
                ts_event,
                "slope_source_signal_exits",
                elapsed_minutes=elapsed,
                **snapshot,
            )
            return

        favourable = (
            float(bar.high) / entry - 1.0
            if side > 0
            else entry / max(float(bar.low), 1e-12) - 1.0
        )
        if side > 0:
            self._trail_best = max(float(self._trail_best), float(bar.high))
        else:
            self._trail_best = min(float(self._trail_best), float(bar.low))
        if not self._trail_active and favourable >= activation:
            self._trail_active = True
            self.diagnostics["slope_trailing_activations"] += 1
            self._event(
                "PUBLIC_SLOPE_POSITIVE_TRAILING_ACTIVATED",
                ts_event,
                favourable_fraction=favourable,
                activation_fraction=activation,
                trail_fraction=positive_distance,
                best_price=float(self._trail_best),
            )

        _ExecutionShell._manage_open_position(self, ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
