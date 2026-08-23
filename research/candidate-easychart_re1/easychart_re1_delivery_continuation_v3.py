"""First following completed minute owns rebalance control transfer.

After a five-minute footprint's first return, waiting through unrelated chop
until some eventual one-minute close breaks the touch extreme is outcome-selected
entry.  The causal response is the first strictly later completed minute.  It
either demonstrates active control transfer or the pullback episode ends with no
trade.

This changes no magnitude threshold or clock window: one market event follows
another.  Source invalidation and objective consumption are still checked before
the response decision, and all geometry remains fixed before submission.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import SetupState, V5TradePlan
from domain import Candle
from easychart_re1_delivery_continuation_v2 import (
    COMPLETE_MICRO_FIRST_OBSTACLE_RULE,
    DeliveryContinuationEngineV2,
)
from easychart_re1_persistent_confirmed import (
    PERSISTENT_CONFIRMED_RESPONSE_RULE,
)


FIRST_FOLLOWING_CONTINUATION_RESPONSE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "THE_FIRST_STRICTLY_LATER_COMPLETED_ONE_MINUTE_BAR_AFTER_THE_FIRST_FOOTPRINT_TOUCH_OWNS_THE_CONTINUATION_CONTROL_TRANSFER_DECISION"
)
if FIRST_FOLLOWING_CONTINUATION_RESPONSE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (
        FIRST_FOLLOWING_CONTINUATION_RESPONSE_RULE,
    )


class DeliveryContinuationEngineV3(DeliveryContinuationEngineV2):
    """Complete first-obstacle geometry plus a non-selective first response."""

    def _advance_setups(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        observation = self._current_flow
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
                setup.state = SetupState.WAITING_ACCEPTANCE_RESPONSE
                self._inc("persistent_continuation_first_touch_armed")
                self._trace(
                    "persistent_continuation_first_touch_armed",
                    bar.ts_close_ns,
                    setup_id=setup.setup_id,
                    side=setup.side.name,
                    touch_high=bar.high,
                    touch_low=bar.low,
                    touch_close=bar.close,
                    rule_provenance=(
                        PERSISTENT_CONFIRMED_RESPONSE_RULE,
                        FIRST_FOLLOWING_CONTINUATION_RESPONSE_RULE,
                    ),
                )
                continue

            if bar.ts_close_ns <= setup.first_touch_time_ns:
                continue
            mechanism = self._response_mechanism(
                setup,
                bar,
                observation,
            )
            if mechanism is None:
                self._finish(
                    setup,
                    "persistent_first_following_minute_failed_control_transfer",
                    bar.ts_close_ns,
                    response_open=bar.open,
                    response_high=bar.high,
                    response_low=bar.low,
                    response_close=bar.close,
                    touch_high=setup.touch_high,
                    touch_low=setup.touch_low,
                    flow_active=None
                    if observation is None
                    else observation.active,
                    flow_directed=None
                    if observation is None
                    else observation.directed,
                    rule_provenance=FIRST_FOLLOWING_CONTINUATION_RESPONSE_RULE,
                )
                continue
            plan = self._make_plan(setup, bar, mechanism)
            if plan is None:
                self._finish(
                    setup,
                    "persistent_continuation_no_trade_geometry",
                    bar.ts_close_ns,
                )
                continue
            self._finish(
                setup,
                "persistent_continuation_planned",
                bar.ts_close_ns,
                plan_id=plan.plan_id,
                response_mechanism=mechanism,
                rule_provenance=FIRST_FOLLOWING_CONTINUATION_RESPONSE_RULE,
            )
            output.append(plan)
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["first_following_continuation_response"] = {
            "owner": "FIRST_STRICTLY_LATER_COMPLETED_ONE_MINUTE_BAR",
            "failure": "END_EPISODE_NO_TRADE",
            "rules": (
                PERSISTENT_CONFIRMED_RESPONSE_RULE,
                COMPLETE_MICRO_FIRST_OBSTACLE_RULE,
                FIRST_FOLLOWING_CONTINUATION_RESPONSE_RULE,
            ),
        }
        return output
