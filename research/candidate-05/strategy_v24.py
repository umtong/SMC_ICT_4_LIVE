#!/usr/bin/env python3
"""Candidate 05 v24: trade only reset-and-reaccelerated PBA retests."""
from __future__ import annotations

from typing import Any

from balance_acceptance_phase_logic import EARLY_RESET_REACCELERATION
from balance_acceptance_phase_logic import INVALID_OBSERVATION
from balance_acceptance_phase_logic import MATURE_AT_BREAKOUT
from balance_acceptance_phase_logic import NO_BROAD_FLOW_RESET
from balance_acceptance_phase_logic import NO_DIRECTIONAL_BREAKOUT
from balance_acceptance_phase_logic import NO_TAIL_REACCELERATION
from balance_acceptance_phase_logic import position_building_flow_phase
from strategy_base import LiquidityResponseConfig
from strategy_v16 import BalanceAcceptanceWatch
from strategy_v22 import ActualFillMilestoneStrategy


class ResetReacceleratedBalanceAcceptanceStrategy(ActualFillMilestoneStrategy):
    """Require an early expansion, broad-flow reset and retest reacceleration.

    v22's PBA path correctly required OI-sponsored expansion, directional depth
    migration, three closes outside the old balance, and a first retest which
    reclaimed the boundary with current tail flow and depth. It did not separate
    an early position-building auction from a mature impulse which was still
    accelerating at the retest.

    v24 retains the detector and all execution/risk logic, but permits the final
    PBA action only when the causal flow sequence is:

    1. breakout three-minute flow is directional but below the algebraic 2:1
       aggressor-ratio boundary;
    2. directional three-minute flow is lower at the actual first retest, showing
       that the broad impulse reset rather than remained extended;
    3. the final fifteen seconds reaccelerate above that cooled broad-flow state.

    This changes no balance, OI, depth, price, target, fee, slippage, stop,
    quantity, 3% risk-budget, execution, or global-slot rule.
    """

    _PHASE_DIAGNOSTIC = {
        MATURE_AT_BREAKOUT: "pba_flow_phase_mature_at_breakout",
        NO_DIRECTIONAL_BREAKOUT: "pba_flow_phase_no_directional_breakout",
        NO_BROAD_FLOW_RESET: "pba_flow_phase_no_broad_reset",
        NO_TAIL_REACCELERATION: "pba_flow_phase_no_tail_reacceleration",
        INVALID_OBSERVATION: "pba_flow_phase_invalid_observation",
    }

    _PHASE_REASON = {
        MATURE_AT_BREAKOUT: "POSITION_BUILDING_BREAKOUT_FLOW_ALREADY_MATURE",
        NO_DIRECTIONAL_BREAKOUT: "POSITION_BUILDING_BREAKOUT_FLOW_NOT_DIRECTIONAL",
        NO_BROAD_FLOW_RESET: "POSITION_BUILDING_BROAD_FLOW_DID_NOT_RESET",
        NO_TAIL_REACCELERATION: "POSITION_BUILDING_RETEST_TAIL_DID_NOT_REACCELERATE",
        INVALID_OBSERVATION: "POSITION_BUILDING_FLOW_PHASE_OBSERVATION_INVALID",
    }

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "pba_flow_phase_confirmed": 0,
                "pba_flow_phase_mature_at_breakout": 0,
                "pba_flow_phase_no_directional_breakout": 0,
                "pba_flow_phase_no_broad_reset": 0,
                "pba_flow_phase_no_tail_reacceleration": 0,
                "pba_flow_phase_invalid_observation": 0,
            },
        )

    def _submit_balance_acceptance(
        self,
        watch: BalanceAcceptanceWatch,
        row: dict[str, float | int],
    ) -> bool:
        breakout_flow_3m = float(watch.details.get("flow_3m", float("nan")))
        retest_flow_3m = self._feature("flow_3m")
        retest_flow_15s = self._feature("flow_15s")
        phase = position_building_flow_phase(
            side=watch.side,
            breakout_flow_3m=breakout_flow_3m,
            retest_flow_3m=retest_flow_3m,
            retest_flow_15s=retest_flow_15s,
        )
        phase_details: dict[str, Any] = {
            **watch.details,
            "pba_flow_phase": phase,
            "pba_breakout_flow_3m": breakout_flow_3m,
            "pba_retest_flow_3m": retest_flow_3m,
            "pba_retest_flow_15s": retest_flow_15s,
            "pba_directional_breakout_flow_3m": watch.side * breakout_flow_3m,
            "pba_directional_retest_flow_3m": watch.side * retest_flow_3m,
            "pba_directional_retest_flow_15s": watch.side * retest_flow_15s,
        }
        watch.details.update(phase_details)

        if phase != EARLY_RESET_REACCELERATION:
            key = self._PHASE_DIAGNOSTIC[phase]
            self.diagnostics[key] += 1
            self._expire_balance_watch(
                row,
                self._PHASE_REASON[phase],
            )
            return False

        self.diagnostics["pba_flow_phase_confirmed"] += 1
        self._transition(
            watch.scenario_id,
            "POSITION_BUILDING_FLOW_RESET_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "BALANCE_ACCEPTANCE_ENTRY_ELIGIBLE",
            "EARLY_EXPANSION_RESET_THEN_RETEST_TAIL_REACCELERATED",
            float(row["close"]),
            phase_details,
        )
        return super()._submit_balance_acceptance(watch, row)


__all__ = ["ResetReacceleratedBalanceAcceptanceStrategy"]
