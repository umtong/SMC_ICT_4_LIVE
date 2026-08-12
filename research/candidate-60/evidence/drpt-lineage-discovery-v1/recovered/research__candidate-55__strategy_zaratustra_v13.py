"""Thin provenance layer for ZaratustraV13 over the reused trailing shell."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v13_reused_execution", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused Zaratustra execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

Candidate35Config = _BASE.Candidate35Config


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """V13 policy with the already validated one-account trailing execution."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "external_source": (
                    "remiotore/ccxt-freqtrade:strategies/ZaratustraV13.py"
                ),
                "external_source_blob": (
                    "c8e46aa6b0164f6638c379e3cbd7ba7d9b28cd23"
                ),
                "source_timeframes_minutes": [5],
                "source_policy": "DI_LEVEL_OR_BOLLINGER_BREAKOUT_EDGE",
                "source_asymmetric_dx_clause_preserved": 1,
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        super()._on_complete_universe_minute(ts_event)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-public-zaratustra-v13",
                    "source_file": "ZaratustraV13.py",
                    "source_blob": (
                        "c8e46aa6b0164f6638c379e3cbd7ba7d9b28cd23"
                    ),
                    "source_timeframes_minutes": [5],
                    "source_entry_policy": (
                        "DI_LEVEL_OR_BOLLINGER_BREAKOUT_EDGE"
                    ),
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
