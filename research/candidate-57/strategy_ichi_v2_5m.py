"""NautilusTrader adapter for the public ichiV2 five-minute strategy family.

The module reuses the project's proven public-strategy execution shell,
continuous NAV, one global slot, realistic costs and current-NAV 3% planned-loss
sizing. It adds only the causal ichiV2 entry state and declared source
management variants. Chikou span is never used.
"""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Any

from router import ICHI_STATE, _aggregate_complete, _signal_at, _source_arrays
from strategy_base import Candidate35Strategy as _ExecutionShell
from strategy_picasso import (
    Candidate35Config as _PicassoConfig,
    Candidate35Strategy as _PicassoStrategy,
)


class Candidate35Config(_PicassoConfig, frozen=True):
    ichi_trigger_mode: str = "level"
    ichi_side_mode: str = "long"
    ichi_profile: str = "report_inferred"
    ichi_shift_inputs_one_candle: bool = True
    ichi_above_cloud_level: int = 1
    ichi_bullish_level: int = 4
    ichi_fan_shift_value: int = 3
    ichi_min_fan_magnitude_gain: float = 1.0013
    ichi_conversion_period: int = 20
    ichi_base_period: int = 60
    ichi_lagging_span_period: int = 120
    ichi_displacement: int = 30
    ichi_stop_fraction: float = 0.04
    ichi_objective_fraction: float = 0.10

    ichi_roi_enabled: bool = True
    ichi_ignore_roi_if_entry_signal: bool = True
    ichi_roi_0: float = 0.015
    ichi_roi_t1_minutes: int = 10_000
    ichi_roi_t1: float = 0.015
    ichi_roi_t2_minutes: int = 20_000
    ichi_roi_t2: float = 0.015
    ichi_roi_t3_minutes: int = 30_000
    ichi_roi_t3: float = 0.015

    ichi_trailing_enabled: bool = False
    ichi_trailing_positive: float = 0.0
    ichi_trailing_offset: float = 0.0
    ichi_trailing_only_offset_is_reached: bool = True
    ichi_exit_indicator: str = "trend_close_1.5h"


