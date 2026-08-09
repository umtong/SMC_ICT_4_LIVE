"""Exact zero-ROI completion for the Candidate 55 UTBot execution layer."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_utbot_1h.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_utbot_exact_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load UTBot execution base: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

from router import UTBOT_STATE  # noqa: E402

Candidate35Config = _BASE.Candidate35Config


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """Add Freqtrade's final ``1856: 0`` ROI breakeven behavior."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics["source_zero_roi_exits"] = 0

    def _manage_open_position(self, ts_event: int) -> None:
        scenario = self.current_scenario or {}
        if (
            self.current_symbol is not None
            and scenario.get("state") == UTBOT_STATE
        ):
            elapsed = max(0, self.minute_index - self.position_open_minute)
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", 0.0))
            if elapsed >= 1856 and side in (-1, 1) and math.isfinite(entry) and entry > 0.0:
                bar = self.bars[self.current_symbol][-1]
                breakeven_touched = (
                    float(bar.high) >= entry if side > 0 else float(bar.low) <= entry
                )
                if breakeven_touched:
                    instrument_id = self.instrument_ids[self.current_symbol]
                    self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
                    self.diagnostics["source_zero_roi_exits"] += 1
                    self._event(
                        "PUBLIC_UTBOT_ZERO_ROI_EXIT",
                        ts_event,
                        elapsed_minutes=elapsed,
                        roi_profit_ratio=0.0,
                        entry_reference=entry,
                    )
                    return
        super()._manage_open_position(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
