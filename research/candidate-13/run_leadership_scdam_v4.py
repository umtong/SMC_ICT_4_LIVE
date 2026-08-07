#!/usr/bin/env python3
"""Candidate 13 v4 Nautilus runner.

The inherited strategy and v3 scenario execution are unchanged.  V4 installs
the early-repricing AAC refinement and strict historical Binance timestamp
normalization before compiling the materialized NautilusTrader portfolio
runner.
"""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from runner_materializer_v4 import materialize_runner_source
from semantic_market_leadership_v4 import SemanticMarketLeadershipGate
from semantic_logic import install as _install_semantic_logic
from semantic_post_gate import amend_after_leadership

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = materialize_runner_source(_BASE.read_text(encoding="utf-8"))
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
