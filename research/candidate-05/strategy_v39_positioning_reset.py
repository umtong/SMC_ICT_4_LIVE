#!/usr/bin/env python3
"""Candidate 05 v39: positioning-reset gate for early sponsored CHoCH."""
from __future__ import annotations

from positioning_reset_logic import completed_path_efficiency
from positioning_reset_logic import positioning_reset_supports_early_reversal
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_v26 import ScenarioValidEntryStrategy


class PositioningResetReversalStrategy(ScenarioValidEntryStrategy):
    """Route premature CHoCHs to observation instead of immediate participation.

    v39 changes one decision only. An early coherent sponsored CHoCH may use the
    existing price-capped immediate order only when the sweep was preceded by a
    directional 30-minute path, the premium index was already normalizing in the
    proposed reversal direction, and 15-minute open interest is not materially
    expanding at CHoCH. Otherwise the unchanged v17 path waits for a causal
    retrace/breakaway response. Detector, target, stop, costs, 3% NAV sizing,
    Nautilus execution and pending-order lifecycle are inherited unchanged.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "positioning_reset_early_participation_pass": 0,
                "positioning_reset_early_participation_deferred": 0,
            },
        )

    def _path_efficiency_30m(self) -> float:
        rows = list(self.bars)
        if len(rows) < 31:
            return float("nan")
        closes = [float(row["close"]) for row in rows[-31:]]
        return completed_path_efficiency(closes)

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        previous_scenario = None if self.pending is None else self.pending.scenario_id
        super()._detect_sweep(row, previous_close)
        setup = self.pending
        if setup is None or setup.scenario_id == previous_scenario:
            return
        setup.details.update(
            {
                "sweep_premium_change_5m": self._feature("premium_change_5m"),
                "sweep_path_efficiency_30m": self._path_efficiency_30m(),
            },
        )

    def _early_sponsored_participation_allowed(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        flow_3m: float,
    ) -> bool:
        if not super()._early_sponsored_participation_allowed(
            setup,
            row,
            flow_3m,
        ):
            return False
        allowed = positioning_reset_supports_early_reversal(
            side=setup.side,
            sweep_premium_change_5m=float(
                setup.details.get("sweep_premium_change_5m", float("nan")),
            ),
            sweep_path_efficiency_30m=float(
                setup.details.get("sweep_path_efficiency_30m", float("nan")),
            ),
            choch_oi_change_15m=self._feature("oi_change_15m"),
        )
        key = (
            "positioning_reset_early_participation_pass"
            if allowed
            else "positioning_reset_early_participation_deferred"
        )
        self.diagnostics[key] += 1
        return allowed


__all__ = ["PositioningResetReversalStrategy"]
