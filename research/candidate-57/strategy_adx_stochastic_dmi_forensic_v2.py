"""Behaviour-preserving DMI lifecycle instrumentation for ADXStochastic.

The public entry, source ROI, source exit, stop, sizing, arbitration and account
mechanics are inherited unchanged.  This wrapper only records whether a bullish
directional-state transition occurs before profit or large adverse excursion.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Sequence

from router import ADX_STOCH_STATE, _adx, _aggregate_complete
from router_picasso import BarObservation
from strategy_adx_stochastic_source_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Config(_BaseConfig, frozen=True):
    """No policy parameters are added by the forensic wrapper."""


def _directional_indicators(
    candles: Sequence[BarObservation], period: int
) -> tuple[list[float], list[float]]:
    size = len(candles)
    plus = [math.nan] * size
    minus = [math.nan] * size
    if period <= 0 or size <= period:
        return plus, minus
    true_range = [0.0] * size
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for index in range(1, size):
        current = candles[index]
        previous = candles[index - 1]
        high = float(current.high)
        low = float(current.low)
        previous_high = float(previous.high)
        previous_low = float(previous.low)
        previous_close = float(previous.close)
        true_range[index] = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
        up_move = high - previous_high
        down_move = previous_low - low
        plus_dm[index] = (
            up_move if up_move > down_move and up_move > 0.0 else 0.0
        )
        minus_dm[index] = (
            down_move if down_move > up_move and down_move > 0.0 else 0.0
        )
    tr_sum = sum(true_range[1 : period + 1])
    plus_sum = sum(plus_dm[1 : period + 1])
    minus_sum = sum(minus_dm[1 : period + 1])
    if tr_sum > 1e-12:
        plus[period] = 100.0 * plus_sum / tr_sum
        minus[period] = 100.0 * minus_sum / tr_sum
    for index in range(period + 1, size):
        tr_sum = tr_sum - tr_sum / period + true_range[index]
        plus_sum = plus_sum - plus_sum / period + plus_dm[index]
        minus_sum = minus_sum - minus_sum / period + minus_dm[index]
        if tr_sum > 1e-12:
            plus[index] = 100.0 * plus_sum / tr_sum
            minus[index] = 100.0 * minus_sum / tr_sum
    return plus, minus


class Candidate35Strategy(_BaseStrategy):
    _MFE_THRESHOLDS_R = (0.10, 0.25)
    _MAE_THRESHOLDS_R = (0.10, 0.25, 0.50)

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate57_adx_dmi_forensic_v2": 1,
                "adx_dmi_forensic_policy_changed": 0,
                "adx_dmi_source_state_checks": 0,
                "adx_dmi_bullish_crosses": 0,
                "adx_dmi_negative_pressure_weakenings": 0,
            }
        )

    def _forensic_diagnostics(self) -> dict[str, Any] | None:
        scenario = self.current_scenario
        if scenario is None or scenario.get("state") != ADX_STOCH_STATE:
            return None
        diagnostics = scenario.get("diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
            scenario["diagnostics"] = diagnostics
        return diagnostics

    @staticmethod
    def _threshold_key(prefix: str, threshold: float) -> str:
        text = f"{threshold:.2f}".replace(".", "p")
        return f"forensic_time_to_{prefix}_{text}r"

    def _update_path(self, ts_event: int) -> None:
        diagnostics = self._forensic_diagnostics()
        if diagnostics is None or self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        entry = float(scenario.get("entry_reference", 0.0))
        stop = float(scenario.get("stop", 0.0))
        if not math.isfinite(entry) or entry <= 0.0:
            return
        risk_fraction = abs(entry - stop) / entry
        if not math.isfinite(risk_fraction) or risk_fraction <= 1e-12:
            return
        elapsed = max(0, self.minute_index - self.position_open_minute)
        bar = self.bars[self.current_symbol][-1]
        favourable_fraction = max(0.0, float(bar.high) / entry - 1.0)
        adverse_fraction = max(0.0, 1.0 - float(bar.low) / entry)
        close_r = (float(bar.close) / entry - 1.0) / risk_fraction
        mfe_r = favourable_fraction / risk_fraction
        mae_r = adverse_fraction / risk_fraction
        diagnostics["forensic_elapsed_minutes"] = elapsed
        diagnostics["forensic_close_r_latest"] = close_r
        if mfe_r > float(diagnostics.get("forensic_mfe_r", -math.inf)):
            diagnostics["forensic_mfe_r"] = mfe_r
            diagnostics["forensic_mfe_r_minute"] = elapsed
        if mae_r > float(diagnostics.get("forensic_mae_r", -math.inf)):
            diagnostics["forensic_mae_r"] = mae_r
            diagnostics["forensic_mae_r_minute"] = elapsed
        for threshold in self._MFE_THRESHOLDS_R:
            key = self._threshold_key("mfe", threshold)
            if key not in diagnostics and mfe_r >= threshold:
                diagnostics[key] = elapsed
        for threshold in self._MAE_THRESHOLDS_R:
            key = self._threshold_key("mae", threshold)
            if key not in diagnostics and mae_r >= threshold:
                diagnostics[key] = elapsed

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 != 4:
            return
        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )
        period = int(self.config.adxstoch_adx_period)
        if len(candles) < period * 2 + 4:
            return
        plus, minus = _directional_indicators(candles, period)
        adx = _adx(candles, period)
        values = (
            float(plus[-1]),
            float(minus[-1]),
            float(plus[-2]),
            float(minus[-2]),
            float(adx[-1]),
            float(adx[-2]),
            float(adx[-4]),
        )
        if not all(math.isfinite(value) for value in values):
            return
        current_plus, current_minus = values[0], values[1]
        previous_plus, previous_minus = values[2], values[3]
        current_adx, previous_adx, adx_three_back = values[4], values[5], values[6]
        spread = current_plus - current_minus
        previous_spread = previous_plus - previous_minus
        bullish_state = current_plus > current_minus
        bullish_cross = bullish_state and previous_plus <= previous_minus
        negative_weakening = (
            current_minus < previous_minus and spread > previous_spread
        )

        diagnostics["forensic_dmi_checks"] = int(
            diagnostics.get("forensic_dmi_checks", 0)
        ) + 1
        self.diagnostics["adx_dmi_source_state_checks"] += 1
        if "forensic_entry_plus_di" not in diagnostics:
            diagnostics.update(
                {
                    "forensic_entry_dmi_elapsed_minute": elapsed,
                    "forensic_entry_plus_di": current_plus,
                    "forensic_entry_minus_di": current_minus,
                    "forensic_entry_dmi_spread": spread,
                    "forensic_entry_bullish_dmi_state": int(bullish_state),
                    "forensic_entry_adx": current_adx,
                    "forensic_entry_adx_slope_1": current_adx - previous_adx,
                    "forensic_entry_adx_slope_3": current_adx - adx_three_back,
                }
            )
        diagnostics.update(
            {
                "forensic_latest_plus_di": current_plus,
                "forensic_latest_minus_di": current_minus,
                "forensic_latest_dmi_spread": spread,
                "forensic_latest_adx": current_adx,
                "forensic_latest_adx_slope_1": current_adx - previous_adx,
                "forensic_latest_adx_slope_3": current_adx - adx_three_back,
            }
        )
        if (
            bullish_state
            and "forensic_first_bullish_dmi_state_minute" not in diagnostics
        ):
            diagnostics["forensic_first_bullish_dmi_state_minute"] = elapsed
            diagnostics["forensic_mark_r_at_first_bullish_dmi_state"] = close_r
            diagnostics["forensic_mfe_r_at_first_bullish_dmi_state"] = float(
                diagnostics["forensic_mfe_r"]
            )
            diagnostics["forensic_mae_r_at_first_bullish_dmi_state"] = float(
                diagnostics["forensic_mae_r"]
            )
        if bullish_cross and "forensic_first_bullish_dmi_cross_minute" not in diagnostics:
            diagnostics["forensic_first_bullish_dmi_cross_minute"] = elapsed
            diagnostics["forensic_mark_r_at_first_bullish_dmi_cross"] = close_r
            diagnostics["forensic_mfe_r_at_first_bullish_dmi_cross"] = float(
                diagnostics["forensic_mfe_r"]
            )
            diagnostics["forensic_mae_r_at_first_bullish_dmi_cross"] = float(
                diagnostics["forensic_mae_r"]
            )
            self.diagnostics["adx_dmi_bullish_crosses"] += 1
        if (
            negative_weakening
            and "forensic_first_negative_pressure_weakening_minute"
            not in diagnostics
        ):
            diagnostics[
                "forensic_first_negative_pressure_weakening_minute"
            ] = elapsed
            diagnostics[
                "forensic_mark_r_at_first_negative_pressure_weakening"
            ] = close_r
            diagnostics[
                "forensic_dmi_spread_at_first_negative_pressure_weakening"
            ] = spread
            self.diagnostics["adx_dmi_negative_pressure_weakenings"] += 1

    def _pretag_exit(self, ts_event: int) -> None:
        if self.current_scenario is None or self.current_symbol is None:
            return
        if self.current_scenario.get("state") != ADX_STOCH_STATE:
            return
        entry = float(self.current_scenario.get("entry_reference", 0.0))
        bar = self.bars[self.current_symbol][-1]
        elapsed = max(0, self.minute_index - self.position_open_minute)
        if math.isfinite(entry) and entry > 0.0:
            roi_fraction = float(self._roi_profit_ratio(elapsed))
            if float(bar.high) >= entry * (1.0 + roi_fraction):
                self.current_scenario["management_exit_reason"] = "SOURCE_ROI"
                return
        source_exit, _ = self._source_exit_signal()
        if source_exit:
            self.current_scenario["management_exit_reason"] = "SOURCE_SIGNAL"
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        before_funding = (
            moment.hour in (7, 15, 23)
            and moment.minute >= self.config.funding_flatten_minute
        )
        if before_funding:
            self.current_scenario["management_exit_reason"] = "FUNDING_FLATTEN"
        elif elapsed >= int(self.config.max_hold_minutes):
            self.current_scenario["management_exit_reason"] = "MAX_HOLD"
        elif ts_event >= int(self.config.evaluation_end_ns):
            self.current_scenario["management_exit_reason"] = "EVALUATION_END"

    def _manage_open_position(self, ts_event: int) -> None:
        self._update_path(ts_event)
        self._pretag_exit(ts_event)
        super()._manage_open_position(ts_event)

    def on_position_closed(self, event: Any) -> None:
        if (
            self.current_scenario is not None
            and self.current_scenario.get("state") == ADX_STOCH_STATE
        ):
            self.current_scenario.setdefault(
                "management_exit_reason", "SOURCE_STOP_OR_BRACKET"
            )
        super().on_position_closed(event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
