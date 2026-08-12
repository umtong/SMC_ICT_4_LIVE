"""Causal management repair for the public ``ZaratustraV5`` entry edge.

The source-faithful Candidate 55 V5 run found a useful but nearly break-even
rising-edge entry: six of seven recorded closed scenarios won, while one full
2.96% underlying stop erased the small trend-following winners.  The entry is
therefore held fixed and only the weak management component is replaced.

Each declared variant exits when the auction state that justified the entry is
no longer present on a complete 5m or 15m candle.  ``strict`` requires all three
source components (RSI side of 50, directional index above 25, close on the
correct side of the Bollinger middle) to remain valid.  ``majority`` tolerates
one failed component and exits once two fail.  The original source stop and
causal 1m trailing stop remain active as protection; no fitted price threshold
is introduced.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Any

_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_state_reused_execution", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused Zaratustra execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

from router import (  # noqa: E402
    ZARATUSTRA_STATE,
    _aggregate_complete,
    _indicator_pack,
)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    zaratustra_state_exit_mode: str = "strict_5m"


def _decode_exit_mode(mode: str) -> tuple[int, int]:
    """Return (timeframe minutes, failed-component threshold)."""
    normalized = str(mode).strip().lower().replace("-", "_")
    if normalized.startswith("strict_"):
        failed_threshold = 1
    elif normalized.startswith("majority_"):
        failed_threshold = 2
    else:
        raise ValueError(f"unsupported Zaratustra state-exit mode: {mode}")
    if normalized.endswith("_5m"):
        timeframe = 5
    elif normalized.endswith("_15m"):
        timeframe = 15
    elif normalized.endswith("_30m"):
        timeframe = 30
    else:
        raise ValueError(f"unsupported Zaratustra state-exit timeframe: {mode}")
    return timeframe, failed_threshold


def _state_components(
    bars: tuple[Any, ...],
    *,
    timeframe: int,
    side: int,
    rsi_period: int,
    di_period: int,
    bb_period: int,
) -> tuple[tuple[bool, bool, bool] | None, dict[str, float | int]]:
    """Evaluate the source state on the latest complete declared candle."""
    candles = _aggregate_complete(bars, int(timeframe))
    minimum = max(int(rsi_period) + 2, int(di_period) + 2, int(bb_period) + 1)
    if len(candles) < minimum:
        return None, {
            "timeframe_minutes": int(timeframe),
            "candles": len(candles),
            "minimum": minimum,
        }
    pack = _indicator_pack(
        candles,
        rsi_period=int(rsi_period),
        di_period=int(di_period),
        bb_period=int(bb_period),
    )
    index = len(candles) - 1
    values = {
        "state_ts": int(pack["ts"][index]),
        "timeframe_minutes": int(timeframe),
        "rsi": float(pack["rsi"][index]),
        "pdi": float(pack["pdi"][index]),
        "mdi": float(pack["mdi"][index]),
        "close": float(pack["close"][index]),
        "bbm": float(pack["bbm"][index]),
    }
    if not all(
        math.isfinite(float(values[name]))
        for name in ("rsi", "pdi", "mdi", "close", "bbm")
    ):
        return None, values
    if int(side) > 0:
        components = (
            float(values["rsi"]) > 50.0,
            float(values["pdi"]) > 25.0,
            float(values["close"]) > float(values["bbm"]),
        )
    else:
        components = (
            float(values["rsi"]) < 50.0,
            float(values["mdi"]) > 25.0,
            float(values["close"]) < float(values["bbm"]),
        )
    return components, values


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """Frozen edge entry with source-component state invalidation."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        timeframe, failed_threshold = _decode_exit_mode(
            config.zaratustra_state_exit_mode
        )
        self._state_exit_timeframe = int(timeframe)
        self._state_exit_failed_threshold = int(failed_threshold)
        self._last_state_exit_candle: int | None = None
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "management_repair": "causal_source_state_invalidation",
                "zaratustra_state_exit_mode": str(
                    config.zaratustra_state_exit_mode
                ),
                "zaratustra_state_exit_timeframe": timeframe,
                "zaratustra_state_exit_failed_threshold": failed_threshold,
                "zaratustra_state_exit_checks": 0,
                "zaratustra_state_exit_not_ready": 0,
                "zaratustra_state_exits": 0,
                "zaratustra_state_exit_failed_component_histogram": {},
                "entry_policy_frozen": "edge_both",
            }
        )

    def _manage_open_position(self, ts_event: int) -> None:
        scenario = self.current_scenario or {}
        if (
            self.current_symbol is not None
            and scenario.get("state") == ZARATUSTRA_STATE
        ):
            side = int(scenario.get("side", 0))
            if side in (-1, 1):
                components, values = _state_components(
                    tuple(self.bars[self.current_symbol]),
                    timeframe=self._state_exit_timeframe,
                    side=side,
                    rsi_period=int(self.config.zaratustra_rsi_period),
                    di_period=int(self.config.zaratustra_di_period),
                    bb_period=int(self.config.zaratustra_bb_period),
                )
                state_ts = int(values.get("state_ts", 0))
                # Evaluate once per complete management candle.  Repeated
                # minute callbacks may otherwise count the same state many times.
                if state_ts and state_ts != self._last_state_exit_candle:
                    self._last_state_exit_candle = state_ts
                    self.diagnostics["zaratustra_state_exit_checks"] += 1
                    if components is None:
                        self.diagnostics[
                            "zaratustra_state_exit_not_ready"
                        ] += 1
                    else:
                        failed = 3 - sum(int(value) for value in components)
                        histogram = self.diagnostics[
                            "zaratustra_state_exit_failed_component_histogram"
                        ]
                        key = str(failed)
                        histogram[key] = int(histogram.get(key, 0)) + 1
                        if failed >= self._state_exit_failed_threshold:
                            instrument_id = self.instrument_ids[
                                self.current_symbol
                            ]
                            self.cancel_all_orders(instrument_id)
                            self.close_all_positions(instrument_id)
                            self.diagnostics[
                                "zaratustra_state_exits"
                            ] += 1
                            self._event(
                                "ZARATUSTRA_CAUSAL_STATE_INVALIDATION_EXIT",
                                ts_event,
                                state_exit_mode=str(
                                    self.config.zaratustra_state_exit_mode
                                ),
                                failed_components=failed,
                                rsi_component=int(components[0]),
                                directional_component=int(components[1]),
                                band_component=int(components[2]),
                                **values,
                            )
                            return
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._last_state_exit_candle = None


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "_decode_exit_mode",
    "_state_components",
]
