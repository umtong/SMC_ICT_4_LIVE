#!/usr/bin/env python3
"""Candidate-04 v6: confirmed swing-pool failed-auction reversal.

The prior-session range candidate was too sparse and repeatedly interpreted the
same boundary. This strategy replaces rolling/session repetition with a causal
registry of confirmed swing liquidity pools. A pool is knowable only after the
right-hand pivot bars close, is consumed on its first meaningful penetration,
and can produce at most one parent event.

Orders, fills, contingent-order behavior, fees, positions, account balances and
NAV remain entirely owned by NautilusTrader.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup


@dataclass(slots=True)
class SwingPool:
    pool_id: int
    side: int  # +1 buy-side/high liquidity, -1 sell-side/low liquidity
    level: float
    observed_index: int
    last_pivot_index: int
    touches: int
    prominence_atr: float
    active: bool = True


class SwingPoolFailedAuctionStrategy(LiquidityTransitionStrategy):
    """Trade one causal failed auction per confirmed swing-liquidity pool."""

    PIVOT_LEFT = 3
    PIVOT_RIGHT = 3
    POOL_MERGE_ATR = 0.18
    POOL_MIN_AGE_BARS = 15
    POOL_MAX_AGE_BARS = 480
    POOL_MIN_PROMINENCE_ATR = 0.08
    SWEEP_MIN_ATR = 0.05
    SWEEP_MAX_ATR = 1.20
    MAX_AUCTION_EFFICIENCY_120 = 0.45
    CONFIRMATION_BODY_ATR = 0.45
    CONFIRMATION_VOLUME_BURST = 1.20
    CONFIRMATION_CLOSE_LOCATION = 0.68
    TARGET_NET_R = 1.60

    def __init__(self, config: LiquidityTransitionConfig) -> None:
        super().__init__(config)
        self.swing_pools: list[SwingPool] = []
        self.next_pool_id = 0
        self.last_registered_center = -1

    def on_bar(self, bar: Any) -> None:
        # The base method owns bar ingestion, portfolio gating and Nautilus order
        # handling. Pivot registration occurs afterward, so a pivot cannot be used
        # on the same bar which confirms it.
        super().on_bar(bar)
        self._register_confirmed_pivot()

    def _register_confirmed_pivot(self) -> None:
        rows = list(self.bars)
        width = self.PIVOT_LEFT + self.PIVOT_RIGHT + 1
        if len(rows) < width or self.bar_index < self.PIVOT_RIGHT:
            return
        center_global = self.bar_index - self.PIVOT_RIGHT
        if center_global <= self.last_registered_center:
            return
        self.last_registered_center = center_global

        window = rows[-width:]
        center = self.PIVOT_LEFT
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return

        center_high = float(window[center]["high"])
        center_low = float(window[center]["low"])
        left = window[:center]
        right = window[center + 1 :]
        candidates: list[tuple[int, float, float]] = []

        highs = [float(item["high"]) for item in window]
        if center_high == max(highs) and highs.count(center_high) == 1:
            prominence = min(
                center_high - max(float(item["high"]) for item in left),
                center_high - max(float(item["high"]) for item in right),
            ) / atr
            candidates.append((1, center_high, prominence))

        lows = [float(item["low"]) for item in window]
        if center_low == min(lows) and lows.count(center_low) == 1:
            prominence = min(
                min(float(item["low"]) for item in left) - center_low,
                min(float(item["low"]) for item in right) - center_low,
            ) / atr
            candidates.append((-1, center_low, prominence))

        for side, price, prominence in candidates:
            nearby = [
                pool
                for pool in self.swing_pools
                if pool.active
                and pool.side == side
                and abs(pool.level - price) <= self.POOL_MERGE_ATR * atr
            ]
            if nearby:
                pool = min(nearby, key=lambda item: abs(item.level - price))
                pool.level = (pool.level * pool.touches + price) / (pool.touches + 1)
                pool.touches += 1
                pool.last_pivot_index = center_global
                pool.prominence_atr = max(pool.prominence_atr, prominence)
            else:
                self.next_pool_id += 1
                self.swing_pools.append(
                    SwingPool(
                        pool_id=self.next_pool_id,
                        side=side,
                        level=price,
                        observed_index=self.bar_index,
                        last_pivot_index=center_global,
                        touches=1,
                        prominence_atr=prominence,
                    ),
                )

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False

        for pool in self.swing_pools:
            if pool.active and self.bar_index - pool.observed_index > self.POOL_MAX_AGE_BARS:
                pool.active = False

        taken: list[tuple[SwingPool, float, bool, int, float]] = []
        for pool in self.swing_pools:
            if not pool.active:
                continue
            age = self.bar_index - pool.observed_index
            if pool.side > 0:
                penetration = (float(row["high"]) - pool.level) / atr
                rejected = float(row["close"]) < pool.level
                trade_side = -1
                extreme = float(row["high"])
            else:
                penetration = (pool.level - float(row["low"])) / atr
                rejected = float(row["close"]) > pool.level
                trade_side = 1
                extreme = float(row["low"])
            if penetration < self.SWEEP_MIN_ATR:
                continue
            eligible = (
                age >= self.POOL_MIN_AGE_BARS
                and pool.prominence_atr >= self.POOL_MIN_PROMINENCE_ATR
                and penetration <= self.SWEEP_MAX_ATR
            )
            taken.append((pool, penetration, eligible and rejected, trade_side, extreme))

        if not taken:
            return False

        # Every pool crossed by the bar is consumed. This prevents one liquidity
        # event being counted repeatedly on later bars.
        for pool, *_ in taken:
            pool.active = False

        rejected = [item for item in taken if item[2]]
        if not rejected:
            self._event(
                "SWING_POOL_ACCEPTED_NO_REVERSAL",
                "SWING_POOL_AUCTION",
                row,
                {"consumed_pool_ids": [item[0].pool_id for item in taken]},
            )
            return True

        if self._efficiency(120) > self.MAX_AUCTION_EFFICIENCY_120:
            self._event(
                "DIRECTIONAL_AUCTION_REJECTION_SKIPPED",
                "SWING_POOL_AUCTION",
                row,
                {"consumed_pool_ids": [item[0].pool_id for item in rejected]},
            )
            return True

        # Prefer the pool with more independent touches, then greater prominence
        # and age. These are pre-sweep properties, not outcome-selected scores.
        pool, penetration, _, side, extreme = max(
            rejected,
            key=lambda item: (
                item[0].touches,
                item[0].prominence_atr,
                self.bar_index - item[0].observed_index,
            ),
        )
        rows = list(self.bars)
        pre = rows[-(self.config.pre_sweep_structure_bars + 1) : -1]
        structure = (
            max(float(item["high"]) for item in pre)
            if side > 0
            else min(float(item["low"]) for item in pre)
        )
        opposite = self._nearest_opposite_pool(side, float(row["close"]))
        self.pending = PendingSetup(
            scenario="CONFIRMED_SWING_POOL_FAILED_AUCTION",
            side=side,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.confirmation_bars,
            extreme=extreme,
            structure=structure,
            atr=atr,
            target_reference=opposite,
            details={
                "pool_id": pool.pool_id,
                "pool_side": pool.side,
                "pool_level": pool.level,
                "pool_touches": pool.touches,
                "pool_age_bars": self.bar_index - pool.observed_index,
                "pool_prominence_atr": pool.prominence_atr,
                "penetration_atr": penetration,
                "efficiency_120": self._efficiency(120),
                "opposite_pool": opposite,
            },
        )
        self._event("SWEEP_DETECTED", self.pending.scenario, row, self.pending.details)
        return True

    def _nearest_opposite_pool(self, side: int, current: float) -> float | None:
        if side > 0:
            candidates = [
                pool.level
                for pool in self.swing_pools
                if pool.active and pool.side > 0 and pool.level > current
            ]
            return min(candidates) if candidates else None
        candidates = [
            pool.level
            for pool in self.swing_pools
            if pool.active and pool.side < 0 and pool.level < current
        ]
        return max(candidates) if candidates else None

    def _detect_trend_sweep(self, row: dict[str, float | int]) -> bool:
        return False

    def _try_confirm_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None or setup.scenario != "CONFIRMED_SWING_POOL_FAILED_AUCTION":
            return super()._try_confirm_pending(row)
        if self.bar_index <= setup.created_index:
            return False

        side = setup.side
        broken = (
            float(row["close"]) > setup.structure
            if side > 0
            else float(row["close"]) < setup.structure
        )
        if not broken:
            return False

        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            self.pending = None
            return False
        body = side * (float(row["close"]) - float(row["open"])) / atr
        volume_burst = self._volume_burst()
        close_location = self._close_location(row, side)
        details = {
            **setup.details,
            "confirmation_body_atr": body,
            "confirmation_volume_burst": volume_burst,
            "confirmation_close_location": close_location,
            "structure": setup.structure,
        }
        passed = (
            body >= self.CONFIRMATION_BODY_ATR
            and volume_burst >= self.CONFIRMATION_VOLUME_BURST
            and close_location >= self.CONFIRMATION_CLOSE_LOCATION
        )
        if not passed:
            self._event("WEAK_FIRST_BREAK", setup.scenario, row, details)
            self.pending = None
            return False

        last_entry = self.last_entry_by_scenario.get(setup.scenario, -10**12)
        if self.bar_index - last_entry < self.config.cooldown_bars:
            self.pending = None
            return False

        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            self.TARGET_NET_R,
            details,
        )
        self.pending = None
        return submitted


__all__ = [
    "LiquidityTransitionConfig",
    "SwingPool",
    "SwingPoolFailedAuctionStrategy",
]
