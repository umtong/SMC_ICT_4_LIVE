#!/usr/bin/env python3
"""Candidate 15 V6 residual-laggard Nautilus portfolio runner."""
from __future__ import annotations

from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
CANDIDATE14 = HERE.parent / "candidate-14"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(CANDIDATE14) not in sys.path:
    sys.path.insert(1, str(CANDIDATE14))

import market_leadership as _market_leadership  # noqa: E402
from candidate15_portfolio_materializer import (  # noqa: E402,F401
    far_stop_preserves_sweep_invalidation,
    materialize_candidate15_portfolio_source,
)
from candidate15_v6_residual_laggard_materializer import (  # noqa: E402,F401
    materialize_residual_laggard_source,
    residual_laggard_symbol,
)
from portfolio_materializer import materialize_combined_portfolio_source  # noqa: E402
from quarter_hour_persistent_initiative import (  # noqa: E402,F401
    QHI_ROUTER_KEY,
    PersistentInitiativeContinuationEngine,
)
from response_qualified_persistent_initiative import (  # noqa: E402,F401
    ResponseQualifiedPersistentQuarterHourRouter,
)
from runner_materializer import materialize_runner_source  # noqa: E402
from semantic_market_leadership import SemanticMarketLeadershipGate  # noqa: E402
from semantic_logic import install as _install_semantic_logic  # noqa: E402
from candidate15_logic import install as _install_candidate15_logic  # noqa: E402


_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
_install_semantic_logic()
_install_candidate15_logic()

_BASE = CANDIDATE14 / "run_leadership_scdam_base.py"
_SOURCE = _BASE.read_text(encoding="utf-8")
_SOURCE = materialize_runner_source(_SOURCE)
_SOURCE = materialize_combined_portfolio_source(_SOURCE)
_SOURCE = materialize_candidate15_portfolio_source(_SOURCE)
_SOURCE = materialize_residual_laggard_source(_SOURCE)
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
