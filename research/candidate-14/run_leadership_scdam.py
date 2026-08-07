#!/usr/bin/env python3
"""Candidate 14 combined Nautilus portfolio runner.

Candidate 13's cross-market SCDAM core and Candidate 12 I7's BTC session auction
emit plans into one deterministic global arbitration path. Candidate 14 installs
only the preserved semantic gate and displacement-failure execution extension;
NautilusTrader remains the sole order, fill, fee, margin, position and NAV engine.
"""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from portfolio_materializer import materialize_combined_portfolio_source
from runner_materializer import materialize_runner_source
from semantic_market_leadership import SemanticMarketLeadershipGate
from semantic_logic import install as _install_semantic_logic

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = _BASE.read_text(encoding="utf-8")
_SOURCE = materialize_runner_source(_SOURCE)
_SOURCE = materialize_combined_portfolio_source(_SOURCE)
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
