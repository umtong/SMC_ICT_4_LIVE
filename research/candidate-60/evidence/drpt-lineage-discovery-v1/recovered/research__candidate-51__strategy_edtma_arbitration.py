"""Strategy adapter for EDTMA cross-asset arbitration experiments."""
from __future__ import annotations

from dataclasses import replace

import strategy_edtma_repair as _base


class Candidate35Config(_base.Candidate35Config, frozen=True):
    edtma_arbitration_mode: str = "source_score"
    edtma_min_same_side_breadth: int = 1
    edtma_require_side_majority: bool = False
    edtma_require_btc_anchor: bool = False


class Candidate35Strategy(_base.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            edtma_arbitration_mode=str(config.edtma_arbitration_mode),
            edtma_min_same_side_breadth=int(config.edtma_min_same_side_breadth),
            edtma_require_side_majority=bool(config.edtma_require_side_majority),
            edtma_require_btc_anchor=bool(config.edtma_require_btc_anchor),
        )
        self.diagnostics.update(
            {
                "edtma_arbitration_mode": str(config.edtma_arbitration_mode),
                "edtma_min_same_side_breadth": int(config.edtma_min_same_side_breadth),
                "edtma_require_side_majority": int(config.edtma_require_side_majority),
                "edtma_require_btc_anchor": int(config.edtma_require_btc_anchor),
            }
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
