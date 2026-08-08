#!/usr/bin/env python3
"""Candidate 14 v8 combined Nautilus portfolio runner.

Candidate 14 v8 preserves the v6 exclusive failed-auction origin correction and
replaces the incomplete accepted-auction continuation label with an explicit
failure-resolution state.  A deep boundary re-entry is only the failure
observation; a later completed opposite initiative must own the trade.
NautilusTrader remains the sole order, fill, fee, margin, position and NAV
engine.
"""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from acceptance_resolution_v8 import install as _install_acceptance_resolution
from auction_origin_ownership import install as _install_origin_ownership
from portfolio_materializer import materialize_combined_portfolio_source
from runner_materializer import materialize_runner_source
from semantic_market_leadership import SemanticMarketLeadershipGate
from semantic_logic import install as _install_semantic_logic

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()
_install_origin_ownership()
_install_acceptance_resolution()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = _BASE.read_text(encoding="utf-8")
_SOURCE = materialize_runner_source(_SOURCE)
_SOURCE = materialize_combined_portfolio_source(_SOURCE)
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
