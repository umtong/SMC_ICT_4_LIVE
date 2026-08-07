"""Candidate 05 v39 ablation: inventory traps with quarter context as veto."""
from __future__ import annotations

import math

from inventory_repricing_logic import QH_CONTEXT_MAX_AGE_BARS
from inventory_repricing_logic import QH_CONTEXT_MIN_AGE_BARS
from inventory_repricing_logic import inventory_trap_confirmed
from strategy_base import LiquidityResponseConfig
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v39_inventory_repricing import InventoryRepricingStrategy


class InventoryContextVetoStrategy(InventoryRepricingStrategy):
    """Remove only the requirement that an internal trap have a QH context.

    An accepted, mature quarter-hour repricing state still vetoes a proposed
    reversal in the opposite direction.  When no such state exists, the local
    inventory trap is evaluated on its own completed flow, depth, VWAP reclaim,
    later CHoCH, live target and unchanged v26 execution geometry.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "quarter_context_absence_neutral": 0,
                "quarter_context_aligned_support": 0,
                "quarter_context_opposition_veto": 0,
            },
        )

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        previous_pending = self.pending
        ScenarioValidEntryStrategy._detect_sweep(self, row, previous_close)
        setup = self.pending
        if setup is None or setup is previous_pending:
            return

        source = str(setup.details.get("pool_source", ""))
        strength = int(setup.details.get("pool_strength", 1))
        external_or_clustered = source == "CONFIRMED_5M_SWING" or strength >= 3
        quality = inventory_trap_confirmed(
            side=setup.side,
            penetration_atr=float(setup.details.get("penetration_atr", math.nan)),
            flow_15s=float(setup.details.get("flow_15s", math.nan)),
            flow_60s=float(setup.details.get("flow_60s", math.nan)),
            depth_imbalance=float(setup.details.get("depth_imbalance_1", math.nan)),
            close=float(row["close"]),
            trade_vwap=self._feature("trade_vwap_60s"),
            external_or_clustered=external_or_clustered,
        )
        if not quality:
            self.diagnostics["inventory_quality_rejections"] += 1
            self._expire_pending(row, "INVENTORY_TRAP_FLOW_DEPTH_VWAP_NOT_CONFIRMED")
            return

        if external_or_clustered:
            setup.details.update(
                {
                    "v39_state": "EXTERNAL_OR_CLUSTERED_INVENTORY_TRAP",
                    "v39_trade_vwap_60s": self._feature("trade_vwap_60s"),
                },
            )
            self.diagnostics["external_inventory_traps"] += 1
            return

        context = self.quarter_context
        active_context = False
        context_age = -1
        if context is not None:
            context_age = self.bar_index - context.created_index
            active_context = (
                context.accepted
                and QH_CONTEXT_MIN_AGE_BARS
                <= context_age
                <= QH_CONTEXT_MAX_AGE_BARS
            )
        if active_context and context is not None and context.direction != setup.side:
            self.diagnostics["quarter_context_opposition_veto"] += 1
            self.diagnostics["quarter_context_rejections"] += 1
            self._expire_pending(row, "INTERNAL_TRAP_OPPOSED_ACCEPTED_QUARTER_REPRICING")
            return

        if active_context:
            self.diagnostics["quarter_context_aligned_support"] += 1
            context_state = "ALIGNED_ACCEPTED_QUARTER_REPRICING"
        else:
            self.diagnostics["quarter_context_absence_neutral"] += 1
            context_state = "NO_ACTIVE_QUARTER_REPRICING"
        setup.details.update(
            {
                "v39_state": "INTERNAL_INVENTORY_TRAP_CONTEXT_VETO_ABLATION",
                "v39_trade_vwap_60s": self._feature("trade_vwap_60s"),
                "quarter_context_state": context_state,
                "quarter_context_direction": (
                    context.direction if context is not None else None
                ),
                "quarter_context_age_bars": context_age,
                "quarter_context_accepted": (
                    context.accepted if context is not None else False
                ),
            },
        )
        self.diagnostics["quarter_internal_inventory_traps"] += 1


LiquidityResponseStrategy = InventoryContextVetoStrategy

__all__ = [
    "InventoryContextVetoStrategy",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
]
