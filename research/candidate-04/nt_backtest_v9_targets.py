#!/usr/bin/env python3
"""Control only the V9 causal target structure inside NautilusTrader.

The V9 signal sequence, invalidation, risk budget and every market event remain
unchanged. This runner patches only the target submitted to NautilusTrader:

* measured: pre-event dealing-range measured move (frozen V9 behavior),
* fixed10: cost-aware +1.0R,
* fixed12: cost-aware +1.2R,
* fixed16: cost-aware +1.6R.

No fill, position, PnL, fee or NAV is calculated here.
"""
from __future__ import annotations

import os
from typing import Any

import nt_auction_failure_strategy as failure
import nt_backtest as base


MODE = os.environ.get("C04_NT_TARGET_MODE", "measured")
TARGETS = {
    "fixed10": 1.0,
    "fixed12": 1.2,
    "fixed16": 1.6,
}

_original_submit_bracket = failure.LiquidityTransitionStrategy._submit_bracket


def _controlled_submit_bracket(
    self: Any,
    setup: failure.PendingSetup,
    row: dict[str, float | int],
    target_net_r: float,
    details: dict[str, Any],
) -> bool:
    if not isinstance(self, failure.AuctionExcessFailureContinuationStrategy):
        return _original_submit_bracket(self, setup, row, target_net_r, details)
    if MODE == "measured":
        return _original_submit_bracket(self, setup, row, target_net_r, details)
    try:
        controlled_target = TARGETS[MODE]
    except KeyError as exc:
        raise RuntimeError(
            f"unknown C04_NT_TARGET_MODE={MODE!r}; expected measured or {sorted(TARGETS)}",
        ) from exc

    fixed_setup = failure.PendingSetup(
        scenario=setup.scenario,
        side=setup.side,
        created_index=setup.created_index,
        expires_index=setup.expires_index,
        extreme=setup.extreme,
        structure=setup.structure,
        atr=setup.atr,
        target_reference=None,
        details=dict(setup.details),
    )
    return _original_submit_bracket(
        self,
        fixed_setup,
        row,
        controlled_target,
        {
            **details,
            "target_control_mode": MODE,
            "fixed_target_net_r": controlled_target,
            "original_measured_target": setup.target_reference,
        },
    )


failure.LiquidityTransitionStrategy._submit_bracket = _controlled_submit_bracket

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
