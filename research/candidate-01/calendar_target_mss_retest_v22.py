#!/usr/bin/env python3
"""Failed intrinsic sweep -> MSS -> retest with calendar external target.

The v21 trigger, market-structure shift, broken-pivot retest and local
invalidation are frozen. The sole candidate variable is target hierarchy.

The nearer of the two latest opposing 40-bps directional-change pivots remains
the MSS level. The farther pivot is treated as intermediate local liquidity.
At the failed-sweep confirmation time, the scenario selects the nearest active,
unconsumed completed-day or completed-week high/low strictly beyond that local
intermediate pivot in the trade direction. No target is selected from reward to
risk or PnL. If no causal calendar level exists, the setup is not armed.
"""
from __future__ import annotations

from dataclasses import replace

from core import Side
from directional_change_failed_sweep_week import DirectionalChangeEvent
from directional_change_mss_retest_v21 import (
    DirectionalChangeMssRetestStateMachine,
    MssRetestSetup,
)
from impact_regime_probe import EventFeature, ScenarioPlan
from causal_calendar_liquidity_v22 import (
    CalendarTargetSelection,
    CausalCalendarLiquidityBook,
)


class CalendarTargetMssRetestStateMachine(
    DirectionalChangeMssRetestStateMachine,
):
    """Use causal calendar pools beyond the local DC hierarchy as targets."""

    def __init__(self) -> None:
        super().__init__()
        self.calendar_book = CausalCalendarLiquidityBook()
        self.target_selections: list[CalendarTargetSelection] = []

    def _arm_from_event(
        self,
        *,
        event: DirectionalChangeEvent,
        feature: EventFeature,
    ) -> None:
        if event.event_type == "DOWN":
            if not self.high_events or len(self.low_events) < 2:
                self.counts["insufficient_confirmed_liquidity"] += 1
                self.high_events.append(event)
                return
            prior_same = self.high_events[-1]
            opposing = self.low_events[-2:]
            side = Side.SHORT
            boundary = float(prior_same.pivot_price)
            internal = max(float(item.pivot_price) for item in opposing)
            intermediate = min(float(item.pivot_price) for item in opposing)
            sweep = event.pivot_price > prior_same.pivot_price
            reentered = event.confirmation_price < prior_same.pivot_price
            flow = (
                event.trend_flow_imbalance > 0.0
                and event.reversal_flow_imbalance < 0.0
            )
            hierarchy = intermediate < internal < boundary
            self.high_events.append(event)
        else:
            if not self.low_events or len(self.high_events) < 2:
                self.counts["insufficient_confirmed_liquidity"] += 1
                self.low_events.append(event)
                return
            prior_same = self.low_events[-1]
            opposing = self.high_events[-2:]
            side = Side.LONG
            boundary = float(prior_same.pivot_price)
            internal = min(float(item.pivot_price) for item in opposing)
            intermediate = max(float(item.pivot_price) for item in opposing)
            sweep = event.pivot_price < prior_same.pivot_price
            reentered = event.confirmation_price > prior_same.pivot_price
            flow = (
                event.trend_flow_imbalance < 0.0
                and event.reversal_flow_imbalance > 0.0
            )
            hierarchy = boundary < internal < intermediate
            self.low_events.append(event)

        if not sweep:
            self.counts["no_same_side_liquidity_sweep"] += 1
            return
        if not reentered:
            self.counts["outside_value_retained"] += 1
            return
        if not flow:
            self.counts["order_flow_did_not_reverse"] += 1
            return
        if not hierarchy:
            self.counts["invalid_liquidity_hierarchy"] += 1
            return

        scenario_id = (
            f"dc-calendar-mss:{event.confirmation_index}:"
            f"{side.value.lower()}:{event.confirmation_time_ns}"
        )
        selection = self.calendar_book.select_target(
            scenario_id=scenario_id,
            signal_time_ns=int(event.confirmation_time_ns),
            side=side,
            local_internal_pivot=internal,
            local_intermediate_pivot=intermediate,
        )
        if selection is None:
            self.counts["no_active_calendar_target_beyond_local_intermediate"] += 1
            return

        target = float(selection.target_price)
        # select_target only returns active levels beyond the local intermediate
        # pivot. This explicit check guards serialization/precision regressions.
        target_hierarchy = (
            target > intermediate if side is Side.LONG else target < intermediate
        )
        if not target_hierarchy:
            raise RuntimeError(
                f"calendar target hierarchy regression for {scenario_id}",
            )

        self._invalidate_same_side(
            side=side,
            feature=feature,
            index=event.confirmation_index,
        )
        setup = MssRetestSetup(
            scenario_id=scenario_id,
            side=side,
            phase="WAIT_MSS",
            created_index=int(event.confirmation_index),
            created_time_ns=int(event.confirmation_time_ns),
            boundary=boundary,
            internal_pivot=internal,
            external_target=target,
            sweep_path_high=float(event.path_high),
            sweep_path_low=float(event.path_low),
            trend_flow_imbalance=float(event.trend_flow_imbalance),
            reversal_flow_imbalance=float(event.reversal_flow_imbalance),
        )
        self.active.append(setup)
        self.target_selections.append(selection)
        self.counts["armed"] += 1
        self.counts[f"calendar_target_{selection.target_period.lower()}"] += 1
        self._transition(
            setup=setup,
            feature=feature,
            index=event.confirmation_index,
            event_type="ARMED",
            reason_code=(
                "FAILED_INTRINSIC_SWEEP_WAITING_FOR_MSS_WITH_"
                f"{selection.target_period}_EXTERNAL_TARGET"
            ),
        )

    @staticmethod
    def _plan(
        setup: MssRetestSetup,
        feature: EventFeature,
        index: int,
    ) -> ScenarioPlan:
        plan = DirectionalChangeMssRetestStateMachine._plan(
            setup,
            feature,
            index,
        )
        return replace(
            plan,
            reason_code=(
                "FAILED_SWEEP_MSS_BROKEN_PIVOT_RETEST_TO_"
                "CALENDAR_EXTERNAL_LIQUIDITY"
            ),
        )

    def on_feature(
        self,
        *,
        index: int,
        features: list[EventFeature],
    ) -> list[ScenarioPlan]:
        self.calendar_book.on_bar(features[index].bar)
        return super().on_feature(index=index, features=features)
