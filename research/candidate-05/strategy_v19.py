#!/usr/bin/env python3
"""Candidate 05 v19: single-variable ablation of blind CHoCH retrace entry.

The parent strategy arms a causal reversal after a liquidity sweep, tail-flow
inflection, directional depth and CHoCH.  When the next completed minute is not
a true breakaway, v9 immediately rests a limit at the CHoCH close without
requiring the eventual retest to show renewed sponsorship.

This experiment removes only that blind resting-limit branch.  Sponsored early
CHoCH participation, balance acceptance, target handoff, risk sizing, costs,
execution and all detectors remain unchanged.  The result is diagnostic rather
than a proposed production rule: if the removed branch is structurally useful,
a later version must replace it with an explicit retest-response state instead
of deleting the opportunity class.
"""
from __future__ import annotations

from typing import Any

from strategy_v18 import ExecutionConfirmedCancelStrategy
from strategy_v9 import ArmedEntryPath


class BlindRetraceAblationStrategy(ExecutionConfirmedCancelStrategy):
    """Route non-breakaway CHoCH paths to observation instead of a blind limit."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.diagnostics["ablation_blind_choch_retrace_removed"] = 0

    def _submit_retrace_limit(
        self,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
    ) -> bool:
        self.diagnostics["ablation_blind_choch_retrace_removed"] += 1
        self._expire_armed_entry(
            row,
            "ABLATION_BLIND_CHOCH_RETRACE_REMOVED",
        )
        return False


__all__ = ["BlindRetraceAblationStrategy"]
