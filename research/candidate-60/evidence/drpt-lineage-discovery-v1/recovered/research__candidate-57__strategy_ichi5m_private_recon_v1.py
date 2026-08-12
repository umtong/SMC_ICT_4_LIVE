"""NautilusTrader adapter for the private ``ichi_5m`` reconstruction.

Execution, matching, continuous NAV, one global slot, fees, adverse slippage,
funding reserve and current-NAV 3% planned-loss sizing are inherited from the
project's public-strategy shell. This module adds only the reconstructed source
state, source invalidation and declared tight-trailing option.
"""
from __future__ import annotations

from dataclasses import replace
import math

from router import ICHI5_STATE, _aggregate_complete, _arrays
from strategy_base import SYMBOLS
from strategy_picasso import (
    Candidate35Config as _PicassoConfig,
    Candidate35Strategy as _PicassoStrategy,
)


class Candidate35Config(_PicassoConfig, frozen=True):
    ichi5_entry_mode: str = "anchor_cloud"
    ichi5_side_mode: str = "long"
    ichi5_trigger_mode: str = "level"
    ichi5_risk_mode: str = "auction_structure"
    ichi5_min_fan_gain: float = 1.001
    ichi5_fan_shift_value: int = 2
    ichi5_target_fraction: float = 0.010
    ichi5_source_stop_fraction: float = 0.100
    ichi5_structural_lookback: int = 12
    ichi5_atr_period: int = 14
    ichi5_stop_atr_buffer: float = 0.25
    ichi5_min_stop_fraction: float = 0.0015
    ichi5_conversion_period: int = 9
    ichi5_base_period: int = 26
    ichi5_lagging_span_period: int = 52
    ichi5_displacement: int = 26
    ichi5_source_exit_mode: str = "anchor_or_fan_cross"
    ichi5_hidden_source_available: bool = False


