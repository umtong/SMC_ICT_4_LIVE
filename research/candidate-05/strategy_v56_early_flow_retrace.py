#!/usr/bin/env python3
"""Candidate 05 v56: reject only mature-flow first retraces from v46.

The positive confirmed-second-touch and sponsored-CHoCH paths remain unchanged.
Only a first-retrace bracket is vetoed when completed 15-second flow no longer
leads the broad three-minute auction or broad flow is already at a two-to-one
imbalance. All targets, stops, costs, sizing, orders and NautilusTrader account
handling are inherited unchanged from v46.
"""
from __future__ import annotations

from typing import Any

from flow_maturity_logic import early_reversal_transfer
from strategy import LiquidityResponseConfig
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


class EarlyFlowFirstRetraceStrategy(NoPostRetraceBreakawayStrategy):
    """Apply one causal phase gate to the legacy first-retrace path only."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "first_retrace_phase_observations": 0,
                "first_retrace_early_flow_pass": 0,
                "first_retrace_mature_flow_rejections": 0,
                "first_retrace_rejected_branch_counts": {},
            },
        )

    @staticmethod
    def _is_first_retrace_branch(branch: str) -> bool:
        value = str(branch).upper()
        return (
            "RETRACE" in value
            and "SECOND" not in value
            and "ACCEPTANCE" not in value
            and "SPOT_LED" not in value
        )

    def _submit_price_capped_bracket(self, *args: Any, **kwargs: Any) -> bool:
        branch = str(kwargs.get("branch", ""))
        armed = kwargs.get("armed")
        if armed is not None and self._is_first_retrace_branch(branch):
            self.diagnostics["first_retrace_phase_observations"] += 1
            side = int(armed.setup.side)
            allowed = early_reversal_transfer(
                side=side,
                flow_15s=self._feature("flow_15s"),
                flow_60s=self._feature("flow_60s"),
                flow_3m=self._feature("flow_3m"),
            )
            if not allowed:
                self.diagnostics["first_retrace_mature_flow_rejections"] += 1
                counts = self.diagnostics["first_retrace_rejected_branch_counts"]
                counts[branch] = int(counts.get(branch, 0)) + 1
                row = kwargs.get("row")
                if row is not None and self.armed_entry_path is not None:
                    self._expire_armed_entry(
                        row,
                        "FIRST_RETRACE_REJECTED_AFTER_BROAD_FLOW_MATURED",
                    )
                return False
            self.diagnostics["first_retrace_early_flow_pass"] += 1
        return super()._submit_price_capped_bracket(*args, **kwargs)


LiquidityResponseStrategy = EarlyFlowFirstRetraceStrategy

__all__ = [
    "EarlyFlowFirstRetraceStrategy",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
]
