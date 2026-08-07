"""Candidate 05 v45b: corrected hybrid auction lifecycle and regime routing."""
from __future__ import annotations

from typing import Any

from strategy_v41_competing_auction import CompetingSweep
from strategy_v45_hybrid_auction import HybridAuctionRouterStrategy, HybridLevel


class CorrectedHybridAuctionStrategy(HybridAuctionRouterStrategy):
    """Consume each balance once and admit causal trend pullback rejections."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.v45b_consumed_balance_starts: set[int] = set()
        self.diagnostics.update(
            {
                "v45b_balanced_rejection_pass": 0,
                "v45b_trend_pullback_rejection_pass": 0,
                "v45b_countertrend_rejection_reject": 0,
            },
        )

    @staticmethod
    def _balance_start(level: HybridLevel) -> int | None:
        if level.source != "COMPLETED_20M_BALANCE":
            return None
        parts = level.level_id.split("-")
        try:
            return int(parts[1])
        except (IndexError, ValueError):
            return None

    def _balance_levels(self, atr: float) -> list[HybridLevel]:
        levels = super()._balance_levels(atr)
        return [
            level for level in levels
            if self._balance_start(level) not in self.v45b_consumed_balance_starts
        ]

    def _consume_hybrid(
        self,
        level: HybridLevel,
        owner: Any | None,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        start = self._balance_start(level)
        if start is not None:
            self.v45b_consumed_balance_starts.add(start)
        super()._consume_hybrid(level, owner, row, reason)

    def _arm_rejection(
        self,
        watch: CompetingSweep,
        row: dict[str, float | int],
        oi_change: float,
    ) -> None:
        regime, direction, _ = self._regime()
        rejection_side = -watch.sweep_direction
        if regime == "BALANCED":
            self.diagnostics["v45b_balanced_rejection_pass"] += 1
            # Call the v41 implementation directly because v44 would repeat the
            # same balanced check and reject the trend-pullback case below.
            from strategy_v41_competing_auction import CompetingAuctionStrategy
            CompetingAuctionStrategy._arm_rejection(self, watch, row, oi_change)
            return
        if regime == "DIRECTIONAL" and direction == rejection_side:
            self.diagnostics["v45b_trend_pullback_rejection_pass"] += 1
            from strategy_v41_competing_auction import CompetingAuctionStrategy
            CompetingAuctionStrategy._arm_rejection(self, watch, row, oi_change)
            return
        self.v41_watch = None
        self.diagnostics["v45b_countertrend_rejection_reject"] += 1


CandidateStrategy = CorrectedHybridAuctionStrategy
StrategyClass = CorrectedHybridAuctionStrategy
