#!/usr/bin/env python3
"""Materialized NautilusTrader runner for the structural risk-transfer ablation."""
from __future__ import annotations

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
CORE_ROOT = HERE.parent / "core_far_continuous_v1"
SOURCE_ROOT = HERE.parent / "session_portfolio_v1"
for path in (HERE, CORE_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import market_leadership as _market_leadership  # noqa: E402
from continuous_far_materializer import materialize_continuous_far_source  # noqa: E402
from runner_materializer import materialize_runner_source  # noqa: E402
from semantic_market_leadership import SemanticMarketLeadershipGate  # noqa: E402
from semantic_logic import install as _install_semantic_logic  # noqa: E402
from structure_transfer_materializer import (  # noqa: E402
    materialize_structure_transfer_source,
)

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()

_BASE = SOURCE_ROOT / "run_leadership_scdam_base.py"
_SOURCE = _BASE.read_text(encoding="utf-8")
_SOURCE = materialize_runner_source(_SOURCE)
_SOURCE = materialize_continuous_far_source(_SOURCE)
_SOURCE = materialize_structure_transfer_source(_SOURCE)
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
