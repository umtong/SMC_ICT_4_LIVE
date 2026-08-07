"""Install the v22 external-target hierarchy over v21's cost control."""
from __future__ import annotations

from typing import Any

import c10_liquidation_research as _research
import c10_liquidation_strategy as _strategy
from c10_v22_target import select_external_target
from v20_impact_control import (
    ImpactAwareLiquidationAuctionStateMachine,
    install as install_impact_control,
)


class ExternalTargetImpactStateMachine(ImpactAwareLiquidationAuctionStateMachine):
    """Keep v21 detection but target only completed 8h session liquidity."""

    def _target_pool(
        self,
        *,
        direction: int,
        entry: float,
        source_pool_id: str,
    ) -> Any | None:
        return select_external_target(
            self.pools,
            direction=direction,
            entry=entry,
            source_pool_id=source_pool_id,
        )


def install_external_target() -> None:
    """Install v21 impact control first, then replace only target selection."""

    install_impact_control()
    _strategy.LiquidationAuctionStateMachine = ExternalTargetImpactStateMachine
    _research._V22_EXTERNAL_TARGET_INSTALLED = True


__all__ = ["ExternalTargetImpactStateMachine", "install_external_target"]
