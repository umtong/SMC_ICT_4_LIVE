"""Nautilus replay composition for the minimal internal-boundary MSS fix."""
from __future__ import annotations

import backtest as _base
from backtest_initiative_auction import Candidate07Strategy as _InitiativeStrategy
from model_internal_mss import InternalBoundaryMSSRouter


class Candidate07Strategy(_InitiativeStrategy):
    """Initiative candidate with a real pre-sweep internal-boundary MSS."""

    def __init__(self, config):
        super().__init__(config)
        self.router = InternalBoundaryMSSRouter(self.logic)


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["Candidate07Strategy", "run_week"]
