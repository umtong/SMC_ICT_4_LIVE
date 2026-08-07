"""Controlled v24 launcher with causal dense no-trade interval alignment."""
from __future__ import annotations

# Installation order is part of the execution contract. The fixed-point
# impact strategy must be selected before c10_v24_research imports
# c10_live_cost_ledger and c10_v24_strategy; otherwise their class bases freeze
# the earlier raw strategy and a zero-trade run can conceal the defect.
from v20_impact_control import install as install_impact_control

install_impact_control()

import c10_v24_research as _research
from c10_v24_dense_alignment import align_cross_market_rows_dense

# The implementation repair changes only the representation of natural
# no-trade intervals. All detector/scenario/execution variables remain in the
# original run_v24 module and its process-isolated full/ablation contract.
_research.align_cross_market_rows = align_cross_market_rows_dense

import run_v24 as _runner

_runner.run_cross_market_backtest = _research.run_cross_market_backtest
# run_v24 spawns the current script for isolated workers. Point that lookup to
# this controlled launcher so each child installs the same dense aligner and
# the same pre-import impact strategy.
_runner.__file__ = __file__


if __name__ == "__main__":
    raise SystemExit(_runner.main())
