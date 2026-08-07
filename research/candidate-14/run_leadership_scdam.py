#!/usr/bin/env python3
"""Candidate 14 direct Nautilus runner.

The inherited portfolio runner, market data adapter, global allocator and
Nautilus execution/accounting remain unchanged.  Before compiling that runner,
Candidate 14 installs event-local cross-market transfer semantics and the
scenario-specific market/passive execution choice.
"""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from runner_materializer import materialize_runner_source
from semantic_market_leadership import EventPriceDiscoveryTransferGate
from semantic_logic import install as _install_semantic_logic

_market_leadership.MarketLeadershipGate = EventPriceDiscoveryTransferGate
_install_semantic_logic()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = materialize_runner_source(_BASE.read_text(encoding="utf-8"))
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
