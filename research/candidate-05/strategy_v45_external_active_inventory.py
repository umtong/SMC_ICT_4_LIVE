#!/usr/bin/env python3
"""Candidate 05 v45: actively confirmed external inventory traps only.

v44 removed lower-timeframe raids which lacked the quarter-hour repricing state
they were designed to continue. The remaining 30-day losses came from the 5m
external branch because it still used the old baseline rejection classifier
without the inventory-transfer conditions already declared by the hybrid model.

v45 changes only that omission. A new external setup must show a one-third ATR
raid, material final-flow reversal, 3:2 reversal-side resting depth and sweep
VWAP recovery. Its later CHOCH must be actively sponsored rather than a passive
rotation. Internal context, target handoff, PBA, prices, stops, targets, costs,
3% NAV sizing and the NautilusTrader lifecycle remain unchanged.
"""
from __future__ import annotations

from external_inventory_wiring_logic import EXTERNAL_POOL_SOURCE
from external_inventory_wiring_logic import INTERNAL_HYBRID_STATE
from external_inventory_wiring_logic import external_setup_from_hybrid
from flow_inflection_logic import choch_flow_state
from inventory_repricing_logic import inventory_trap_confirmed
from strategy import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_v44_context_aligned_internal import ContextAlignedInternalStrategy


class ActiveExternalInventoryStrategy(ContextAlignedInternalStrategy):
    """Require strict sweep transfer and active CHOCH for external liquidity."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "external_inventory_quality_pass": 0,
                "external_inventory_quality_rejections": 0,
                "external_active_choch_pass": 0,
                "external_passive_choch_rejections": 0,
            },
        )

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        prior = None if self.pending is None else self.pending.scenario_id
        super()._detect_sweep(row, previous_close)
        setup = self.pending
        if setup is None or setup.scenario_id == prior:
            return
        if not external_setup_from_hybrid(setup.details):
            return

        # Persist the branch identity so the later CHOCH decision receives the
        # same causal sweep contract rather than inferring it from a price shape.
        setup.details["hybrid_state"] = "EXTERNAL_REJECTION_BASELINE"
        passed = inventory_trap_confirmed(
            side=setup.side,
            penetration_atr=float(
                setup.details.get("penetration_atr", float("nan")),
            ),
            flow_15s=float(setup.details.get("flow_15s", float("nan"))),
            flow_60s=float(setup.details.get("flow_60s", float("nan"))),
            depth_imbalance=float(
                setup.details.get("depth_imbalance_1", float("nan")),
            ),
            close=float(row["close"]),
            trade_vwap=self._feature("trade_vwap_60s"),
            external_or_clustered=True,
        )
        if passed:
            setup.details["strict_external_inventory_confirmed"] = True
            self.diagnostics["external_inventory_quality_pass"] += 1
            return

        self.diagnostics["external_inventory_quality_rejections"] += 1
        self._expire_pending(
            row,
            "EXTERNAL_RAID_LACKED_STRICT_INVENTORY_TRANSFER",
        )

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        if bool(setup.details.get("strict_external_inventory_confirmed", False)):
            state = choch_flow_state(
                side=setup.side,
                flow_15s=self._feature("flow_15s"),
                flow_3m=self._feature("flow_3m"),
                depth_imbalance=self._feature("depth_imbalance_1"),
            )
            if state != "ACTIVE_CONFIRMATION":
                self.diagnostics["external_passive_choch_rejections"] += 1
                self._expire_pending(
                    row,
                    "EXTERNAL_INVENTORY_TRAP_REQUIRES_ACTIVE_CHOCH",
                )
                return False
            self.diagnostics["external_active_choch_pass"] += 1
        return super()._submit_entry(setup, row)


LiquidityResponseStrategy = ActiveExternalInventoryStrategy

__all__ = [
    "ActiveExternalInventoryStrategy",
    "EXTERNAL_POOL_SOURCE",
    "INTERNAL_HYBRID_STATE",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "external_setup_from_hybrid",
]
