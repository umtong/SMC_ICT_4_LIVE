#!/usr/bin/env python3
"""Candidate 13 v3 direct Nautilus runner.

The materialized Candidate 11 runner remains unchanged in
``run_leadership_scdam_base``.  Before importing it, Candidate 13 installs two
narrow semantic corrections:

1. mutually exclusive FAR/AAC cross-market approval;
2. AAC execution at the causal equilibrium between the first void and the
   defended pullback, with expiry measured in structure bars.

Detection, targets, fees, exact 3% sizing, global arbitration and Nautilus
accounting remain the frozen implementation.
"""
from __future__ import annotations

import market_leadership as _market_leadership
from semantic_market_leadership import SemanticMarketLeadershipGate
from semantic_logic import install as _install_semantic_logic

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()

from run_leadership_scdam_base import *  # noqa: F401,F403,E402
