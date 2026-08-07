"""Controlled v24 launcher with causal dense no-trade interval alignment."""
from __future__ import annotations

import c10_v24_research as _research
from c10_v24_dense_alignment import align_cross_market_rows_dense

# The implementation repair changes only the representation of natural
# no-trade intervals. All detector/scenario/execution variables remain in the
# original run_v24 module and its process-isolated full/ablation contract.
_research.align_cross_market_rows = align_cross_market_rows_dense

import run_v24 as _runner

_runner.run_cross_market_backtest = _research.run_cross_market_backtest
# run_v24 spawns the current script for isolated workers. Point that lookup to
# this controlled launcher so each child installs the same dense aligner.
_runner.__file__ = __file__


if __name__ == "__main__":
    raise SystemExit(_runner.main())
