#!/usr/bin/env python3
"""Candidate 05 v46: a no-retrace breakaway must contain no prior retest.

The corrected v45 BTC 30-day run produced five trades, four wins and one loss.
The only loss was labelled ``TAIL_FLOW_LIQUIDITY_BREAKAWAY`` even though price
had already touched the frozen CHoCH reference twice without confirming the
retest response. That contradicts the breakaway scenario's own state premise.

v46 repairs only this transition. Once any CHoCH retest touch has occurred, a
later one-ATR extension cannot be reclassified as a no-retrace breakaway. The
auction must complete the existing retest-response path or expire. Sweep and
CHOCH logic, target, stop, fees, slippage, 3% current-NAV sizing, order lifecycle
and NautilusTrader execution remain unchanged.
"""
from __future__ import annotations

from breakaway_state_logic import no_retrace_breakaway_allowed
from flow_inflection_logic import breakaway_follow_through
from retrace_logic import pending_limit_invalidated
from retest_response_logic import retest_touched
from strategy import LiquidityResponseConfig
from strategy_v45_external_active_inventory import ActiveExternalInventoryStrategy


class NoPostRetraceBreakawayStrategy(ActiveExternalInventoryStrategy):
    """Preserve breakaways only while the path has never touched CHoCH."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics["post_retrace_breakaway_invalidations"] = 0

    def _resolve_entry_path(self, row: dict[str, float | int]) -> None:
        armed = self.armed_entry_path
        if armed is None or self.bar_index <= armed.created_index:
            return

        side = armed.setup.side
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        # Structural invalidation and a current retest touch remain entirely
        # authoritative in the inherited v20+ response state machine.
        if pending_limit_invalidated(
            side=side,
            stop=armed.stop,
            high=high,
            low=low,
        ) or retest_touched(
            side=side,
            reference_price=armed.choch_close,
            high=high,
            low=low,
        ):
            super()._resolve_entry_path(row)
            return

        breakaway_candidate = breakaway_follow_through(
            side=side,
            choch_close=armed.choch_close,
            current_close=close,
            atr=armed.atr,
            sweep_depth_imbalance=float(
                armed.setup.details.get("depth_imbalance_1", float("nan")),
            ),
            current_depth_imbalance=self._feature("depth_imbalance_1"),
            current_flow_3m=self._feature("flow_3m"),
        )
        touch_count = int(armed.details.get("retest_touch_count", 0))
        if (
            breakaway_candidate
            and not no_retrace_breakaway_allowed(
                retest_touch_count=touch_count,
                breakaway_candidate=breakaway_candidate,
            )
        ):
            self.diagnostics["post_retrace_breakaway_invalidations"] += 1
            self._expire_armed_entry(
                row,
                "NO_RETRACE_BREAKAWAY_INVALID_AFTER_CHOCH_RETEST",
            )
            return

        super()._resolve_entry_path(row)


LiquidityResponseStrategy = NoPostRetraceBreakawayStrategy

__all__ = [
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "NoPostRetraceBreakawayStrategy",
]
