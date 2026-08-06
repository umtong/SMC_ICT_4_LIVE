"""Select diagnostic flow routing without changing replay or execution."""
from __future__ import annotations

import strategy_flow as _strategy_flow
from model_flow_diagnostic import DiagnosticAggressorFlowRouter

_strategy_flow.CausalAggressorFlowRouter = DiagnosticAggressorFlowRouter

from backtest_flow import run_week  # noqa: E402

__all__ = ["run_week"]
