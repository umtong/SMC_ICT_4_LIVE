"""NautilusTrader adapter for EdgeBot 4σ rolling-VWAP reconstruction."""
from __future__ import annotations

from dataclasses import replace
import math

from router import EDGE_STATE, _aggregate_complete, _snapshot
from strategy_base import SYMBOLS
from strategy_picasso import (
    Candidate35Config as _PicassoConfig,
    Candidate35Strategy as _PicassoStrategy,
)


class Candidate35Config(_PicassoConfig, frozen=True):
    edge_signal_mode: str = "weighted_band"
    edge_scope: str = "all"
    edge_mean_exit_mode: str = "static_entry_mean"
    edge_risk_mode: str = "six_sigma"
    edge_vwap_period: int = 20
    edge_entry_sigma: float = 4.0
    edge_stop_sigma: float = 6.0
    edge_residual_window: int = 20
    edge_atr_period: int = 14
    edge_stop_atr_buffer: float = 0.25
    edge_min_stop_fraction: float = 0.0015
    edge_dynamic_emergency_target_fraction: float = 0.20


class Candidate35Strategy(_PicassoStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        if int(config.picasso_bucket_minutes) != 15:
            raise ValueError("EdgeBot reconstruction requires completed 15-minute candles")
        if abs(float(config.edge_entry_sigma) - 4.0) > 1e-12:
            raise ValueError("public EdgeBot threshold is frozen at four sigma")
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            edge_signal_mode=str(config.edge_signal_mode),
            edge_scope=str(config.edge_scope),
            edge_mean_exit_mode=str(config.edge_mean_exit_mode),
            edge_risk_mode=str(config.edge_risk_mode),
            edge_vwap_period=int(config.edge_vwap_period),
            edge_entry_sigma=float(config.edge_entry_sigma),
            edge_stop_sigma=float(config.edge_stop_sigma),
            edge_residual_window=int(config.edge_residual_window),
            edge_atr_period=int(config.edge_atr_period),
            edge_stop_atr_buffer=float(config.edge_stop_atr_buffer),
            edge_min_stop_fraction=float(config.edge_min_stop_fraction),
            edge_dynamic_emergency_target_fraction=float(
                config.edge_dynamic_emergency_target_fraction
            ),
        )
        self.diagnostics.update(
            {
                "candidate57_edgebot_vwap_4sigma": 1,
                "external_source": "EdgeBot Lab mr_meanrev_v3 public strategy description",
                "edge_signal_mode": str(config.edge_signal_mode),
                "edge_scope": str(config.edge_scope),
                "edge_mean_exit_mode": str(config.edge_mean_exit_mode),
                "edge_risk_mode": str(config.edge_risk_mode),
                "edge_entry_sigma": float(config.edge_entry_sigma),
                "edge_dynamic_mean_exits": 0,
                "edge_final_entry_blackouts": 0,
                "edge_public_stop_known": 0,
                "edge_public_sigma_estimator_known": 0,
            }
        )

    def _roi_profit_ratio(self, elapsed_minutes: int) -> float:
        return 100.0

    def _source_exit_signal(self) -> tuple[bool, dict[str, float | int | str]]:
        mode = str(self.config.edge_mean_exit_mode).strip().lower()
        if mode == "static_entry_mean" or self.current_symbol is None or self.current_scenario is None:
            return False, {"source_exit_mode": mode}
        if mode != "dynamic_mean":
            raise ValueError(f"unsupported edge_mean_exit_mode={mode!r}")
        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )
        required = max(
            int(self.route_config.edge_vwap_period)
            + int(self.route_config.edge_residual_window)
            + 2,
            int(self.route_config.edge_atr_period) + 2,
        )
        if len(candles) < required:
            return False, {"source_exit_ready": 0, "source_exit_mode": mode}
        snap = _snapshot(candles, self.route_config)
        if snap is None:
            return False, {"source_exit_ready": 0, "source_exit_mode": mode}
        close = float(candles[-1].close)
        vwap = float(snap["source_vwap"])
        side = int(self.current_scenario.get("side", 0))
        crossed = close >= vwap if side > 0 else close <= vwap if side < 0 else False
        return bool(crossed), {
            "source_exit_ready": 1,
            "source_exit_mode": mode,
            "source_exit_side": side,
            "source_exit_close": close,
            "source_exit_current_vwap": vwap,
            "source_exit_crossed_mean": int(crossed),
        }

    def _manage_open_position(self, ts_event: int) -> None:
        before = int(self.diagnostics.get("picasso_source_signal_exits", 0))
        super()._manage_open_position(ts_event)
        after = int(self.diagnostics.get("picasso_source_signal_exits", 0))
        if after > before:
            self.diagnostics["edge_dynamic_mean_exits"] += after - before

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        open_symbols = [
            symbol for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        final_cutoff = int(self.config.evaluation_end_ns) - 120_000_000_000
        if not open_symbols and not self.entry_pending and ts_event >= final_cutoff:
            self.minute_index += 1
            self.diagnostics["complete_universe_minutes"] += 1
            self._record_equity(ts_event)
            self.diagnostics["edge_final_entry_blackouts"] += 1
            return
        super()._on_complete_universe_minute(ts_event)
        if self.current_scenario is not None and self.current_scenario.get("state") == EDGE_STATE:
            self.current_scenario.update(
                {
                    "candidate": "candidate-57-edgebot-vwap-4sigma",
                    "source_signal_mode": str(self.config.edge_signal_mode),
                    "source_scope": str(self.config.edge_scope),
                    "source_mean_exit_mode": str(self.config.edge_mean_exit_mode),
                    "source_risk_mode": str(self.config.edge_risk_mode),
                    "source_claim_reconstruction": True,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
