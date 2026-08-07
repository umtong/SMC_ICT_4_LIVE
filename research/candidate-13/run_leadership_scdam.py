#!/usr/bin/env python3
"""Candidate 13 v2 direct Nautilus runner.

The materialized Candidate 11 runner remains unchanged in
``run_leadership_scdam_base``.  Before importing it, this module replaces only
the binary cross-market approval class with Candidate 13's mutually exclusive
FAR/AAC semantic gate.  Detection, orders, costs, sizing and Nautilus accounting
are untouched.
"""
from __future__ import annotations

import market_leadership as _market_leadership
from semantic_market_leadership import SemanticMarketLeadershipGate

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate

from run_leadership_scdam_base import *  # noqa: F401,F403,E402
