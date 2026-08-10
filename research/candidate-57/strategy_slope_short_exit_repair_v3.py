"""Structural repair for the public Slope-is-Dope short source exit.

The public short condition compares close with a prior rolling *minimum* low,
which is almost continuously true.  This wrapper exposes a symmetric rolling
maximum-high invalidation or an MA-cross-only interpretation.  Entries, long
exits, ROI values, trailing, stops, sizing and account mechanics are inherited.
The already-identified v1 ROI-order defect is repaired here as part of the v2
mechanical baseline so this experiment actually reaches the source exit.
"""
from __future__ import annotations

import math

from router import _aggregate_complete, _sma
from strategy_slope_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Config(_BaseConfig, frozen=True):
    slope_short_exit_mode: str = "symmetric_high"


class Candidate35Strategy(_BaseStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        mode = str(config.slope_short_exit_mode).strip().lower()
        if mode not in {"symmetric_high", "ma_only"}:
            raise ValueError(f"unsupported slope_short_exit_mode={mode!r}")
        super().__init__(config)
        # V2 mechanical baseline: `_roi_profit_ratio` expects ascending elapsed
        # minutes. Without this reset the terminal zero-ROI row is selected at
        # entry and the short-exit experiment is never reached.
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
                )
            )
        )
        self.diagnostics.update(
            {
                "candidate57_slope_short_exit_repair_v3": 1,
                "candidate57_slope_roi_schedule_fix_v2": 1,
                "slope_roi_schedule_order": "ascending_elapsed_minutes",
                "slope_short_exit_mode": mode,
                "slope_short_literal_minimum_low_used": 0,
                "slope_long_source_exit_changed": 0,
                "slope_entry_changed": 0,
                "slope_risk_or_management_changed": 0,
            }
        )

    def _source_exit_signal(self) -> tuple[bool, dict[str, float | int | str]]:
        if self.current_symbol is None or self.current_scenario is None:
            return False, {}
        side = int(self.current_scenario.get("side", 0))
        if side > 0:
            # Preserve the public long exit exactly in the base adapter.
            return super()._source_exit_signal()
        if side != -1:
            return False, {"source_exit_ready": 0}

        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )
        short_window = int(self.config.slope_exit_rolling_short)
        required = max(
            int(self.config.slope_fast_ma_period),
            int(self.config.slope_slow_ma_period),
            short_window + 1,
        ) + 1
        if len(candles) < required:
            return False, {
                "source_exit_ready": 0,
                "source_exit_candles": len(candles),
            }

        closes = [float(candle.close) for candle in candles]
        highs = [float(candle.high) for candle in candles]
        fast = float(_sma(closes, int(self.config.slope_fast_ma_period))[-1])
        slow = float(_sma(closes, int(self.config.slope_slow_ma_period))[-1])
        close = float(closes[-1])
        prior_high_short = max(highs[-short_window - 1 : -1])
        if not all(
            math.isfinite(value)
            for value in (fast, slow, close, prior_high_short)
        ):
            return False, {"source_exit_ready": 0}

        ma_exit = fast > slow
        mode = str(self.config.slope_short_exit_mode).strip().lower()
        symmetric_exit = close > prior_high_short
        rolling_exit = symmetric_exit if mode == "symmetric_high" else False
        return bool(ma_exit or rolling_exit), {
            "source_exit_ready": 1,
            "source_exit_side": side,
            "source_exit_mode": mode,
            "source_exit_close": close,
            "source_exit_fast_ma": fast,
            "source_exit_slow_ma": slow,
            "source_exit_prior_high_short": prior_high_short,
            "source_exit_ma_condition": int(ma_exit),
            "source_exit_symmetric_high_condition": int(symmetric_exit),
            "source_exit_applied_rolling_condition": int(rolling_exit),
        }


__all__ = ["Candidate35Config", "Candidate35Strategy"]
