#!/usr/bin/env python3
"""Candidate 13 V15 Nautilus runner: structural FAR retrace execution."""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from runner_materializer_v4 import materialize_runner_source
from semantic_logic_v15 import install as _install_v15_execution
from semantic_market_leadership_v4 import SemanticMarketLeadershipGate
from semantic_post_gate_v15 import amend_after_leadership

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_v15_execution()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = materialize_runner_source(_BASE.read_text(encoding="utf-8"))
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