class Candidate35Strategy(_PicassoStrategy):
    """One global-slot account running reconstructed one-minute ichi state."""

    def __init__(self, config: Candidate35Config) -> None:
        if int(config.picasso_bucket_minutes) != 1:
            raise ValueError("private ichi5m reconstruction requires one-minute candles")
        if str(config.ichi5_source_exit_mode) != "anchor_or_fan_cross":
            raise ValueError("unsupported private ichi5m source exit mode")
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            ichi5_entry_mode=str(config.ichi5_entry_mode),
            ichi5_side_mode=str(config.ichi5_side_mode),
            ichi5_trigger_mode=str(config.ichi5_trigger_mode),
            ichi5_risk_mode=str(config.ichi5_risk_mode),
            ichi5_min_fan_gain=float(config.ichi5_min_fan_gain),
            ichi5_fan_shift_value=int(config.ichi5_fan_shift_value),
            ichi5_target_fraction=float(config.ichi5_target_fraction),
            ichi5_source_stop_fraction=float(config.ichi5_source_stop_fraction),
            ichi5_structural_lookback=int(config.ichi5_structural_lookback),
            ichi5_atr_period=int(config.ichi5_atr_period),
            ichi5_stop_atr_buffer=float(config.ichi5_stop_atr_buffer),
            ichi5_min_stop_fraction=float(config.ichi5_min_stop_fraction),
            ichi5_conversion_period=int(config.ichi5_conversion_period),
            ichi5_base_period=int(config.ichi5_base_period),
            ichi5_lagging_span_period=int(config.ichi5_lagging_span_period),
            ichi5_displacement=int(config.ichi5_displacement),
        )
        self.diagnostics.update(
            {
                "candidate57_ichi5m_private_reconstruction_v1": 1,
                "external_source": "Strategy Ninja private ichi_5m indicator footprint and monthly outcomes",
                "ichi5_hidden_source_available": int(
                    bool(config.ichi5_hidden_source_available)
                ),
                "ichi5_entry_mode": str(config.ichi5_entry_mode),
                "ichi5_side_mode": str(config.ichi5_side_mode),
                "ichi5_trigger_mode": str(config.ichi5_trigger_mode),
                "ichi5_risk_mode": str(config.ichi5_risk_mode),
                "ichi5_source_exit_mode": str(config.ichi5_source_exit_mode),
                "ichi5_chikou_used": 0,
                "ichi5_source_signal_exits": 0,
                "ichi5_final_entry_blackouts": 0,
                "ichi5_entry_changed_after_freeze": 0,
                "ichi5_risk_changed_after_freeze": 0,
                "ichi5_management_changed_after_freeze": 0,
            }
        )

    def _source_exit_signal(self) -> tuple[bool, dict[str, float | int | str]]:
        if self.current_symbol is None or self.current_scenario is None:
            return False, {}
        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )
        required = max(
            100,
            int(self.route_config.ichi5_lagging_span_period)
            + int(self.route_config.ichi5_displacement)
            + 2,
        )
        if len(candles) < required:
            return False, {
                "source_exit_ready": 0,
                "source_exit_completed_candles": len(candles),
            }
        arrays = _arrays(candles, self.route_config)
        index = len(candles) - 1
        previous = index - 1
        current_ema12 = float(arrays["ema_close_12"][index])
        previous_ema12 = float(arrays["ema_close_12"][previous])
        current_open48 = float(arrays["ema_open_48"][index])
        previous_open48 = float(arrays["ema_open_48"][previous])
        current_fan = float(arrays["fan_magnitude"][index])
        previous_fan = float(arrays["fan_magnitude"][previous])
        values = (
            current_ema12,
            previous_ema12,
            current_open48,
            previous_open48,
            current_fan,
            previous_fan,
        )
        if not all(math.isfinite(value) for value in values):
            return False, {"source_exit_ready": 0}
        side = int(self.current_scenario.get("side", 0))
        if side > 0:
            anchor_cross = (
                current_ema12 < current_open48
                and previous_ema12 >= previous_open48
            )
            fan_cross = current_fan < 1.0 and previous_fan >= 1.0
        elif side < 0:
            anchor_cross = (
                current_ema12 > current_open48
                and previous_ema12 <= previous_open48
            )
            fan_cross = current_fan > 1.0 and previous_fan <= 1.0
        else:
            return False, {"source_exit_ready": 0}
        crossed = bool(anchor_cross or fan_cross)
        return crossed, {
            "source_exit_ready": 1,
            "source_exit_side": side,
            "source_exit_mode": str(self.config.ichi5_source_exit_mode),
            "source_exit_current_ema12": current_ema12,
            "source_exit_previous_ema12": previous_ema12,
            "source_exit_current_open48": current_open48,
            "source_exit_previous_open48": previous_open48,
            "source_exit_current_fan": current_fan,
            "source_exit_previous_fan": previous_fan,
            "source_exit_anchor_cross": int(anchor_cross),
            "source_exit_fan_cross": int(fan_cross),
        }

    def _manage_open_position(self, ts_event: int) -> None:
        before = int(self.diagnostics.get("picasso_source_signal_exits", 0))
        super()._manage_open_position(ts_event)
        after = int(self.diagnostics.get("picasso_source_signal_exits", 0))
        if after > before:
            self.diagnostics["ichi5_source_signal_exits"] += after - before

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        # The reused shell accepts signals through the inclusive evaluation-end
        # bar. Block only a flat account's final two completed minutes so a new
        # entry cannot be left open after the last matching cycle. Existing
        # positions and pending entries still pass to the shell for liquidation-
        # aware reconciliation and forced evaluation-end closure.
        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        final_cutoff = int(self.config.evaluation_end_ns) - 120_000_000_000
        if (
            not open_symbols
            and not self.entry_pending
            and ts_event >= final_cutoff
        ):
            self.minute_index += 1
            self.diagnostics["complete_universe_minutes"] += 1
            self._record_equity(ts_event)
            self.diagnostics["ichi5_final_entry_blackouts"] += 1
            return

        super()._on_complete_universe_minute(ts_event)
        if (
            self.current_scenario is not None
            and self.current_scenario.get("state") == ICHI5_STATE
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-57-private-ichi5m-recon-v1",
                    "source_reconstruction": True,
                    "source_hidden_code_available": False,
                    "source_entry_mode": str(self.config.ichi5_entry_mode),
                    "source_risk_mode": str(self.config.ichi5_risk_mode),
                    "source_exit_mode": str(self.config.ichi5_source_exit_mode),
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
