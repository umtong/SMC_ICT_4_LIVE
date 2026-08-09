"""NautilusTrader execution for public CusTrend with source exits.

The mature Candidate 55 MBE2 shell is reused for continuous NAV, one global
slot, current-NAV 3% sizing, real Binance 1m execution, source ROI and causal
1m trailing.  This layer adds only CusTrend's complete-hour 5-bar exit signal.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_mbe2.py")
_SPEC = importlib.util.spec_from_file_location("candidate55_custrend_reused_execution", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused MBE2 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

from router import CUSTREND_STATE, _aggregate_complete, _rolling_shifted  # noqa: E402

Candidate35Config = _BASE.Candidate35Config


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """Source-faithful CusTrend policy over the reused one-account shell."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self._last_source_exit_candle: int | None = None
        self.diagnostics.update({
            "candidate": "candidate-55",
            "external_source": "remiotore/ccxt-freqtrade:strategies/CusTrend_coralTrend_Adx_EMA_Oct_1h.py",
            "external_source_blob": "f2f50e7e6fdc8505c9a0602f1b88aacb0f59d6e0",
            "source_timeframes_minutes": [60, 240],
            "source_exit_checks": 0,
            "source_exit_signals": 0,
            "complete_delayed_4h_ema_only": 1,
            "real_binance_1m_execution": 1,
        })

    def _source_exit(self) -> tuple[bool, dict[str, float | int]]:
        assert self.current_symbol is not None
        hourly = _aggregate_complete(tuple(self.bars[self.current_symbol]), 60)
        if len(hourly) < 25:
            return False, {"source_exit_not_ready": 1}
        index = len(hourly) - 1
        volumes = [float(candle.volume) for candle in hourly]
        means = _rolling_shifted(volumes, 19)
        scenario = self.current_scenario or {}
        side = int(scenario.get("side", 0))
        current = hourly[index]
        shifted = hourly[index - 5]
        values: dict[str, float | int] = {
            "source_exit_candle_ts": int(current.ts_event),
            "source_exit_close": float(current.close),
            "source_exit_shifted_low": float(shifted.low),
            "source_exit_shifted_high": float(shifted.high),
            "source_exit_volume": float(current.volume),
            "source_exit_volume_mean": float(means[index]),
            "source_exit_side": side,
        }
        if not math.isfinite(float(means[index])) or side not in (-1, 1):
            return False, values
        volume_ok = float(current.volume) > float(means[index])
        signal = (
            float(current.close) < float(shifted.low) and volume_ok
            if side > 0
            else float(current.close) > float(shifted.high) and volume_ok
        )
        return bool(signal), values

    def _manage_open_position(self, ts_event: int) -> None:
        scenario = self.current_scenario or {}
        if self.current_symbol is not None and scenario.get("state") == CUSTREND_STATE:
            signal, values = self._source_exit()
            candle_ts = int(values.get("source_exit_candle_ts", 0))
            if candle_ts and candle_ts != self._last_source_exit_candle:
                self._last_source_exit_candle = candle_ts
                self.diagnostics["source_exit_checks"] += 1
                if signal:
                    instrument_id = self.instrument_ids[self.current_symbol]
                    self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
                    self.diagnostics["source_exit_signals"] += 1
                    self._event("PUBLIC_CUSTREND_EXIT_SIGNAL", ts_event, **values)
                    return
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._last_source_exit_candle = None


__all__ = ["Candidate35Config", "Candidate35Strategy"]
