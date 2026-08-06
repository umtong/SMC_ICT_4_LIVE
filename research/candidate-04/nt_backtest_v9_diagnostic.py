#!/usr/bin/env python3
"""Replay frozen V9 while recording only past-known regime diagnostics.

This module does not alter V9 trade selection, targets, stops or sizing. It
annotates NautilusTrader ENTRY_SUBMITTED events with state available at the
failure-confirmation bar so winning and losing market mechanisms can be
compared without outcome leakage.
"""
from __future__ import annotations

import math
from typing import Any

import nt_auction_failure_strategy as failure
import nt_backtest as base


HORIZONS = (15, 30, 60, 120, 240, 720)
_original_submit_bracket = failure.LiquidityTransitionStrategy._submit_bracket


def _past_regime_details(self: Any, side: int) -> dict[str, float]:
    rows = list(self.bars)
    atr = float(self._atr())
    result: dict[str, float] = {}
    if not rows or not math.isfinite(atr) or atr <= 0.0:
        return result
    close = float(rows[-1]["close"])
    for horizon in HORIZONS:
        if len(rows) < horizon + 1:
            continue
        previous = float(rows[-horizon - 1]["close"])
        selected = rows[-(horizon + 1) :]
        high = max(float(item["high"]) for item in selected)
        low = min(float(item["low"]) for item in selected)
        span = max(high - low, 1e-12)
        result[f"directional_displacement_{horizon}m_atr"] = (
            side * (close - previous) / atr
        )
        result[f"auction_efficiency_{horizon}m"] = float(self._efficiency(horizon))
        result[f"directional_range_location_{horizon}m"] = (
            (close - low) / span if side > 0 else (high - close) / span
        )
    return result


def _diagnostic_submit_bracket(
    self: Any,
    setup: failure.PendingSetup,
    row: dict[str, float | int],
    target_net_r: float,
    details: dict[str, Any],
) -> bool:
    if isinstance(self, failure.AuctionExcessFailureContinuationStrategy):
        rejection_volume = float(details.get("rejection_volume_burst", 0.0))
        failure_volume = float(details.get("failure_volume_burst", 0.0))
        diagnostics = _past_regime_details(self, int(setup.side))
        diagnostics["failure_to_rejection_volume_ratio"] = (
            failure_volume / rejection_volume if rejection_volume > 0.0 else math.inf
        )
        diagnostics["failure_delay_bars_state"] = float(
            details.get("failure_delay_bars", math.nan),
        )
        details = {**details, **diagnostics, "diagnostic_only": True}
    return _original_submit_bracket(self, setup, row, target_net_r, details)


failure.LiquidityTransitionStrategy._submit_bracket = _diagnostic_submit_bracket

_original_importable_strategy_config = base.ImportableStrategyConfig


def _strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        kwargs["strategy_path"] = (
            "nt_auction_failure_strategy:AuctionExcessFailureContinuationStrategy"
        )
    return _original_importable_strategy_config(*args, **kwargs)


base.ImportableStrategyConfig = _strategy_config


if __name__ == "__main__":
    base.main()
