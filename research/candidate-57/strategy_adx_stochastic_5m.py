"""NautilusTrader adapter for the public ADXStochastic five-minute strategy."""
from __future__ import annotations

from dataclasses import replace
import math

from router import ADX_STOCH_STATE, _adx, _aggregate_complete, _fast_stochastic
from strategy_base import SYMBOLS
from strategy_picasso import (
    Candidate35Config as _PicassoConfig,
    Candidate35Strategy as _PicassoStrategy,
)


class Candidate35Config(_PicassoConfig, frozen=True):
    adxstoch_risk_mode: str = "source_fraction"
    adxstoch_exit_mode: str = "literal"
    adxstoch_adx_period: int = 14
    adxstoch_fastk_period: int = 5
    adxstoch_fastd_period: int = 3
    adxstoch_entry_adx: float = 50.0
    adxstoch_entry_stoch: float = 20.0
    adxstoch_exit_adx: float = 25.0
    adxstoch_exit_stoch: float = 75.0
    adxstoch_source_stop_fraction: float = 0.10 / 9.0
    adxstoch_target_fraction: float = 0.05
    adxstoch_structural_lookback_5m: int = 8
    adxstoch_atr_period_5m: int = 14
    adxstoch_stop_atr_buffer: float = 0.25
    adxstoch_min_stop_fraction: float = 0.0015


class Candidate35Strategy(_PicassoStrategy):
    """One global-slot account preserving source ROI and declared exit semantics."""

    def __init__(self, config: Candidate35Config) -> None:
        if int(config.picasso_bucket_minutes) != 5:
            raise ValueError("ADXStochastic requires completed five-minute candles")
        mode = str(config.adxstoch_exit_mode).strip().lower()
        if mode not in {"literal", "corrected", "none"}:
            raise ValueError(f"unsupported adxstoch_exit_mode={mode!r}")
        if abs(float(config.picasso_source_effective_leverage) - 1.0) > 1e-12:
            raise ValueError("ADXStochastic leverage must be normalized to underlying price")
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            adxstoch_risk_mode=str(config.adxstoch_risk_mode),
            adxstoch_adx_period=int(config.adxstoch_adx_period),
            adxstoch_fastk_period=int(config.adxstoch_fastk_period),
            adxstoch_fastd_period=int(config.adxstoch_fastd_period),
            adxstoch_entry_adx=float(config.adxstoch_entry_adx),
            adxstoch_entry_stoch=float(config.adxstoch_entry_stoch),
            adxstoch_source_stop_fraction=float(config.adxstoch_source_stop_fraction),
            adxstoch_target_fraction=float(config.adxstoch_target_fraction),
            adxstoch_structural_lookback_5m=int(config.adxstoch_structural_lookback_5m),
            adxstoch_atr_period_5m=int(config.adxstoch_atr_period_5m),
            adxstoch_stop_atr_buffer=float(config.adxstoch_stop_atr_buffer),
            adxstoch_min_stop_fraction=float(config.adxstoch_min_stop_fraction),
        )
        self._adxstoch_roi_schedule = (
            (0, 0.04 / 9.0),
            (30, 0.02 / 9.0),
            (60, 0.01 / 9.0),
        )
        self.diagnostics.update(
            {
                "candidate57_adx_stochastic_adapter": 1,
                "external_source": "remiotore/ccxt-freqtrade ADXStochastic.py",
                "adxstoch_source_leverage": 9.0,
                "adxstoch_exit_mode": mode,
                "adxstoch_source_exit_duplicated_fastk_preserved": int(mode == "literal"),
                "adxstoch_source_signal_exits": 0,
                "adxstoch_roi_schedule_normalized_to_underlying": 1,
                "adxstoch_final_entry_blackouts": 0,
                "adxstoch_trailing_enabled": 0,
            }
        )

    def _roi_profit_ratio(self, elapsed_minutes: int) -> float:
        result = float(self._adxstoch_roi_schedule[0][1])
        for minute, value in self._adxstoch_roi_schedule:
            if elapsed_minutes >= minute:
                result = float(value)
            else:
                break
        return result

    def _source_exit_signal(self) -> tuple[bool, dict[str, float | int | str]]:
        mode = str(self.config.adxstoch_exit_mode).strip().lower()
        if mode == "none" or self.current_symbol is None:
            return False, {"source_exit_mode": mode, "source_exit_enabled": 0}
        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )
        minimum = max(
            int(self.config.adxstoch_adx_period) * 2 + 3,
            int(self.config.adxstoch_fastk_period)
            + int(self.config.adxstoch_fastd_period)
            + 3,
        )
        if len(candles) < minimum:
            return False, {"source_exit_ready": 0, "source_exit_mode": mode}
        adx = float(_adx(candles, int(self.config.adxstoch_adx_period))[-1])
        fastk, fastd = _fast_stochastic(
            candles,
            int(self.config.adxstoch_fastk_period),
            int(self.config.adxstoch_fastd_period),
        )
        k = float(fastk[-1])
        d = float(fastd[-1])
        if not all(math.isfinite(value) for value in (adx, k, d)):
            return False, {"source_exit_ready": 0, "source_exit_mode": mode}
        adx_clause = adx < float(self.config.adxstoch_exit_adx)
        k_clause = k > float(self.config.adxstoch_exit_stoch)
        d_clause = d > float(self.config.adxstoch_exit_stoch)
        exit_now = adx_clause and k_clause and (d_clause if mode == "corrected" else True)
        return bool(exit_now), {
            "source_exit_ready": 1,
            "source_exit_mode": mode,
            "source_exit_adx": adx,
            "source_exit_fastk": k,
            "source_exit_fastd": d,
            "source_exit_adx_clause": int(adx_clause),
            "source_exit_fastk_clause": int(k_clause),
            "source_exit_fastd_clause": int(d_clause),
            "source_exit_public_duplicate_fastk": int(mode == "literal"),
        }

    def _manage_open_position(self, ts_event: int) -> None:
        before = int(self.diagnostics.get("picasso_source_signal_exits", 0))
        super()._manage_open_position(ts_event)
        after = int(self.diagnostics.get("picasso_source_signal_exits", 0))
        if after > before:
            self.diagnostics["adxstoch_source_signal_exits"] += after - before

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
            self.diagnostics["adxstoch_final_entry_blackouts"] += 1
            return
        super()._on_complete_universe_minute(ts_event)
        if (
            self.current_scenario is not None
            and self.current_scenario.get("state") == ADX_STOCH_STATE
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-57-public-adx-stochastic",
                    "source_exit_mode": str(self.config.adxstoch_exit_mode),
                    "source_risk_mode": str(self.config.adxstoch_risk_mode),
                    "source_roi_normalization": "leveraged_profit_ratio_divided_by_9",
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
