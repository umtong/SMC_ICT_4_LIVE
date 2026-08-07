#!/usr/bin/env python3
"""Candidate 13 direct Nautilus runner with fail-closed order materialization.

The materialized portfolio runner remains in ``run_leadership_scdam_base.py``.
Before compiling it, Candidate 13 installs the mutually exclusive FAR/AAC state
semantics and the scenario-specific price plan.  One exact source boundary is
then expanded so a TradePlan explicitly marked MARKET builds a Nautilus MARKET
bracket; all other parents retain the inherited passive GTD limit path.
"""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from runner_materializer import materialize_runner_source
from semantic_market_leadership import SemanticMarketLeadershipGate
from semantic_logic import install as _install_semantic_logic

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = materialize_runner_source(_BASE.read_text(encoding="utf-8"))
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
