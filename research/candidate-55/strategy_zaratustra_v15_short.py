"""Execution provenance for the frozen V15 independent short family."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v15.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_short_execution", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

Candidate35Config = _BASE.Candidate35Config


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """Unchanged V15 execution with only the structurally rejected long side removed."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "candidate55_frozen_family": "V15_EDGE_EXACT_SHORT",
                "family_selection_intervals": [
                    ["2026-06-22", "2026-06-28"],
                    ["2026-07-22", "2026-07-28"],
                ],
                "fresh_validation_interval": ["2026-04-01", "2026-04-30"],
                "entry_thresholds_changed_after_selection": 0,
                "stop_or_trailing_changed_after_selection": 0,
                "short_only": 1,
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        super()._on_complete_universe_minute(ts_event)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-v15-edge-exact-short",
                    "candidate55_frozen_family": "V15_EDGE_EXACT_SHORT",
                    "short_only": 1,
                    "fresh_validation_interval": ["2026-04-01", "2026-04-30"],
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
