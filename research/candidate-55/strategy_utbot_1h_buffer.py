"""Warmup-buffer repair for the public UTBot 1h adapter."""
from __future__ import annotations

from collections import deque
import importlib.util
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_utbot_1h_exact.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_utbot_buffer_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load UTBot execution base: {_BASE_PATH}")
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
            }
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
