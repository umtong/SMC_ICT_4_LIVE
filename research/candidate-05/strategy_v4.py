#!/usr/bin/env python3
"""Candidate 05 v4: 5m liquidity rejection with directional depth support."""
from __future__ import annotations

from typing import Any

from depth_logic import DIRECTIONAL_DEPTH_MIN
from depth_logic import directional_depth_support
from strategy_base import LiquidityResponseConfig
from strategy_v2 import LiquidityResponseRetraceStrategy


class LiquidityResponseDepthStrategy(LiquidityResponseRetraceStrategy):
    """Require the replenished book to support the proposed reversal direction.

    v2 already required aggressive sweep flow, inefficient price response,
    consumed-side replenishment, range reclaim, opposite CHoCH, and a first 50%
    retrace. v4 changes one causal family only: before CHoCH can create an order,
    the aggregate +/-1% depth imbalance observed at the sweep must show at least
    a ten-percentage-point edge in the reversal direction.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "directional_depth_min": DIRECTIONAL_DEPTH_MIN,
                "directional_depth_pass": 0,
                "directional_depth_fail": 0,
            },
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is not None and not bool(setup.details.get("directional_depth_checked", False)):
            imbalance = float(setup.details.get("depth_imbalance_1", float("nan")))
            directional = setup.side * imbalance
            setup.details["directional_depth_checked"] = True
            setup.details["directional_depth_support"] = directional
            setup.details["directional_depth_min"] = DIRECTIONAL_DEPTH_MIN
            if not directional_depth_support(
                side=setup.side,
                depth_imbalance=imbalance,
                minimum=DIRECTIONAL_DEPTH_MIN,
            ):
                self.diagnostics["directional_depth_fail"] = int(
                    self.diagnostics["directional_depth_fail"],
                ) + 1
                self._expire_pending(row, "DIRECTIONAL_DEPTH_DID_NOT_SUPPORT_REVERSAL")
                return False
            self.diagnostics["directional_depth_pass"] = int(
                self.diagnostics["directional_depth_pass"],
            ) + 1
            self._transition(
                setup.scenario_id,
                "DIRECTIONAL_DEPTH_CONFIRMED",
                int(row["ts"]),
                int(row["ts"]),
                "CHOCH_ARMED",
                "RESTING_DEPTH_SUPPORTS_REVERSAL_DIRECTION",
                float(row["close"]),
                setup.details,
            )
        return super()._process_pending(row)


__all__ = ["LiquidityResponseDepthStrategy"]
