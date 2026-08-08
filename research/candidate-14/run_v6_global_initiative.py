#!/usr/bin/env python3
"""Candidate 14 V6 market-owned initiative portfolio runner.

The inherited four-market SCDAM detector supplies completed external-liquidity
transfer events. V6 admits only event owners, persists one global initiative
state, and adds fresh five-minute MSS/FVG continuation plans. NautilusTrader
remains the only order, fill, fee, margin, position, and NAV engine.
"""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from runner_materializer import materialize_runner_source
from semantic_logic import install as _install_semantic_logic
from v6_market_leadership import OwnershipMarketLeadershipGate
from v6_portfolio_materializer import materialize_v6_portfolio_source

_market_leadership.MarketLeadershipGate = OwnershipMarketLeadershipGate
_install_semantic_logic()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = _BASE.read_text(encoding="utf-8")
_SOURCE = materialize_runner_source(_SOURCE)
_SOURCE = materialize_v6_portfolio_source(_SOURCE)
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
