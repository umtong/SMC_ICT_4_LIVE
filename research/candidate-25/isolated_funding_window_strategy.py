"""Candidate 25 isolation overlay.

Configuration flags alone do not bypass Candidate 16-19's overridden auction
router.  This overlay closes that implementation gap at the single dispatch
point used by the shared base strategy: no sweep/auction episode can be
created.  Session rolling, pivots, natural liquidity pools, FOK execution,
position lifecycle, costs, portfolio accounting and NAV remain inherited.
"""
from __future__ import annotations

from typing import Any

from funding_window_strategy import Candidate25Config
from funding_window_strategy import Candidate25Strategy as _Candidate25Strategy


class Candidate25Strategy(_Candidate25Strategy):
    """Run only the funding-window reset family."""

    def __init__(self, config: Candidate25Config) -> None:
        super().__init__(config=config)
        self.diagnostics["candidate25_inherited_auction_detection_calls_blocked"] = 0

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        del row, previous_close
        self.diagnostics["candidate25_inherited_auction_detection_calls_blocked"] = int(
            self.diagnostics[
                "candidate25_inherited_auction_detection_calls_blocked"
            ],
        ) + 1
        return None


__all__ = ["Candidate25Config", "Candidate25Strategy"]
