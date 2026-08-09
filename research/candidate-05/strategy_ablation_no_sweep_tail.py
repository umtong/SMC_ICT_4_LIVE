#!/usr/bin/env python3
"""Diagnostic ablation: remove only sweep-tail flow inflection approval."""
from __future__ import annotations

from strategy_base import LiquidityResponseConfig
from strategy_v4 import LiquidityResponseDepthStrategy
from strategy_v12 import SoftwareLiquidityProtectionStrategy


class NoSweepTailAblationStrategy(SoftwareLiquidityProtectionStrategy):
    """Bypass only the final-15-second versus 60-second flow turn check.

    All liquidity detection, failed-auction classification, directional depth,
    CHoCH displacement, CHoCH flow maturity, observed entry-path split,
    structural stop, live-liquidity target, costs, 3% NAV sizing and Nautilus
    execution remain identical to v12. This class exists only to determine
    whether sweep-tail inflection is an alpha-preserving state observation or an
    opportunity-suppressing duplicate of later confirmation.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics["ablation_no_sweep_tail_paths"] = 0

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is not None and not bool(setup.details.get("sweep_tail_ablation_marked", False)):
            setup.details["sweep_tail_ablation_marked"] = True
            setup.details["ablation"] = "REMOVE_SWEEP_TAIL_FLOW_INFLECTION_ONLY"
            self.diagnostics["ablation_no_sweep_tail_paths"] += 1
        # Call the directional-depth predecessor directly, bypassing only
        # TailFlowLiquidityStrategy._process_pending. Dynamic dispatch still
        # resolves self._submit_entry to v9/v12, so CHoCH flow and every later
        # execution rule are unchanged.
        return LiquidityResponseDepthStrategy._process_pending(self, row)


__all__ = ["NoSweepTailAblationStrategy"]
