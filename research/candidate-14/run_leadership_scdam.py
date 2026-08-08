#!/usr/bin/env python3
"""Candidate 14 v10 combined Nautilus portfolio runner.

V10 preserves v9's completion-before-failure state machine and changes one
causal measurement boundary: a confirmed accepted-auction-failure reversal is
measured across markets from its failure observation to its later initiative,
not from the original opposite-direction source sweep. NautilusTrader remains
the sole order, fill, fee, margin, position and NAV engine.
"""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from auction_origin_ownership import install as _install_origin_ownership
from confirmed_acceptance_failure_v9 import install as _install_v9_resolution
from failure_leg_leadership_materializer import materialize_failure_leg_leadership_source
from portfolio_materializer import materialize_combined_portfolio_source
from runner_materializer import materialize_runner_source
from semantic_market_leadership import SemanticMarketLeadershipGate
from semantic_logic import install as _install_semantic_logic

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()
_install_origin_ownership()
_install_v9_resolution()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = _BASE.read_text(encoding="utf-8")
_SOURCE = materialize_runner_source(_SOURCE)
_SOURCE = materialize_combined_portfolio_source(_SOURCE)
_SOURCE = materialize_failure_leg_leadership_source(_SOURCE)
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
