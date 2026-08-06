#!/usr/bin/env python3
"""Control only V11 second-order refailure targets in NautilusTrader.

The low-impact continuation branch is frozen. For the nested acceptance-
refailure branch only, compare the causal fair-value target with cost-aware
fixed 0.8R, 1.0R and 1.2R exits. No signal, invalidation, size, fill, fee,
position or NAV is calculated outside NautilusTrader.
"""
from __future__ import annotations

import os
from typing import Any

import nt_backtest as base
import nt_dual_risk_auction_strategy as dual


MODE = os.environ.get("C04_REFAILURE_TARGET_MODE", "measured")
TARGETS = {
    "fixed08": 0.8,
    "fixed10": 1.0,
    "fixed12": 1.2,
}
_original_submit = dual.LiquidityTransitionStrategy._submit_bracket


def _controlled_submit(
    self: Any,
    setup: dual.PendingSetup,
    row: dict[str, float | int],
    target_net_r: float,
    details: dict[str, Any],
) -> bool:
    if (
        isinstance(self, dual.DualRiskAuctionStrategy)
        and setup.scenario == dual.REFAILURE_SCENARIO
        and MODE != "measured"
    ):
        try:
            controlled_target = TARGETS[MODE]
        except KeyError as exc:
            raise RuntimeError(
                f"unknown C04_REFAILURE_TARGET_MODE={MODE!r}; expected measured or {sorted(TARGETS)}",
            ) from exc
        fixed_setup = dual.PendingSetup(
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
        return _original_submit(
            self,
            fixed_setup,
            row,
            controlled_target,
            {
                **details,
                "refailure_target_control_mode": MODE,
                "fixed_refailure_target_net_r": controlled_target,
                "original_fair_value_target": setup.target_reference,
            },
        )
    return _original_submit(self, setup, row, target_net_r, details)


dual.LiquidityTransitionStrategy._submit_bracket = _controlled_submit

_original_importable_strategy_config = base.ImportableStrategyConfig


def _strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        kwargs["strategy_path"] = "nt_dual_risk_auction_strategy:DualRiskAuctionStrategy"
    return _original_importable_strategy_config(*args, **kwargs)


base.ImportableStrategyConfig = _strategy_config


if __name__ == "__main__":
    base.main()
