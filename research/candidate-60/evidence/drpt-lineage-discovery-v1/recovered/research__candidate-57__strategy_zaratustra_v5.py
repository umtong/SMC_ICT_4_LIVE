"""NautilusTrader execution adapter for public ZaratustraV5.

The proven public-strategy shell supplies one global slot, matching, continuous
NAV, fees, adverse slippage, funding reserve and current-NAV 3% planned-loss
sizing.  This adapter supplies only the causal 5m/15m/30m source state and the
source trailing policy with one-minute, next-bar-usable ordering.
"""
from __future__ import annotations

from dataclasses import replace

from router import ZARA_STATE
from strategy_base import SYMBOLS
from strategy_picasso import (
    Candidate35Config as _PicassoConfig,
    Candidate35Strategy as _PicassoStrategy,
)


class Candidate35Config(_PicassoConfig, frozen=True):
    zara_trigger_mode: str = "level"
    zara_side_mode: str = "both"
    zara_risk_mode: str = "source_fraction"
    zara_rsi_period: int = 14
    zara_di_period: int = 14
    zara_bb_period: int = 20
    zara_rsi_threshold: float = 50.0
    zara_di_threshold: float = 25.0
    zara_source_stop_fraction: float = 0.0296
    zara_target_fraction: float = 0.20
    zara_structural_lookback_5m: int = 8
    zara_atr_period_5m: int = 14
    zara_stop_atr_buffer: float = 0.25
    zara_min_stop_fraction: float = 0.0015


class Candidate35Strategy(_PicassoStrategy):
    """One-slot Zaratustra account with causal one-minute trailing detail."""

    def __init__(self, config: Candidate35Config) -> None:
        if int(config.picasso_bucket_minutes) != 5:
            raise ValueError("ZaratustraV5 requires completed five-minute decisions")
        if abs(float(config.picasso_trailing_offset) - 0.0071) > 1e-12:
            raise ValueError("ZaratustraV5 underlying trailing activation must be 0.71%")
        if abs(float(config.picasso_trailing_positive) - 0.0013) > 1e-12:
            raise ValueError("ZaratustraV5 underlying trailing distance must be 0.13%")
        if abs(float(config.picasso_source_effective_leverage) - 1.0) > 1e-12:
            raise ValueError("source leverage must already be normalized to underlying price")
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            zara_trigger_mode=str(config.zara_trigger_mode),
            zara_side_mode=str(config.zara_side_mode),
            zara_risk_mode=str(config.zara_risk_mode),
            zara_rsi_period=int(config.zara_rsi_period),
            zara_di_period=int(config.zara_di_period),
            zara_bb_period=int(config.zara_bb_period),
            zara_rsi_threshold=float(config.zara_rsi_threshold),
            zara_di_threshold=float(config.zara_di_threshold),
            zara_source_stop_fraction=float(config.zara_source_stop_fraction),
            zara_target_fraction=float(config.zara_target_fraction),
            zara_structural_lookback_5m=int(config.zara_structural_lookback_5m),
            zara_atr_period_5m=int(config.zara_atr_period_5m),
            zara_stop_atr_buffer=float(config.zara_stop_atr_buffer),
            zara_min_stop_fraction=float(config.zara_min_stop_fraction),
        )
        self.diagnostics.update(
            {
                "candidate57_zaratustra_v5_adapter": 1,
                "external_source": "remiotore/ccxt-freqtrade ZaratustraV5.py; Freqle uniform report",
                "zara_trigger_mode": str(config.zara_trigger_mode),
                "zara_side_mode": str(config.zara_side_mode),
                "zara_risk_mode": str(config.zara_risk_mode),
                "zara_source_leverage": 10.0,
                "zara_source_stoploss_profit_ratio": 0.296,
                "zara_underlying_trailing_activation": 0.0071,
                "zara_underlying_trailing_distance": 0.0013,
                "zara_source_exit_enabled": 0,
                "zara_roi_enabled": 0,
                "zara_final_entry_blackouts": 0,
                "zara_one_minute_trailing_detail": 1,
                "zara_same_source_bar_trailing_fill_allowed": 0,
            }
        )

    def _roi_profit_ratio(self, elapsed_minutes: int) -> float:
        # The public source has no ROI table. A deliberately unreachable value
        # keeps the inherited execution branch inert without modifying its
        # causal trailing implementation.
        return 100.0

    def _source_exit_signal(self) -> tuple[bool, dict[str, float | int | str]]:
        return False, {"source_exit_enabled": 0}

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        final_cutoff = int(self.config.evaluation_end_ns) - 120_000_000_000
        if not open_symbols and not self.entry_pending and ts_event >= final_cutoff:
            self.minute_index += 1
            self.diagnostics["complete_universe_minutes"] += 1
            self._record_equity(ts_event)
            self.diagnostics["zara_final_entry_blackouts"] += 1
            return
        super()._on_complete_universe_minute(ts_event)
        if (
            self.current_scenario is not None
            and self.current_scenario.get("state") == ZARA_STATE
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-57-public-zaratustra-v5",
                    "source_trigger_mode": str(self.config.zara_trigger_mode),
                    "source_side_mode": str(self.config.zara_side_mode),
                    "source_risk_mode": str(self.config.zara_risk_mode),
                    "source_trailing_detail": "completed_1m_next_bar_usable",
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
