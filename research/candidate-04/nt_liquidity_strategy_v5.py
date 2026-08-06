#!/usr/bin/env python3
"""Candidate-04 v5: controlled session-auction variants for NautilusTrader.

This module does not calculate fills, positions, PnL or NAV. It only changes the
causal interpretation of an already-confirmed prior-session liquidity sweep.
All orders and accounting remain inside NautilusTrader.

Three hypotheses are deliberately compared on the same first random BTC week:

* SESSION_REVERSAL_08: every confirmed rejection is traded as a failed auction
  with a modest 0.8R post-cost objective.
* SESSION_REVERSAL_12: the same causal event with a 1.2R post-cost objective.
* HYBRID_AUCTION_ROUTER: shallow penetration is treated as a liquidity grab and
  traded in the rejection direction; deep, high-participation penetration is a
  probe which must subsequently fail before continuation is allowed. Ambiguous
  middle states are skipped rather than forced into either interpretation.
"""
from __future__ import annotations

from typing import Any

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy_v3 import LiquidityTransitionStrategyV3
from nt_liquidity_strategy_v4 import LiquidityTransitionStrategyV4


SESSION_SCENARIO = "SESSION_RANGE_FAILED_AUCTION"


def classify_session_sweep(
    penetration_atr: float,
    confirmation_volume_burst: float,
) -> str:
    """Classify a confirmed sweep by economically distinct auction states.

    A shallow excursion is consistent with stop collection followed by rejection.
    A deep excursion with unusually strong participation can represent genuine
    price discovery; it is not traded until the rejection itself fails. The gap
    between the two regimes is intentionally abstained from.
    """

    if penetration_atr <= 0.45:
        return "SHALLOW_REJECTION"
    if penetration_atr >= 0.75 and confirmation_volume_burst >= 3.0:
        return "DEEP_PRICE_DISCOVERY_PROBE"
    return "AMBIGUOUS_SKIP"


def without_target_reference(setup: PendingSetup, scenario: str) -> PendingSetup:
    """Clone a setup so the objective is expressed in cost-aware R, not hindsight."""

    return PendingSetup(
        scenario=scenario,
        side=setup.side,
        created_index=setup.created_index,
        expires_index=setup.expires_index,
        extreme=setup.extreme,
        structure=setup.structure,
        atr=setup.atr,
        target_reference=None,
        details=dict(setup.details),
    )


class _SessionReversalBase(LiquidityTransitionStrategy):
    SESSION_TARGET_NET_R = 1.0
    SESSION_NAME = "SESSION_REJECTION_REVERSAL"

    def _submit_bracket(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        target_net_r: float,
        details: dict[str, Any],
    ) -> bool:
        if setup.scenario != SESSION_SCENARIO:
            return LiquidityTransitionStrategy._submit_bracket(
                self,
                setup,
                row,
                target_net_r,
                details,
            )

        routed = without_target_reference(setup, self.SESSION_NAME)
        return LiquidityTransitionStrategy._submit_bracket(
            self,
            routed,
            row,
            self.SESSION_TARGET_NET_R,
            {
                **details,
                "auction_interpretation": "FAILED_AUCTION_REVERSAL",
                "session_target_net_r": self.SESSION_TARGET_NET_R,
            },
        )


class SessionReversal08Strategy(_SessionReversalBase):
    SESSION_TARGET_NET_R = 0.80
    SESSION_NAME = "SESSION_REJECTION_REVERSAL_08"


class SessionReversal12Strategy(_SessionReversalBase):
    SESSION_TARGET_NET_R = 1.20
    SESSION_NAME = "SESSION_REJECTION_REVERSAL_12"


class HybridAuctionRouterStrategy(LiquidityTransitionStrategyV4):
    """Route shallow rejection and deep acceptance without mixing the states."""

    SHALLOW_TARGET_NET_R = 0.80

    def _submit_bracket(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        target_net_r: float,
        details: dict[str, Any],
    ) -> bool:
        if setup.scenario != SESSION_SCENARIO:
            return LiquidityTransitionStrategy._submit_bracket(
                self,
                setup,
                row,
                target_net_r,
                details,
            )

        state = classify_session_sweep(
            float(setup.details.get("penetration_atr", 0.0)),
            float(details.get("confirmation_volume_burst", 0.0)),
        )
        routed_details = {**details, "auction_router_state": state}

        if state == "SHALLOW_REJECTION":
            routed = without_target_reference(
                setup,
                "SHALLOW_SESSION_LIQUIDITY_GRAB_REVERSAL",
            )
            return LiquidityTransitionStrategy._submit_bracket(
                self,
                routed,
                row,
                self.SHALLOW_TARGET_NET_R,
                {
                    **routed_details,
                    "session_target_net_r": self.SHALLOW_TARGET_NET_R,
                },
            )

        if state == "DEEP_PRICE_DISCOVERY_PROBE":
            # Arm the competing-risk probe from v3. If the rejection later fails,
            # v4 enters only on a favorable causal retest through Nautilus orders.
            return LiquidityTransitionStrategyV3._submit_bracket(
                self,
                setup,
                row,
                target_net_r,
                routed_details,
            )

        self._event(
            "AMBIGUOUS_AUCTION_SKIPPED",
            "SESSION_AUCTION_ROUTER",
            row,
            routed_details,
        )
        return False


__all__ = [
    "HybridAuctionRouterStrategy",
    "LiquidityTransitionConfig",
    "SessionReversal08Strategy",
    "SessionReversal12Strategy",
    "classify_session_sweep",
]
