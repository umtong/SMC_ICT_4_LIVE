#!/usr/bin/env python3
"""Materialized NautilusTrader runner for the first-delivery/runner ablation."""
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
from first_delivery_materializer import materialize_first_delivery_source  # noqa: E402

_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()
# Import only after the semantic FAR plan has replaced the base costed plan, so
# the first-delivery wrapper preserves Candidate 14 entry/stop semantics.
from first_delivery_logic import install as _install_first_delivery_logic  # noqa: E402

_install_first_delivery_logic()

_BASE = SOURCE_ROOT / "run_leadership_scdam_base.py"
_SOURCE = _BASE.read_text(encoding="utf-8")
_SOURCE = materialize_runner_source(_SOURCE)
_SOURCE = materialize_continuous_far_source(_SOURCE)
_SOURCE = materialize_first_delivery_source(_SOURCE)
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
