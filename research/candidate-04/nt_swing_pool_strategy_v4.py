#!/usr/bin/env python3
"""Candidate-04 v7: reselect opposing liquidity when confirmation completes.

The first swing-pool replay detected many valid sweeps but submitted no orders.
The cause was mechanical: a nearby opposing pool selected on the sweep bar had
already been crossed by the time CHoCH confirmation closed, so the base risk gate
correctly rejected it as a target behind entry. This module changes one variable:
select the nearest still-active opposing pool *ahead of the confirmation price*
whose post-cost distance is sufficient. No outcome information is used.
"""
from __future__ import annotations

import math
from typing import Iterable

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import net_r_at_price
from nt_swing_pool_strategy import SwingPoolFailedAuctionStrategy


def select_causal_target(
    candidates: Iterable[float],
    entry: float,
    side: int,
    planned_loss_per_unit: float,
    cost_rate: float,
    minimum_net_r: float,
) -> float | None:
    """Return nearest directional target satisfying a predeclared net-R floor."""

    directional = sorted(
        (float(price) for price in candidates if side * (float(price) - entry) > 0.0),
        reverse=side < 0,
    )
    for price in directional:
        if net_r_at_price(
            entry,
            price,
            side,
            planned_loss_per_unit,
            cost_rate,
        ) >= minimum_net_r:
            return price
    return None


class _CausalTargetSwingPoolStrategy(SwingPoolFailedAuctionStrategy):
    TARGET_NET_R = 1.20

    def _try_confirm_pending(self, row: dict[str, float | int]) -> bool:
        had_pending = self.pending is not None
        handled = super()._try_confirm_pending(row)
        return handled or (had_pending and self.pending is None)

    def _submit_bracket(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        target_net_r: float,
        details: dict[str, object],
    ) -> bool:
        if setup.scenario != "CONFIRMED_SWING_POOL_FAILED_AUCTION":
            return LiquidityTransitionStrategy._submit_bracket(
                self,
                setup,
                row,
                target_net_r,
                details,
            )

        side = setup.side
        entry = float(row["close"])
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False
        stop = setup.extreme - side * self.config.stop_buffer_atr * atr
        price_loss = side * (entry - stop)
        if not math.isfinite(price_loss) or price_loss <= 0.0:
            return False
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        planned_loss = price_loss + cost_rate * (entry + stop)

        if side > 0:
            candidate_prices = (
                pool.level
                for pool in self.swing_pools
                if pool.active and pool.side > 0
            )
        else:
            candidate_prices = (
                pool.level
                for pool in self.swing_pools
                if pool.active and pool.side < 0
            )
        target = select_causal_target(
            candidate_prices,
            entry,
            side,
            planned_loss,
            cost_rate,
            self.TARGET_NET_R,
        )
        if target is None:
            self._event(
                "NO_CAUSAL_OPPOSING_TARGET",
                setup.scenario,
                row,
                {
                    **details,
                    "confirmation_entry": entry,
                    "planned_loss_per_unit": planned_loss,
                    "minimum_target_net_r": self.TARGET_NET_R,
                },
            )
            return False

        routed = PendingSetup(
            scenario=setup.scenario,
            side=setup.side,
            created_index=setup.created_index,
            expires_index=setup.expires_index,
            extreme=setup.extreme,
            structure=setup.structure,
            atr=setup.atr,
            target_reference=target,
            details=dict(setup.details),
        )
        return LiquidityTransitionStrategy._submit_bracket(
            self,
            routed,
            row,
            self.TARGET_NET_R,
            {
                **details,
                "causal_target_reselected": target,
                "minimum_target_net_r": self.TARGET_NET_R,
            },
        )


class SwingPoolCausalTarget12Strategy(_CausalTargetSwingPoolStrategy):
    TARGET_NET_R = 1.20


class SwingPoolCausalTarget16Strategy(_CausalTargetSwingPoolStrategy):
    TARGET_NET_R = 1.60


__all__ = [
    "LiquidityTransitionConfig",
    "SwingPoolCausalTarget12Strategy",
    "SwingPoolCausalTarget16Strategy",
    "select_causal_target",
]
