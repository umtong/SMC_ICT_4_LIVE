"""Install candidate-10 v23 over the verified v22 execution stack."""
from __future__ import annotations

import c10_liquidation_research as _research
import c10_liquidation_strategy as _strategy
from c10_v22_install import install_external_target
from c10_v23_state import OISemanticExternalTargetStateMachine


def install_oi_semantic_mapping() -> None:
    """Keep v22 targets/costs and replace only OI directional semantics."""

    # Import after v22 installation so the live-cost class subclasses the exact
    # v22 target/execution strategy rather than the earlier generic strategy.
    install_external_target()
    from c10_live_cost_ledger import install_live_cost_ledger

    install_live_cost_ledger()
    _strategy.LiquidationAuctionStateMachine = (
        OISemanticExternalTargetStateMachine
    )
    _research._V23_OI_SEMANTIC_MAPPING_INSTALLED = True


__all__ = ["install_oi_semantic_mapping"]
