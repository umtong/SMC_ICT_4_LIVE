#!/usr/bin/env python3
"""One-variable target control for rolling failed auctions.

The baseline rolling two-hour failed-auction state machine confirmed 26 first-
week rotations, but all equilibrium-target plans failed the fixed cost-adjusted
reward/risk gate.  This control changes only the structural objective from range
equilibrium to the opposite external liquidity edge.  Sweep detection,
re-entry, opposite-flow confirmation, entry delay, boundary hold, strict path
stop, six-bar cooldown, costs, 3% current-NAV risk and one global position are
unchanged.

A failed auction that cannot establish value outside one edge can logically
rotate through equilibrium toward the still-resting liquidity at the opposite
edge.  One invocation evaluates exactly one BTC week.
"""

from __future__ import annotations

from core import Side
from impact_regime_probe import EventFeature, ScenarioPlan
import rolling_range_sweep_week as base
from rolling_range_sweep_week import STOP_BUFFER_ATR, SweepSetup


class OppositeEdgeRollingRangeStateMachine(base.RollingRangeSweepStateMachine):
    @staticmethod
    def _plan(setup: SweepSetup, feature: EventFeature, index: int) -> ScenarioPlan:
        stop = (
            setup.path_high + STOP_BUFFER_ATR * setup.atr
            if setup.side is Side.SHORT
            else setup.path_low - STOP_BUFFER_ATR * setup.atr
        )
        target = setup.range_low if setup.side is Side.SHORT else setup.range_high
        return ScenarioPlan(
            scenario_id=setup.scenario_id + f":opposite-edge:{index}",
            response="EXHAUSTION_REVERSAL",
            side=setup.side,
            signal_bar_index=index,
            signal_time_ns=feature.bar.end_time_ns,
            stop_price=stop,
            target_price=target,
            confirmation_hold_price=setup.boundary,
            structure_high=setup.range_high,
            structure_low=setup.range_low,
            structure_midpoint=setup.range_midpoint,
            pulse_high=setup.path_high,
            pulse_low=setup.path_low,
            pulse_flow_score=0.0,
            pulse_move_atr=0.0,
            pulse_path_efficiency=0.0,
            pulse_close_location=0.0,
            reason_code="ROLLING_RANGE_FAILED_AUCTION_TO_OPPOSITE_EDGE",
        )


base.RollingRangeSweepStateMachine = OppositeEdgeRollingRangeStateMachine


if __name__ == "__main__":
    raise SystemExit(base.run(base.build_parser().parse_args()))
