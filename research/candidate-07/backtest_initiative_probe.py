"""Nautilus replay composition for persistent initiative shadow probes."""
from __future__ import annotations

import backtest as _base
from strategy_initiative_probe import Candidate07Strategy as _ProbeStrategy
from strategy_progress import ThreeRProgressProtectionMixin


class Candidate07Strategy(
    ThreeRProgressProtectionMixin,
    _ProbeStrategy,
):
    """Shadow-probe initiative routing with existing cost-floor protection."""


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["run_week"]
