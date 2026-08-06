"""Select the causal liquidity-target ladder without altering execution."""
from __future__ import annotations

import strategy_flow as _strategy_flow
from model_flow_target_ladder import TargetLadderAggressorFlowRouter

_strategy_flow.CausalAggressorFlowRouter = TargetLadderAggressorFlowRouter

from backtest_flow import run_week  # noqa: E402

__all__ = ["run_week"]
