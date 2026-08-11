"""NautilusTrader execution adapter for public ZaratustraV15.

The project shell remains authoritative for one global slot, costs, matching,
continuous NAV and current-NAV 3% planned-loss sizing.  This adapter changes
only source entry semantics and source-normalized stop/trailing values.  The
public strategy has no ROI table and no source exit signal.
"""
from __future__ import annotations

from dataclasses import replace

from router import Z15_STATE
from strategy_base import SYMBOLS
from strategy_picasso import (
    Candidate35Config as _PicassoConfig,
    Candidate35Strategy as _PicassoStrategy,
)


class Candidate35Config(_PicassoConfig, frozen=True):
    z15_family: str = "combined"
    z15_trigger_mode: str = "source"
    z15_dmi_period: int = 14
    z15_mfi_period: int = 14
    z15_atr_period: int = 14
    z15_bb_period: int = 20
    z15_bb_stds: float = 2.0
    z15_mfi_midpoint: float = 50.0
    z15_atr_absolute_max: float = 0.2
    z15_stop_fraction: float = 0.015
    z15_emergency_objective_fraction: float = 0.20


class Candidate35Strategy(_PicassoStrategy):
    """One-slot V15 source adapter with causal one-minute trailing detail."""

    def __init__(self, config: Candidate35Config) -> None:
        if int(config.picasso_bucket_minutes) != 5:
            raise ValueError("ZaratustraV15 requires completed five-minute decisions")
        if abs(float(config.picasso_trailing_offset) - 0.0107) > 1e-12:
            raise ValueError("ZaratustraV15 trailing activation must be 1.07% underlying")
        if abs(float(config.picasso_trailing_positive) - 0.0012) > 1e-12:
            raise ValueError("ZaratustraV15 trailing distance must be 0.12% underlying")
        if abs(float(config.picasso_source_effective_leverage) - 1.0) > 1e-12:
            raise ValueError("source leverage must already be normalized to underlying price")
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            z15_family=str(config.z15_family),
            z15_trigger_mode=str(config.z15_trigger_mode),
            z15_dmi_period=int(config.z15_dmi_period),
            z15_mfi_period=int(config.z15_mfi_period),
            z15_atr_period=int(config.z15_atr_period),
            z15_bb_period=int(config.z15_bb_period),
            z15_bb_stds=float(config.z15_bb_stds),
            z15_mfi_midpoint=float(config.z15_mfi_midpoint),
            z15_atr_absolute_max=float(config.z15_atr_absolute_max),
            z15_stop_fraction=float(config.z15_stop_fraction),
            z15_emergency_objective_fraction=float(
                config.z15_emergency_objective_fraction
            ),
        )
        self.diagnostics.update(
            {
                "candidate60_zaratustra_v15_adapter": 1,
                "external_source": "remiotore/ccxt-freqtrade strategies/ZaratustraV15.py",
                "z15_family": str(config.z15_family),
                "z15_trigger_mode": str(config.z15_trigger_mode),
                "z15_source_leverage": 10.0,
                "z15_source_stoploss_profit_ratio": 0.15,
                "z15_underlying_stop_fraction": 0.015,
                "z15_underlying_trailing_activation": 0.0107,
                "z15_underlying_trailing_distance": 0.0012,
                "z15_source_exit_enabled": 0,
                "z15_roi_enabled": 0,
                "z15_one_minute_trailing_detail": 1,
                "z15_same_source_bar_trailing_fill_allowed": 0,
                "z15_policy_changed_risk_or_costs": 0,
            }
        )

    def _roi_profit_ratio(self, elapsed_minutes: int) -> float:
        return 100.0

    def _source_exit_signal(self) -> tuple[bool, dict[str, float | int | str]]:
        return False, {"source_exit_enabled": 0}

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        super()._on_complete_universe_minute(ts_event)
        if (
            self.current_scenario is not None
            and self.current_scenario.get("state") == Z15_STATE
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-60-public-zaratustra-v15",
                    "source_family": str(self.config.z15_family),
                    "source_trigger_mode": str(self.config.z15_trigger_mode),
                    "source_trailing_detail": "completed_1m_next_bar_usable",
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
