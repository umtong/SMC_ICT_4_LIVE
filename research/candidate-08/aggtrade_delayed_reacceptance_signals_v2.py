"""Compatibility import for the superseding delayed-reacceptance V3 implementation.

The first native run proved that V2's economic detector and execution path completed, but its
research-event evidence omitted one observable state transition.  Existing orchestrator imports are
kept stable while all current execution is routed to the V3 safe-observability and complete-chain
implementation.  Historical V2 evidence remains reproducible from its original commit.
"""

from aggtrade_delayed_reacceptance_signals_v3 import (
    ABLATION_INITIAL_MODE,
    BASE_INITIAL_MODE,
    DelayedReacceptanceConfig,
    IMPLEMENTATION_REVISION,
    REACCEPTANCE_FAMILY,
    build_delayed_reacceptance_signals,
)


__all__ = [
    "ABLATION_INITIAL_MODE",
    "BASE_INITIAL_MODE",
    "DelayedReacceptanceConfig",
    "IMPLEMENTATION_REVISION",
    "REACCEPTANCE_FAMILY",
    "build_delayed_reacceptance_signals",
]
