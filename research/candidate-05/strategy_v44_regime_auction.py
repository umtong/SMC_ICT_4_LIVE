"""Candidate 05 v44: higher-horizon regime-conditioned dual auction.

The v41 local sweep competition is preserved.  A completed 60-minute path
selects which causal result is admissible: balanced paths (efficiency <= 1/3)
may reject an edge, while directional paths (efficiency >= 1/2) may accept an
edge only in their established direction.  Intermediate paths are unresolved.
"""
from __future__ import annotations

import math
from typing import Any

from strategy_v41_competing_auction import CompetingAuctionStrategy, CompetingSweep


class RegimeConditionedAuctionStrategy(CompetingAuctionStrategy):
    REGIME_BARS = 60
    BALANCED_MAX_EFFICIENCY = 1.0 / 3.0
    DIRECTIONAL_MIN_EFFICIENCY = 1.0 / 2.0

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "v44_balanced_rejection_pass": 0,
                "v44_rejection_regime_reject": 0,
                "v44_directional_acceptance_pass": 0,
                "v44_acceptance_regime_reject": 0,
                "v44_intermediate_regime": 0,
            },
        )

    def _regime(self) -> tuple[str, int, float]:
        if len(self.bars) < self.REGIME_BARS + 1:
            return "NOT_READY", 0, math.nan
        rows = list(self.bars)[-self.REGIME_BARS:]
        path = sum(
            abs(float(rows[index]["close"]) - float(rows[index - 1]["close"]))
            for index in range(1, len(rows))
        )
        net_move = float(rows[-1]["close"]) - float(rows[0]["open"])
        efficiency = abs(net_move) / path if path > 0.0 else 0.0
        direction = 1 if net_move > 0.0 else -1 if net_move < 0.0 else 0
        if efficiency <= self.BALANCED_MAX_EFFICIENCY:
            return "BALANCED", direction, efficiency
        if efficiency >= self.DIRECTIONAL_MIN_EFFICIENCY:
            return "DIRECTIONAL", direction, efficiency
        return "INTERMEDIATE", direction, efficiency

    def _arm_rejection(self, watch: CompetingSweep, row: dict[str, float | int], oi_change: float) -> None:
        regime, _, efficiency = self._regime()
        if regime != "BALANCED":
            self.v41_watch = None
            if regime == "INTERMEDIATE":
                self.diagnostics["v44_intermediate_regime"] += 1
            self.diagnostics["v44_rejection_regime_reject"] += 1
            return
        self.diagnostics["v44_balanced_rejection_pass"] += 1
        super()._arm_rejection(watch, row, oi_change)

    def _arm_acceptance(self, watch: CompetingSweep, row: dict[str, float | int], oi_change: float) -> None:
        regime, direction, efficiency = self._regime()
        if regime != "DIRECTIONAL" or direction != watch.sweep_direction:
            self.v41_watch = None
            if regime == "INTERMEDIATE":
                self.diagnostics["v44_intermediate_regime"] += 1
            self.diagnostics["v44_acceptance_regime_reject"] += 1
            return
        self.diagnostics["v44_directional_acceptance_pass"] += 1
        super()._arm_acceptance(watch, row, oi_change)


CandidateStrategy = RegimeConditionedAuctionStrategy
StrategyClass = RegimeConditionedAuctionStrategy
