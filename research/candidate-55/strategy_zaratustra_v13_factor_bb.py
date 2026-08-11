"""Provenance wrapper for the V13 broad-factor accepted BB short policy."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v13.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v13_factor_execution", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused V13 execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

Candidate35Config = _BASE.Candidate35Config


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """Source BB edge retained only inside accepted broad downside repricing."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "source_policy": "V13_BB_EDGE_WITH_BROAD_30M_FACTOR_ACCEPTANCE",
                "factor_gate_policy": (
                    "at_least_three_of_four_assets_down_and_median_30m_return_negative"
                ),
                "factor_gate_optimized_thresholds": 0,
                "factor_gate_lookback_minutes": 30,
                "factor_gate_min_down_assets": 3,
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        super()._on_complete_universe_minute(ts_event)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-zaratustra-v13-factor-bb",
                    "integrated_policy": (
                        "public_V13_BB_short_plus_broad_30m_factor_acceptance"
                    ),
                    "factor_gate_optimized_thresholds": 0,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
