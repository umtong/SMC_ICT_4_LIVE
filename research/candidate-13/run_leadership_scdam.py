#!/usr/bin/env python3
"""Candidate 13 v5 direct Nautilus runner.

The materialized portfolio runner remains in ``run_leadership_scdam_base``.
Before importing it, Candidate 13 installs three narrow semantic corrections:

1. FAR and AAC are mutually exclusive cross-market states;
2. accepted continuation is entered at its defended pullback and invalidated at
   source-boundary reacceptance;
3. a confirmed FAR uses a Nautilus market parent only when the confirmation
   close still clears the existing after-cost structural-R floor.

Detection, liquidity targets, exact 3% NAV sizing, global arbitration, child
orders and Nautilus accounting remain unchanged.
"""
from __future__ import annotations

import market_leadership as _market_leadership
from semantic_execution import install as _install_execution_boundary
from semantic_market_leadership import SemanticMarketLeadershipGate
from semantic_logic import install as _install_semantic_logic

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_execution_boundary()
_install_semantic_logic()

from run_leadership_scdam_base import *  # noqa: F401,F403,E402
