#!/usr/bin/env python3
"""Candidate 14 v7 combined Nautilus portfolio runner.

The v6 exclusive failed-auction origin remains installed. Candidate 14 v7 adds
one execution-state correction for accepted auctions: once a causal defended
pullback and later reacceleration have completed, only that reacceleration leg
may own entry. A second resting order at the already-defended pivot is not
allowed. NautilusTrader remains the sole order, fill, fee, margin, position and
NAV engine.
"""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from aac_entry_ownership import install as _install_aac_entry_ownership
from auction_origin_ownership import install as _install_origin_ownership
from portfolio_materializer import materialize_combined_portfolio_source
from runner_materializer import materialize_runner_source
from semantic_market_leadership import SemanticMarketLeadershipGate
from semantic_logic import install as _install_semantic_logic

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()
_install_origin_ownership()
_install_aac_entry_ownership()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = _BASE.read_text(encoding="utf-8")
_SOURCE = materialize_runner_source(_SOURCE)
_SOURCE = materialize_combined_portfolio_source(_SOURCE)
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
