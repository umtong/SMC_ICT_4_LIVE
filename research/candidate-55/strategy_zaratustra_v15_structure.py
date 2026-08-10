"""Execution provenance for the V15 structural short repair."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v15.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_structure_execution", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V15 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

Candidate35Config = _BASE.Candidate35Config


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """Unchanged source execution with only the broken state routing replaced."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "candidate55_family": "V15_STRUCTURAL_SHORT_REPAIR",
                "opportunity_engine_changed": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "risk_sizing_changed": 0,
                "repair_scope": (
                    "DI_PULLBACK_RESUMPTION_OR_"
                    "BB_CLEAN_SYNCHRONIZED_EXPANSION"
                ),
                "repair_development_intervals": [
                    ["2026-04-01", "2026-04-30"],
                    ["2026-06-22", "2026-06-28"],
                    ["2026-07-22", "2026-07-28"],
                ],
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        super()._on_complete_universe_minute(ts_event)
        if self.current_scenario is not None:
            diagnostics = dict(self.current_scenario.get("diagnostics", {}))
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-v15-structural-short",
                    "candidate55_family": "V15_STRUCTURAL_SHORT_REPAIR",
                    "candidate55_scenario_family": diagnostics.get(
                        "candidate55_scenario_family",
                        "UNRESOLVED",
                    ),
                    "state_repair_only": 1,
                    "source_management_preserved": 1,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