class Candidate35Strategy(_PicassoStrategy):
    """One global-slot account running the causal public ichiV2 state."""

    def __init__(self, config: Candidate35Config) -> None:
        if int(config.picasso_bucket_minutes) != 5:
            raise ValueError("public ichiV2 source requires completed five-minute candles")
        if str(config.ichi_trigger_mode).strip().lower() not in {"level", "edge"}:
            raise ValueError("unsupported ichiV2 trigger mode")
        if str(config.ichi_side_mode).strip().lower() not in {
            "long",
            "short",
            "both",
        }:
            raise ValueError("unsupported ichiV2 side mode")
        if not 0.0 < float(config.ichi_stop_fraction) < 1.0:
            raise ValueError("ichiV2 stop fraction must be in (0, 1)")
        if float(config.ichi_objective_fraction) <= 0.0:
            raise ValueError("ichiV2 objective fraction must be positive")
        if str(config.ichi_exit_indicator) != "trend_close_1.5h":
            raise ValueError("this frozen source version supports the public 1.5h exit only")
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            ichi_trigger_mode=str(config.ichi_trigger_mode),
            ichi_side_mode=str(config.ichi_side_mode),
            ichi_profile=str(config.ichi_profile),
            ichi_shift_inputs_one_candle=bool(config.ichi_shift_inputs_one_candle),
            ichi_above_cloud_level=int(config.ichi_above_cloud_level),
            ichi_bullish_level=int(config.ichi_bullish_level),
            ichi_fan_shift_value=int(config.ichi_fan_shift_value),
            ichi_min_fan_magnitude_gain=float(config.ichi_min_fan_magnitude_gain),
            ichi_conversion_period=int(config.ichi_conversion_period),
            ichi_base_period=int(config.ichi_base_period),
            ichi_lagging_span_period=int(config.ichi_lagging_span_period),
            ichi_displacement=int(config.ichi_displacement),
            ichi_stop_fraction=float(config.ichi_stop_fraction),
            ichi_objective_fraction=float(config.ichi_objective_fraction),
        )
        self._ichi_roi_schedule = tuple(
            sorted(
                (
                    (0, float(config.ichi_roi_0)),
                    (int(config.ichi_roi_t1_minutes), float(config.ichi_roi_t1)),
                    (int(config.ichi_roi_t2_minutes), float(config.ichi_roi_t2)),
                    (int(config.ichi_roi_t3_minutes), float(config.ichi_roi_t3)),
                )
            )
        )
        self.diagnostics.update(
            {
                "candidate57_ichi_v2_adapter": 1,
                "external_source": (
                    "vjaykrsna ichiV2 result claim; nicnl31/pyalgotrader "
                    "ichiV2 and ichiV2_5 source family"
                ),
                "ichi_profile": str(config.ichi_profile),
                "ichi_trigger_mode": str(config.ichi_trigger_mode),
                "ichi_side_mode": str(config.ichi_side_mode),
                "ichi_chikou_used": 0,
                "ichi_inputs_shifted_one_completed_candle": int(
                    bool(config.ichi_shift_inputs_one_candle)
                ),
                "ichi_roi_enabled": int(bool(config.ichi_roi_enabled)),
                "ichi_ignore_roi_if_entry_signal": int(
                    bool(config.ichi_ignore_roi_if_entry_signal)
                ),
                "ichi_roi_ignored_active_signal_minutes": 0,
                "ichi_trailing_enabled": int(bool(config.ichi_trailing_enabled)),
                "ichi_roi_exits": 0,
                "ichi_source_signal_exits": 0,
                "ichi_trailing_activations": 0,
                "ichi_trailing_exits": 0,
                "ichi_source_entry_changed": 0,
                "ichi_source_exit_changed": 0,
                "ichi_source_management_changed": 0,
            }
        )

    def _ichi_roi_profit_ratio(self, elapsed_minutes: int) -> float:
        result = float(self._ichi_roi_schedule[0][1])
        for minute, value in self._ichi_roi_schedule:
            if elapsed_minutes >= int(minute):
                result = float(value)
            else:
                break
        return result

    def _source_entry_signal_active(self) -> bool:
        if self.current_symbol is None or self.current_scenario is None:
            return False
        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )
        required = max(
            100,
            int(self.config.ichi_lagging_span_period)
            + int(self.config.ichi_displacement)
            + 2,
        )
        if len(candles) < required:
            return False
        arrays = _source_arrays(candles, self.route_config)
        long_level, short_level, _ = _signal_at(
            arrays, len(candles) - 1, self.route_config
        )
        side = int(self.current_scenario.get("side", 0))
        return bool(long_level if side > 0 else short_level if side < 0 else False)

    def _source_exit_signal(self) -> tuple[bool, dict[str, float | int | str]]:
        if self.current_symbol is None or self.current_scenario is None:
            return False, {}
        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )
        required = max(
            100,
            int(self.config.ichi_lagging_span_period)
            + int(self.config.ichi_displacement)
            + 2,
        )
        if len(candles) < required:
            return False, {
                "source_exit_ready": 0,
                "source_exit_completed_5m_candles": len(candles),
            }
        arrays = _source_arrays(candles, self.route_config)
        source = arrays[str(self.config.ichi_exit_indicator)]
        trend = arrays["trend_close_5m"]
        if len(source) < 2 or len(trend) < 2:
            return False, {"source_exit_ready": 0}
        current_trend = float(trend[-1])
        previous_trend = float(trend[-2])
        current_source = float(source[-1])
        previous_source = float(source[-2])
        if not all(
            math.isfinite(value)
            for value in (
                current_trend,
                previous_trend,
                current_source,
                previous_source,
            )
        ):
            return False, {"source_exit_ready": 0}
        side = int(self.current_scenario.get("side", 0))
        if side > 0:
            crossed = current_trend < current_source and previous_trend >= previous_source
        elif side < 0:
            crossed = current_trend > current_source and previous_trend <= previous_source
        else:
            return False, {"source_exit_ready": 0}
        return bool(crossed), {
            "source_exit_ready": 1,
            "source_exit_side": side,
            "source_exit_indicator": str(self.config.ichi_exit_indicator),
            "source_exit_current_trend": current_trend,
            "source_exit_previous_trend": previous_trend,
            "source_exit_current_indicator": current_source,
            "source_exit_previous_indicator": previous_source,
            "source_exit_crossed": int(crossed),
        }

    def _close_ichi_position(
        self,
        event_name: str,
        ts_event: int,
        diagnostics_key: str,
        **payload: Any,
    ) -> None:
        if self.current_symbol is None:
            return
        if self.current_scenario is not None:
            self.current_scenario["management_exit_reason"] = event_name
            self.current_scenario["management_exit_signal_ts"] = int(ts_event)
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self.diagnostics[diagnostics_key] += 1
        self._event(event_name, ts_event, **payload)

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") != ICHI_STATE:
            _ExecutionShell._manage_open_position(self, ts_event)
            return
        side = int(scenario.get("side", 0))
        entry = float(scenario.get("entry_reference", 0.0))
        if side not in (-1, 1) or not math.isfinite(entry) or entry <= 0.0:
            _ExecutionShell._manage_open_position(self, ts_event)
            return
        bar = self.bars[self.current_symbol][-1]

        # Test only the trailing floor known before this completed minute.
        if bool(self.config.ichi_trailing_enabled) and self._trail_active:
            if self._trail_best is not None and math.isfinite(float(self._trail_best)):
                distance = float(self.config.ichi_trailing_positive)
                trailing_stop = float(self._trail_best) * (1.0 - side * distance)
                hit = (
                    float(bar.low) <= trailing_stop
                    if side > 0
                    else float(bar.high) >= trailing_stop
                )
                if hit:
                    self._close_ichi_position(
                        "PUBLIC_ICHI_TRAILING_EXIT",
                        ts_event,
                        "ichi_trailing_exits",
                        trailing_stop=trailing_stop,
                        prior_best_price=float(self._trail_best),
                        trail_fraction=distance,
                        usable_from_prior_complete_minute=True,
                    )
                    return

        elapsed = max(0, self.minute_index - self.position_open_minute)
        if bool(self.config.ichi_roi_enabled):
            roi_ignored = (
                bool(self.config.ichi_ignore_roi_if_entry_signal)
                and self._source_entry_signal_active()
            )
            if roi_ignored:
                self.diagnostics["ichi_roi_ignored_active_signal_minutes"] += 1
            else:
                roi_ratio = self._ichi_roi_profit_ratio(elapsed)
                roi_target = entry * (1.0 + side * roi_ratio)
                roi_hit = (
                    float(bar.high) >= roi_target
                    if side > 0
                    else float(bar.low) <= roi_target
                )
                if roi_hit:
                    self._close_ichi_position(
                        "PUBLIC_ICHI_ROI_EXIT",
                        ts_event,
                        "ichi_roi_exits",
                        elapsed_minutes=elapsed,
                        roi_profit_ratio=roi_ratio,
                        roi_target=roi_target,
                        source_entry_signal_active=False,
                    )
                    return

        source_exit, snapshot = self._source_exit_signal()
        if source_exit:
            self._close_ichi_position(
                "PUBLIC_ICHI_SOURCE_SIGNAL_EXIT",
                ts_event,
                "ichi_source_signal_exits",
                elapsed_minutes=elapsed,
                **snapshot,
            )
            return

        # Update favorable excursion only after testing the old floor. A trail
        # armed by this minute is usable from the next completed minute.
        if bool(self.config.ichi_trailing_enabled):
            favorable_price = float(bar.high) if side > 0 else float(bar.low)
            if self._trail_best is None or not math.isfinite(float(self._trail_best)):
                self._trail_best = entry
            self._trail_best = (
                max(float(self._trail_best), favorable_price)
                if side > 0
                else min(float(self._trail_best), favorable_price)
            )
            favorable_fraction = (
                float(self._trail_best) / entry - 1.0
                if side > 0
                else entry / max(float(self._trail_best), 1e-12) - 1.0
            )
            offset = float(self.config.ichi_trailing_offset)
            if not self._trail_active and (
                favorable_fraction >= offset
                or not bool(self.config.ichi_trailing_only_offset_is_reached)
            ):
                self._trail_active = True
                self.diagnostics["ichi_trailing_activations"] += 1
                self._event(
                    "PUBLIC_ICHI_TRAILING_ACTIVATED",
                    ts_event,
                    favorable_fraction=favorable_fraction,
                    activation_fraction=offset,
                    trail_fraction=float(self.config.ichi_trailing_positive),
                    best_price=float(self._trail_best),
                    usable_from_next_complete_minute=True,
                )

        _ExecutionShell._manage_open_position(self, ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
