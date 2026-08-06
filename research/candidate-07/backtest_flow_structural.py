"""Select structural sweep stops without altering Nautilus execution."""
from __future__ import annotations

import strategy_flow as _strategy_flow
from model_flow_structural_stop import StructuralStopTargetLadderRouter

_strategy_flow.CausalAggressorFlowRouter = StructuralStopTargetLadderRouter

import backtest_flow as _backtest_flow  # noqa: E402
from strategy_flow_structural import StructuralStopFlowStrategy  # noqa: E402

_backtest_flow.Candidate07FlowStrategy = StructuralStopFlowStrategy
run_week = _backtest_flow.run_week

__all__ = ["run_week"]
