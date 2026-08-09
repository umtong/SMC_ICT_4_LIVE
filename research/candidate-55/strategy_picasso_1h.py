"""Candidate 55 NautilusTrader adapter for the public 1h Picasso family.

Execution, continuous NAV accounting, four-symbol global-slot arbitration,
fees, slippage, funding reserve and 3% risk sizing are inherited unchanged from
Candidate 51.  The materialized ``router`` module supplies Candidate 55's exact
level-versus-edge semantics.
"""
from __future__ import annotations

from strategy_picasso import Candidate35Config as Candidate35Config
from strategy_picasso import Candidate35Strategy as _ReusedPicassoStrategy


class Candidate35Strategy(_ReusedPicassoStrategy):
    """Thin provenance layer over the reused production-grade execution shell."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "external_source": (
                    "syuraj/freq-test:"
                    "RSI_BB_MACD_Nov_2023_1h_2_Dec_2/"
                    "RSI_BB_MACD_Nov_2023_1h_2_Dec.py"
                ),
                "source_timeframe": "1h",
                "source_level_reentry_tested": int(
                    "level" in str(config.picasso_precedence_mode).lower()
                ),
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        super()._on_complete_universe_minute(ts_event)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-55-public-picasso-1h",
                    "source_file": (
                        "RSI_BB_MACD_Nov_2023_1h_2_Dec.py"
                    ),
                    "source_trigger_mode": (
                        "level"
                        if "level" in str(self.config.picasso_precedence_mode).lower()
                        else "edge"
                    ),
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
