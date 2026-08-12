"""NautilusTrader execution for the public 1h UTBot completed solution.

The Candidate 55 MBE2 shell supplies one global slot, continuous NAV, real
Binance 1m fills, fees, adverse slippage, funding reserve, exact current-NAV
3% planned-loss sizing, ROI and causal 1m trailing.  This layer restores the
UTBot source's exact ROI timestamps and hourly EMA/volume exits.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_mbe2.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_utbot_reused_execution", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused one-account execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

from router import (  # noqa: E402
    UTBOT_STATE,
    _aggregate_complete,
    _rolling_shifted,
    _talib_ema,
)

Candidate35Config = _BASE.Candidate35Config


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """Public UTBot policy over the reused project execution contract."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self._roi_schedule = (
            (0, 0.133),
            (307, 0.099),
            (781, 0.053),
            (1856, 0.0),
        )
        self._last_source_exit_candle: int | None = None
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "external_source": (
                    "freqle/uploads/"
                    "UTBotAlert_Donchain_MultTrend_TradingStratAug2023_OnlyUT_1h.py"
                ),
                "external_source_page": "freqle.org/strategy/bf7dfb7eda706062",
                "source_timeframe_minutes": 60,
                "source_roi_schedule": {
                    "0": 0.133,
                    "307": 0.099,
                    "781": 0.053,
                    "1856": 0.0,
                },
                "source_exit_checks": 0,
                "source_exit_signals": 0,
                "exact_shifted_volume_mean": 1,
                "real_binance_1m_execution": 1,
                "source_maxdrawdown_protection_initially_omitted": 1,
                "source_stoploss_guard_initially_omitted": 1,
            }
        )

    def _source_exit(self) -> tuple[bool, dict[str, float | int]]:
        assert self.current_symbol is not None
        hourly = _aggregate_complete(tuple(self.bars[self.current_symbol]), 60)
        if len(hourly) < 120:
            return False, {"source_exit_not_ready": 1}
        index = len(hourly) - 1
        closes = [float(candle.close) for candle in hourly]
        volumes = [float(candle.volume) for candle in hourly]
        ema_long = _talib_ema(closes, 112)
        ema_short = _talib_ema(closes, 116)
        volume_long = _rolling_shifted(volumes, 16)
        volume_short = _rolling_shifted(volumes, 25)
        scenario = self.current_scenario or {}
        side = int(scenario.get("side", 0))
        current = hourly[index]
        values: dict[str, float | int] = {
            "source_exit_candle_ts": int(current.ts_event),
            "source_exit_close": float(current.close),
            "source_exit_ema_long_112": float(ema_long[index]),
            "source_exit_ema_short_116": float(ema_short[index]),
            "source_exit_volume": float(current.volume),
            "source_exit_volume_mean_long_16": float(volume_long[index]),
            "source_exit_volume_mean_short_25": float(volume_short[index]),
            "source_exit_side": side,
        }
        required = (
            ema_long[index], ema_short[index], volume_long[index], volume_short[index]
        )
        if side not in (-1, 1) or not all(
            math.isfinite(float(value)) for value in required
        ):
            return False, values
        signal = (
            float(current.close) < float(ema_long[index])
            and float(current.volume) > float(volume_long[index])
            if side > 0
            else float(current.close) > float(ema_short[index])
            and float(current.volume) > float(volume_short[index])
        )
        return bool(signal), values

    def _manage_open_position(self, ts_event: int) -> None:
        scenario = self.current_scenario or {}
        if (
            self.current_symbol is not None
            and scenario.get("state") == UTBOT_STATE
        ):
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
                    self._event("PUBLIC_UTBOT_1H_EXIT_SIGNAL", ts_event, **values)
                    return
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._last_source_exit_candle = None


__all__ = ["Candidate35Config", "Candidate35Strategy"]
