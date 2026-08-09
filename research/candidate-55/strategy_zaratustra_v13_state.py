"""Causal management repair for the strongest ZaratustraV13 component.

The frozen public ``source_short`` entry produced positive development and
holdout weeks, then lost in May because two full 2.96% underlying stops erased
many smaller winners.  Both large losses originated from the source's DI-level
branch.  This module changes no entry rule.  It exits DI-origin positions when
the same complete-candle directional state no longer supports the trade.

``strict`` exits after any original DI clause fails; ``majority`` exits after
two fail.  Five- and fifteen-minute state clocks are compared.  Bollinger-edge
entries retain the source stop/trailing management unchanged.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Any

_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v13.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v13_state_execution", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V13 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

from router import (  # noqa: E402
    ZARATUSTRA_STATE,
    _adx_dx,
    _aggregate_complete,
    _directional_indicators,
)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    zaratustra_v13_state_exit_mode: str = "strict_5m"


def _decode_exit_mode(mode: str) -> tuple[int, int]:
    normalized = str(mode).strip().lower().replace("-", "_")
    if normalized.startswith("strict_"):
        failed_threshold = 1
    elif normalized.startswith("majority_"):
        failed_threshold = 2
    else:
        raise ValueError(f"unsupported V13 state mode: {mode}")
    if normalized.endswith("_5m"):
        timeframe = 5
    elif normalized.endswith("_15m"):
        timeframe = 15
    else:
        raise ValueError(f"unsupported V13 state timeframe: {mode}")
    return timeframe, failed_threshold


def _di_components(
    bars: tuple[Any, ...],
    *,
    timeframe: int,
    period: int,
    side: int,
) -> tuple[tuple[bool, bool, bool] | None, dict[str, float | int]]:
    candles = _aggregate_complete(bars, int(timeframe))
    minimum = 2 * int(period) + 2
    if len(candles) < minimum:
        return None, {
            "timeframe_minutes": int(timeframe),
            "candles": len(candles),
            "minimum": minimum,
        }
    plus_di, minus_di = _directional_indicators(candles, int(period))
    dx, adx = _adx_dx(plus_di, minus_di, int(period))
    index = len(candles) - 1
    values: dict[str, float | int] = {
        "state_ts": int(candles[index].ts_event),
        "timeframe_minutes": int(timeframe),
        "dx": float(dx[index]),
        "adx": float(adx[index]),
        "pdi": float(plus_di[index]),
        "mdi": float(minus_di[index]),
    }
    if not all(
        math.isfinite(float(values[name]))
        for name in ("dx", "adx", "pdi", "mdi")
    ):
        return None, values
    if int(side) < 0:
        # Preserve the public source's exact asymmetric short clauses.
        components = (
            float(values["dx"]) > float(values["mdi"]),
            float(values["adx"]) > float(values["pdi"]),
            float(values["mdi"]) > float(values["pdi"]),
        )
    else:
        components = (
            float(values["dx"]) > float(values["mdi"]),
            float(values["adx"]) > float(values["mdi"]),
            float(values["pdi"]) > float(values["mdi"]),
        )
    return components, values


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """Frozen V13 source-short entry plus component-specific invalidation."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        timeframe, threshold = _decode_exit_mode(
            config.zaratustra_v13_state_exit_mode
        )
        self._v13_state_timeframe = int(timeframe)
        self._v13_state_failed_threshold = int(threshold)
        self._last_v13_state_candle: int | None = None
        self.diagnostics.update(
            {
                "management_repair": "v13_di_component_invalidation",
                "entry_policy_frozen": "source_short",
                "zaratustra_v13_state_exit_mode": str(
                    config.zaratustra_v13_state_exit_mode
                ),
                "zaratustra_v13_state_exit_timeframe": timeframe,
                "zaratustra_v13_state_exit_failed_threshold": threshold,
                "zaratustra_v13_state_checks": 0,
                "zaratustra_v13_state_not_ready": 0,
                "zaratustra_v13_state_exits": 0,
                "zaratustra_v13_non_di_entries_untouched": 0,
                "zaratustra_v13_failed_component_histogram": {},
            }
        )

    def _manage_open_position(self, ts_event: int) -> None:
        scenario = self.current_scenario or {}
        diagnostics = scenario.get("diagnostics", {})
        used_di = (
            int(diagnostics.get("used_di_component", 0))
            if isinstance(diagnostics, dict)
            else 0
        )
        if (
            self.current_symbol is not None
            and scenario.get("state") == ZARATUSTRA_STATE
            and used_di == 1
        ):
            side = int(scenario.get("side", 0))
            components, values = _di_components(
                tuple(self.bars[self.current_symbol]),
                timeframe=self._v13_state_timeframe,
                period=int(self.config.zaratustra_di_period),
                side=side,
            )
            state_ts = int(values.get("state_ts", 0))
            if state_ts and state_ts != self._last_v13_state_candle:
                self._last_v13_state_candle = state_ts
                self.diagnostics["zaratustra_v13_state_checks"] += 1
                if components is None:
                    self.diagnostics["zaratustra_v13_state_not_ready"] += 1
                else:
                    failed = 3 - sum(int(value) for value in components)
                    histogram = self.diagnostics[
                        "zaratustra_v13_failed_component_histogram"
                    ]
                    key = str(failed)
                    histogram[key] = int(histogram.get(key, 0)) + 1
                    if failed >= self._v13_state_failed_threshold:
                        instrument_id = self.instrument_ids[self.current_symbol]
                        self.cancel_all_orders(instrument_id)
                        self.close_all_positions(instrument_id)
                        self.diagnostics["zaratustra_v13_state_exits"] += 1
                        self._event(
                            "ZARATUSTRA_V13_DI_STATE_INVALIDATION_EXIT",
                            ts_event,
                            state_exit_mode=str(
                                self.config.zaratustra_v13_state_exit_mode
                            ),
                            failed_components=failed,
                            dx_clause=int(components[0]),
                            adx_clause=int(components[1]),
                            dominance_clause=int(components[2]),
                            **values,
                        )
                        return
        elif (
            self.current_symbol is not None
            and scenario.get("state") == ZARATUSTRA_STATE
            and used_di == 0
            and self._last_v13_state_candle is None
        ):
            self.diagnostics["zaratustra_v13_non_di_entries_untouched"] += 1
            self._last_v13_state_candle = -1
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._last_v13_state_candle = None


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "_decode_exit_mode",
    "_di_components",
]
