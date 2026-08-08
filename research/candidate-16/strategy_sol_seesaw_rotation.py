"""Crypto-specific negative lead-lag rotation for SOL.

A completed BTC/ETH/XRP multi-peer OFI shock plus a same-direction SOL local
transmission is treated as a temporary relative-money-flow imbalance, not as a
continuation signal.  Only SOL takes the opposite side; the other project
symbols publish causal context and never open a position.
"""
from __future__ import annotations

from typing import Any

from cross_impact_context import LAGGED_CROSS_IMPACT_CONTEXT
from strategy_base import PendingSetup
from strategy_cross_impact_continuation import (
    LaggedCrossImpactContinuationStrategy,
)


SCENARIO_FAMILY = "SOL_SEESAW_FLOW_ROTATION"
TRADED_SYMBOL = "SOLUSDT"


class SolSeesawFlowRotationStrategy(LaggedCrossImpactContinuationStrategy):
    """Fade completed cross-market transmission only in the follower SOL leg."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "seesaw_publisher_only_bars": 0,
                "seesaw_actionable": 0,
                "seesaw_entry_submissions": 0,
            },
        )

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        del previous_close
        current = self._current_cross_impact_observation()
        if current is None:
            self._count_reason("CURRENT_OBSERVATION_UNAVAILABLE")
            return

        self.diagnostics["cross_impact_evaluations"] = int(
            self.diagnostics["cross_impact_evaluations"],
        ) + 1
        if self.cross_impact_symbol != TRADED_SYMBOL:
            self.diagnostics["seesaw_publisher_only_bars"] = int(
                self.diagnostics["seesaw_publisher_only_bars"],
            ) + 1
            self._count_reason("SEESAW_INFORMATION_ORIGIN_ONLY")
            return

        transmission = LAGGED_CROSS_IMPACT_CONTEXT.decide(
            target_symbol=self.cross_impact_symbol,
            current=current,
        )
        self._count_reason(transmission.reason)
        if not transmission.actionable:
            return
        if transmission.peer_event_time_ns <= self._cross_impact_last_peer_event_ns:
            self.diagnostics["cross_impact_duplicate_peer_events"] = int(
                self.diagnostics["cross_impact_duplicate_peer_events"],
            ) + 1
            return

        self._cross_impact_last_peer_event_ns = transmission.peer_event_time_ns
        self.diagnostics["cross_impact_actionable"] = int(
            self.diagnostics["cross_impact_actionable"],
        ) + 1
        self.diagnostics["seesaw_actionable"] = int(
            self.diagnostics["seesaw_actionable"],
        ) + 1

        # The experiment changes one semantic role only: a fully transmitted
        # crypto peer shock is interpreted as relative-flow exhaustion in SOL.
        # Signal occurrence, timing, and causal information remain frozen from
        # the continuation experiment; entry direction and its matching local
        # invalidation/objective are reversed.
        side = -int(transmission.side)
        atr = self._atr()
        recent = list(self.bars)[-3:]
        structure = (
            min(float(item["low"]) for item in recent)
            if side > 0
            else max(float(item["high"]) for item in recent)
        )

        self.scenario_counter += 1
        scenario_id = f"sol-seesaw-{self.scenario_counter:07d}"
        details = {
            "scenario_family": SCENARIO_FAMILY,
            "target_symbol": self.cross_impact_symbol,
            "information_origins": list(transmission.peer_symbols),
            "transmission_side": int(transmission.side),
            "trade_side": side,
            "transmission": transmission.to_dict(),
            "entry_state": {
                "interpretation": "COMPLETED_RELATIVE_FLOW_TRANSMISSION",
                "flow_15s": current.flow_15s,
                "flow_60s": current.flow_60s,
                "flow_3m": current.flow_3m,
                "ret_atr": current.ret_atr,
                "efficiency_60s": current.efficiency_60s,
                "notional_burst": current.notional_burst,
                "depth_imbalance_1": current.depth_imbalance_1,
            },
            "invalidation_structure": structure,
            "invalidation_source": "CURRENT_THREE_BAR_SEESAW_EXTREME",
            "objective_source": "PREEXISTING_OPPOSITE_LIQUIDITY_OR_COST_VALID_FALLBACK",
        }
        setup = PendingSetup(
            scenario_id=scenario_id,
            branch=SCENARIO_FAMILY,
            side=side,
            swept_kind="LOW" if side > 0 else "HIGH",
            pool_id=f"sol-seesaw-context-{transmission.peer_event_time_ns}",
            pool_level=structure,
            created_index=self.bar_index,
            expires_index=self.bar_index + 1,
            sweep_extreme=structure,
            structure=structure,
            atr=atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        self.pending = setup
        self._transition(
            scenario_id,
            "SOL_SEESAW_ROTATION_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_ARMED",
            "CRYPTO_NEGATIVE_LEAD_LAG_AFTER_COMPLETED_TRANSMISSION",
            float(row["close"]),
            details,
        )
        submitted = bool(self._submit_entry(setup, row))
        if submitted:
            self.diagnostics["cross_impact_entry_submissions"] = int(
                self.diagnostics["cross_impact_entry_submissions"],
            ) + 1
            self.diagnostics["seesaw_entry_submissions"] = int(
                self.diagnostics["seesaw_entry_submissions"],
            ) + 1
        elif self.pending is setup:
            self._expire_pending(row, "SOL_SEESAW_ENTRY_NOT_SUBMITTED")


__all__ = ["SCENARIO_FAMILY", "SolSeesawFlowRotationStrategy", "TRADED_SYMBOL"]
