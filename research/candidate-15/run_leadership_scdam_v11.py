#!/usr/bin/env python3
"""Candidate 15 V11 beta diffusion plus completed-source auction router."""
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
from bounded_transfer_initiative import (  # noqa: E402,F401
    BoundedResidualTransferContinuationEngine,
    BoundedTransferInitiativeState,
    BoundedTransferPersistentQuarterHourRouter,
    V7_MODULE,
    V7_ROUTER_KEY,
)
from managed_transfer_initiative import (  # noqa: E402,F401
    ManagedResidualTransferContinuationEngine,
    ManagedTransferPersistentQuarterHourRouter,
    V8_MODULE,
    V8_ROUTER_KEY,
)
from beta_coherent_transfer import (  # noqa: E402,F401
    BetaCoherentResidualTransferContinuationEngine,
    BetaCoherentTransferPersistentQuarterHourRouter,
    BetaCoherentTransferState,
    V9_MODULE,
    V9_ROUTER_KEY,
)
from candidate15_portfolio_materializer import (  # noqa: E402,F401
    far_stop_preserves_sweep_invalidation,
    materialize_candidate15_portfolio_source,
)
from candidate15_v6_residual_laggard_materializer import (  # noqa: E402,F401
    materialize_residual_laggard_source,
    residual_laggard_symbol,
)
from candidate15_v7_bounded_transfer_materializer import (  # noqa: E402,F401
    materialize_bounded_transfer_source,
)
from candidate15_v8_managed_transfer_materializer import (  # noqa: E402,F401
    materialize_managed_transfer_source,
)
from candidate15_v9_beta_transfer_materializer import (  # noqa: E402,F401
    materialize_beta_coherent_transfer_source,
)
from candidate15_v10_cost_cover_materializer import (  # noqa: E402,F401
    materialize_execution_valid_cost_cover_source,
)
from candidate15_v11_completed_auction_materializer import (  # noqa: E402,F401
    completed_source_auction_family,
    materialize_v11_completed_auction_router_source,
)
from candidate15_v11_market_leadership import (  # noqa: E402
    Candidate15V11SemanticMarketLeadershipGate,
)
from positive_cost_cover import positive_cost_cover_trigger  # noqa: E402,F401
from portfolio_materializer import materialize_combined_portfolio_source  # noqa: E402
from quarter_hour_persistent_initiative import (  # noqa: E402,F401
    QHI_ROUTER_KEY,
    PersistentInitiativeContinuationEngine,
)
from response_qualified_persistent_initiative import (  # noqa: E402,F401
    ResponseQualifiedPersistentQuarterHourRouter,
)
from runner_materializer import materialize_runner_source  # noqa: E402
from c13_semantic_market_leadership_v16 import (  # noqa: E402,F401
    FAR_ROTATION_SOURCE_NOT_TRANSFER,
)
from c13_semantic_logic_v15 import install as _install_candidate13_v15_logic  # noqa: E402
from semantic_logic import install as _install_semantic_logic  # noqa: E402
from candidate15_logic import install as _install_candidate15_logic  # noqa: E402


_market_leadership.MarketLeadershipGate = Candidate15V11SemanticMarketLeadershipGate
_install_semantic_logic()
_install_candidate13_v15_logic()
_install_candidate15_logic()

_BASE = CANDIDATE14 / "run_leadership_scdam_base.py"
_SOURCE = _BASE.read_text(encoding="utf-8")
_SOURCE = materialize_runner_source(_SOURCE)
_SOURCE = materialize_combined_portfolio_source(_SOURCE)
_SOURCE = materialize_candidate15_portfolio_source(_SOURCE)
_SOURCE = materialize_residual_laggard_source(_SOURCE)
_SOURCE = materialize_bounded_transfer_source(_SOURCE)
_SOURCE = materialize_managed_transfer_source(_SOURCE)
_SOURCE = materialize_beta_coherent_transfer_source(_SOURCE)
_SOURCE = materialize_v11_completed_auction_router_source(_SOURCE)
_SOURCE = materialize_execution_valid_cost_cover_source(_SOURCE)
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
