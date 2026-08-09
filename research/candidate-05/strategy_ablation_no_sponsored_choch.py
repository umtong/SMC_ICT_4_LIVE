#!/usr/bin/env python3
"""Diagnostic ablation: remove only sponsored CHoCH immediate participation."""
from __future__ import annotations

from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_v13 import TargetLiquidityHandoffStrategy
from strategy_v16 import PositionBuildingBalanceAcceptanceStrategy


class NoSponsoredChochParticipationAblationStrategy(
    PositionBuildingBalanceAcceptanceStrategy,
):
    """Route every reversal through the pre-v14 one-minute path observation.

    The position-building balance-acceptance branch remains active. Liquidity
    pools, failed-auction sweep classification, sweep-tail flow, directional
    depth, CHoCH displacement and CHoCH flow approval are unchanged. The only
    removed variable is v14/v15's bounded immediate participation at an active
    CHoCH; approved reversals instead use v13/v12/v9's retrace-or-breakaway
    observation path.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics["ablation_sponsored_choch_routed_to_observation"] = 0

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        self.diagnostics["ablation_sponsored_choch_routed_to_observation"] += 1
        return TargetLiquidityHandoffStrategy._submit_entry(self, setup, row)


__all__ = ["NoSponsoredChochParticipationAblationStrategy"]
