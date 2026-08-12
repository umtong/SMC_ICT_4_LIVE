"""Warmup-buffer repair for the hourly/4h CusTrend adapter.

The reused 5m shell caps its minute deque at 6,000 rows.  CusTrend needs at
least 50 complete 4h candles (12,000 minutes), so the first tournament could
never reach its router despite a 30-day replay.  This is an implementation
repair only; source signals and management are unchanged.
"""
from __future__ import annotations

from collections import deque
import importlib.util
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_custrend.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_custrend_buffer_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load CusTrend execution base: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

from strategy_base import SYMBOLS  # noqa: E402

Candidate35Config = _BASE.Candidate35Config


class Candidate35Strategy(_BASE.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=20_000)
            for symbol in SYMBOLS
        }
        self.diagnostics.update(
            {
                "hourly_warmup_buffer_repaired": 1,
                "minute_buffer_maxlen": 20_000,
                "first_zero_signal_run_was_implementation_invalid": 1,
            }
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
