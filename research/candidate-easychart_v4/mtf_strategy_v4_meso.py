"""Nautilus strategy shell for 15m->1m and 5m->1m EasyChart plans."""
from __future__ import annotations

from mtf_strategy_v4_scale_execution import (
    EasyChartMTFConfig,
    ScaleExecutionStrategy,
)


class MicroMesoEasyChartStrategy(ScaleExecutionStrategy):
    """Execute source-identical MICRO and MESO plans in one global account."""

    EXECUTABLE_SCALES = frozenset({"MICRO", "MESO"})
    ONE_TRADE_PER_PARENT_CONTEXT = False


__all__ = ["EasyChartMTFConfig", "MicroMesoEasyChartStrategy"]
