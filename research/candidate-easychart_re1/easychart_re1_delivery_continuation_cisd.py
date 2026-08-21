"""Event-driven post-touch structure shift for matching-scale continuation.

A first footprint touch can reverse immediately, but a slower inventory
transfer should not be forced into the next one-minute candle and should not be
allowed to wait for an outcome-selected eventual break of the touch extreme.
The causal alternative is the first *new* internal structure created after the
pullback:

* an immediate active/directed footprint response may still enter on the touch;
* otherwise, the first causally confirmed span-2 one-minute swing formed at or
  after the touch becomes the fixed control boundary (HIGH for a long, LOW for
  a short), provided it has not already been crossed when it becomes known;
* a strictly later completed one-minute close must freshly cross that boundary;
* that first cross either carries active directed re-initiative or demonstrates
  adverse aggression being absorbed while price advances, or the episode ends
  without a trade.

No clock expiry, fitted distance, score or eventual-best response is used.
Source invalidation, objective consumption, common-state change, complete
first-obstacle geometry and the proximal external-delivery router remain
unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import Pivot, V5TradePlan
from domain import Candle, Side
from easychart_re1_delivery_continuation_v2 import (
    COMPLETE_MICRO_FIRST_OBSTACLE_RULE,
    DeliveryContinuationEngineV2,
)
from easychart_re1_efficient_objective import PivotOnlyObjectiveBook
from easychart_re1_persistent_continuation import (
    PERSISTENT_FIRST_RETURN_RULE,
    PersistentContinuationSetup,
)


POST_TOUCH_INTERNAL_SHIFT_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "WHEN_THE_FIRST_FOOTPRINT_TOUCH_HAS_NO_IMMEDIATE_RESPONSE_THE_FIRST_NEW_CAUSALLY_CONFIRMED_SPAN2_ONE_MINUTE_SWING_OWNS_A_STRICTLY_LATER_FRESH_CONTROL_SHIFT"
)
FIRST_INTERNAL_SHIFT_CROSS_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "THE_FIRST_STRICTLY_LATER_CLOSE_CROSS_OF_THE_FIXED_POST_TOUCH_INTERNAL_SWING_EITHER_CONFIRMS_ACTIVE_CONTROL_TRANSFER_OR_ENDS_THE_EPISODE"
)
for _rule in (
    POST_TOUCH_INTERNAL_SHIFT_RULE,
    FIRST_INTERNAL_SHIFT_CROSS_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(slots=True)
class PendingInternalShift:
    pivot: Pivot | None = None
    rejected_pivot_ids: set[str] = field(default_factory=set)


class DeliveryContinuationCISDEngine(DeliveryContinuationEngineV2):
    """Complete-obstacle continuation with immediate OR structural response."""

    INTERNAL_SHIFT_SPAN = 2

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.response_structure = PivotOnlyObjectiveBook(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
            pivot_spans=(self.INTERNAL_SHIFT_SPAN,),
        )
        self._pending_internal_shifts: dict[str, PendingInternalShift] = {}
        self._cisd_counts: dict[str, int] = {}

    def _cinc(self, key: str) -> None:
        self._cisd_counts[key] = self._cisd_counts.get(key, 0) + 1

    def _finish(
        self,
        setup: PersistentContinuationSetup,
        reason: str,
        time_ns: int,
        **values: Any,
    ) -> None:
        self._pending_internal_shifts.pop(setup.setup_id, None)
        super()._finish(setup, reason, time_ns, **values)

    @staticmethod
    def _wanted_pivot_side(side: Side) -> str:
        return "HIGH" if side is Side.LONG else "LOW"

    @staticmethod
    def _old_side(side: Side, close: float, level: float) -> bool:
        return close <= level if side is Side.LONG else close >= level

    @staticmethod
    def _fresh_cross(
        side: Side,
        previous_close: float,
        current_close: float,
        level: float,
    ) -> bool:
        if side is Side.LONG:
            return previous_close <= level and current_close > level
        return previous_close >= level and current_close < level

    def _arm_first_internal_pivot(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
        pending: PendingInternalShift,
    ) -> Pivot | None:
        assert setup.first_touch_time_ns is not None
        wanted = self._wanted_pivot_side(setup.side)
        candidates = sorted(
            (
                pivot
                for pivot in self.response_structure.pivots
                if pivot.span == self.INTERNAL_SHIFT_SPAN
                and pivot.side == wanted
                and pivot.event_time_ns >= setup.first_touch_time_ns
                and pivot.observed_time_ns <= bar.ts_close_ns
                and pivot.pivot_id not in pending.rejected_pivot_ids
            ),
            key=lambda item: (
                item.observed_time_ns,
                item.event_time_ns,
                item.pivot_id,
            ),
        )
        for pivot in candidates:
            if not self._old_side(setup.side, bar.close, pivot.price):
                pending.rejected_pivot_ids.add(pivot.pivot_id)
                self._cinc("post_touch_pivot_already_crossed_when_confirmed")
                self._trace(
                    "post_touch_pivot_already_crossed_when_confirmed",
                    bar.ts_close_ns,
                    setup_id=setup.setup_id,
                    side=setup.side.name,
                    pivot_id=pivot.pivot_id,
                    pivot_price=pivot.price,
                    pivot_event_time_ns=pivot.event_time_ns,
                    pivot_observed_time_ns=pivot.observed_time_ns,
                    close=bar.close,
                    rule_provenance=POST_TOUCH_INTERNAL_SHIFT_RULE,
                )
                continue
            pending.pivot = pivot
            self._cinc("post_touch_internal_pivot_armed")
            self._trace(
                "post_touch_internal_pivot_armed",
                bar.ts_close_ns,
                setup_id=setup.setup_id,
                side=setup.side.name,
                pivot_id=pivot.pivot_id,
                pivot_side=pivot.side,
                pivot_price=pivot.price,
                pivot_event_time_ns=pivot.event_time_ns,
                pivot_observed_time_ns=pivot.observed_time_ns,
                touch_time_ns=setup.first_touch_time_ns,
                rule_provenance=POST_TOUCH_INTERNAL_SHIFT_RULE,
            )
            return pivot
        return None

    def _shift_mechanism(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
        observation: Any,
    ) -> str | None:
        if (
            observation is None
            or not observation.active
            or not observation.directed
        ):
            return None
        body_aligned = (
            bar.close > bar.open
            if setup.side is Side.LONG
            else bar.close < bar.open
        )
        if not body_aligned:
            return None
        if (
            observation.material_progress
            and self._aligned(
                setup.side,
                observation.signed_taker_quote,
            )
        ):
            return "POST_TOUCH_INTERNAL_SHIFT_REINITIATIVE"
        if self._opposite_delta(
            setup.side,
            observation.signed_taker_quote,
        ):
            return "POST_TOUCH_INTERNAL_SHIFT_ADVERSE_FLOW_ABSORBED"
        return None

    def _advance_setups(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        observation = self._current_flow
        previous_close = (
            None
            if len(self.response_structure.bars) < 2
            else self.response_structure.bars[-2].close
        )
        for setup in list(self._active.values()):
            if not self._setup_context_survives(setup):
                self._finish(
                    setup,
                    "persistent_continuation_common_regime_changed",
                    bar.ts_close_ns,
                    regime=self.common_snapshot.regime.value,
                    latest_side=None
                    if self.common_snapshot.side is None
                    else self.common_snapshot.side.name,
                )
                continue
            if bar.ts_close_ns <= setup.source_zone.observed_time_ns:
                continue
            if self._target_touched(setup, bar):
                self._finish(
                    setup,
                    "persistent_continuation_target_spent_before_entry",
                    bar.ts_close_ns,
                )
                continue
            if self._stop_touched(setup, bar):
                self._finish(
                    setup,
                    "persistent_continuation_source_invalidated_before_entry",
                    bar.ts_close_ns,
                )
                continue

            if setup.first_touch_time_ns is None:
                touched = (
                    bar.low <= setup.source_zone.upper
                    and bar.high >= setup.source_zone.lower
                )
                if not touched:
                    continue
                setup.first_touch_time_ns = bar.ts_close_ns
                setup.touch_high = bar.high
                setup.touch_low = bar.low
                immediate, mechanism = self._immediate_response(
                    setup,
                    bar,
                    observation,
                )
                if immediate:
                    plan = self._make_plan(setup, bar, mechanism)
                    if plan is not None:
                        self._finish(
                            setup,
                            "persistent_continuation_planned",
                            bar.ts_close_ns,
                            plan_id=plan.plan_id,
                            response_owner="IMMEDIATE_FIRST_TOUCH",
                        )
                        output.append(plan)
                    else:
                        self._finish(
                            setup,
                            "persistent_continuation_no_trade_geometry",
                            bar.ts_close_ns,
                        )
                    continue
                self._pending_internal_shifts[setup.setup_id] = (
                    PendingInternalShift()
                )
                self._cinc("first_touch_waiting_internal_structure_shift")
                self._trace(
                    "first_touch_waiting_internal_structure_shift",
                    bar.ts_close_ns,
                    setup_id=setup.setup_id,
                    side=setup.side.name,
                    touch_high=bar.high,
                    touch_low=bar.low,
                    touch_close=bar.close,
                    rule_provenance=(
                        PERSISTENT_FIRST_RETURN_RULE,
                        POST_TOUCH_INTERNAL_SHIFT_RULE,
                    ),
                )
                continue

            pending = self._pending_internal_shifts.setdefault(
                setup.setup_id,
                PendingInternalShift(),
            )
            pivot = pending.pivot
            if pivot is None:
                self._arm_first_internal_pivot(setup, bar, pending)
                continue
            if bar.ts_close_ns <= pivot.observed_time_ns:
                continue
            if previous_close is None:
                continue
            if not self._fresh_cross(
                setup.side,
                previous_close,
                bar.close,
                pivot.price,
            ):
                continue

            mechanism = self._shift_mechanism(
                setup,
                bar,
                observation,
            )
            if mechanism is None:
                self._finish(
                    setup,
                    "post_touch_first_internal_shift_failed_flow_transfer",
                    bar.ts_close_ns,
                    pivot_id=pivot.pivot_id,
                    pivot_price=pivot.price,
                    previous_close=previous_close,
                    shift_close=bar.close,
                    flow_active=None
                    if observation is None
                    else observation.active,
                    flow_directed=None
                    if observation is None
                    else observation.directed,
                    rule_provenance=FIRST_INTERNAL_SHIFT_CROSS_RULE,
                )
                continue
            plan = self._make_plan(setup, bar, mechanism)
            if plan is None:
                self._finish(
                    setup,
                    "persistent_continuation_no_trade_geometry",
                    bar.ts_close_ns,
                    pivot_id=pivot.pivot_id,
                    pivot_price=pivot.price,
                )
                continue
            self._finish(
                setup,
                "persistent_continuation_planned",
                bar.ts_close_ns,
                plan_id=plan.plan_id,
                response_owner="FIRST_POST_TOUCH_INTERNAL_SHIFT",
                pivot_id=pivot.pivot_id,
                pivot_price=pivot.price,
                mechanism=mechanism,
                rule_provenance=(
                    POST_TOUCH_INTERNAL_SHIFT_RULE,
                    FIRST_INTERNAL_SHIFT_CROSS_RULE,
                ),
            )
            output.append(plan)
        return output

    def on_bar(
        self,
        timeframe_minutes: int,
        bar: Candle,
    ) -> list[V5TradePlan]:
        if timeframe_minutes == self.trigger_minutes:
            self.response_structure.on_bar(bar)
        return super().on_bar(timeframe_minutes, bar)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["post_touch_internal_structure_shift"] = {
            "counts": dict(sorted(self._cisd_counts.items())),
            "pending": len(self._pending_internal_shifts),
            "structure": dict(self.response_structure.diagnostics),
            "rules": (
                PERSISTENT_FIRST_RETURN_RULE,
                COMPLETE_MICRO_FIRST_OBSTACLE_RULE,
                POST_TOUCH_INTERNAL_SHIFT_RULE,
                FIRST_INTERNAL_SHIFT_CROSS_RULE,
            ),
        }
        return output
