#!/usr/bin/env python3
"""Candidate-04 v7b: route CHoCH-confirmed swing setups through causal targets."""
from __future__ import annotations

import math

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_swing_pool_strategy import SwingPoolFailedAuctionStrategy
from nt_swing_pool_strategy_v4 import _CausalTargetSwingPoolStrategy


class _WorkingCausalSwingStrategy(_CausalTargetSwingPoolStrategy):
    """Override parent direct base-call so causal target dispatch is respected."""

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
            return True
        body = side * (float(row["close"]) - float(row["open"])) / atr
        flow_volume = self._volume_burst()
        close_location = self._close_location(row, side)
        details = {
            **setup.details,
            "confirmation_body_atr": body,
            "confirmation_volume_burst": flow_volume,
            "confirmation_close_location": close_location,
            "structure": setup.structure,
        }
        passed = (
            body >= self.CONFIRMATION_BODY_ATR
            and flow_volume >= self.CONFIRMATION_VOLUME_BURST
            and close_location >= self.CONFIRMATION_CLOSE_LOCATION
        )
        if not passed:
            self._event("WEAK_FIRST_BREAK", setup.scenario, row, details)
            self.pending = None
            return True

        last_entry = self.last_entry_by_scenario.get(setup.scenario, -10**12)
        if self.bar_index - last_entry < self.config.cooldown_bars:
            self.pending = None
            return True

        submitted = self._submit_bracket(
            setup,
            row,
            self.TARGET_NET_R,
            details,
        )
        self.pending = None
        return True or submitted


class SwingPoolWorkingTarget12Strategy(_WorkingCausalSwingStrategy):
    TARGET_NET_R = 1.20


class SwingPoolWorkingTarget16Strategy(_WorkingCausalSwingStrategy):
    TARGET_NET_R = 1.60


__all__ = [
    "LiquidityTransitionConfig",
    "SwingPoolWorkingTarget12Strategy",
    "SwingPoolWorkingTarget16Strategy",
]
