"""ZaratustraV15 provenance over the reused one-account trailing shell."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_reused_execution", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused Zaratustra execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

Candidate35Config = _BASE.Candidate35Config


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """V15 entries with validated continuous-account stop/trailing execution."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "external_source": (
                    "remiotore/ccxt-freqtrade:strategies/ZaratustraV15.py"
                ),
                "external_source_blob": (
                    "7f1e39e37949d732fa6b675b93fd808a73b8445c"
                ),
                "source_timeframes_minutes": [5],
                "source_policy": (
                    "DI_OBV_MFI_ATR_STATE_OR_BOLLINGER_BREAKOUT"
                ),
                "source_atr_literal_price_scale_bug_preserved_in_diagnostic": 1,
                "dimensionless_atr_repair_is_predeclared": 1,
                "project_eligible_DI_requires_rising_edge": 1,
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        super()._on_complete_universe_minute(ts_event)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-public-zaratustra-v15",
                    "source_file": "ZaratustraV15.py",
                    "source_blob": (
                        "7f1e39e37949d732fa6b675b93fd808a73b8445c"
                    ),
                    "source_timeframes_minutes": [5],
                    "source_entry_policy": (
                        "DI_OBV_MFI_ATR_STATE_OR_BOLLINGER_BREAKOUT"
                    ),
                    "declared_variant": str(
                        self.route_config.picasso_precedence_mode
                    ),
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
