#!/usr/bin/env python3
"""Candidate 13 direct Nautilus runner with fail-closed order materialization.

The materialized portfolio runner remains in ``run_leadership_scdam_base.py``.
Before compiling it, Candidate 13 installs the mutually exclusive FAR/AAC state
semantics, scenario-specific price plans, and the synchronized post-leadership
execution amendment.  Exact source boundaries are then expanded so explicit
MARKET parents remain inside NautilusTrader.
"""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from runner_materializer import materialize_runner_source
from semantic_market_leadership import SemanticMarketLeadershipGate
from semantic_logic import install as _install_semantic_logic
from semantic_post_gate import amend_after_leadership

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = materialize_runner_source(_BASE.read_text(encoding="utf-8"))
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
