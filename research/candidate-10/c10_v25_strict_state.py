"""Strict event-identity and ablation contract for candidate-10 v25.

This layer changes no thresholds. It ensures that:

1. quote OFI/replenishment affects confirmation only, never acceptance/expiry;
2. every true cross consumes a shelf even while another scenario or cooldown is
   active; and
3. a target touched before confirmation cannot later be recycled.
"""
from __future__ import annotations

from c10_v25_model import (
    LiquidityResponseBar,
    LiquidityResponsePlan,
    LiquidityResponseTransition,
)
from c10_v25_state import LiquidityResponseStateMachine


class StrictLiquidityResponseStateMachine(LiquidityResponseStateMachine):
    """Preserve one-event-one-scenario identity under all lifecycle states."""

    def _consume_background_crosses(
        self,
        bar: LiquidityResponseBar,
    ) -> list[LiquidityResponseTransition]:
        if not self.recent_bars or not (
            self.active_probe is not None or self.cooldown_active
        ):
            return []
        crossed = self._price_crossed_shelves(
            bar,
            previous_mid=self.recent_bars[-1].mid_close,
        )
        if not crossed:
            return []
        self._consume_shelves(crossed)
        count = len(crossed)
        if self.active_probe is not None:
            scenario_id = self.active_probe.scenario_id
            previous_state = "FAILED_AUCTION_WAIT"
            next_state = "FAILED_AUCTION_WAIT"
            reason = "TRUE_CROSS_CONSUMED_WHILE_SOURCE_EVENT_ACTIVE"
            self.counters["TRUE_CROSS_CONSUMED_DURING_ACTIVE_PROBE"] += count
        else:
            scenario_id = f"{self.instrument_id}:COOLDOWN:{bar.ts_ns}"
            previous_state = "EVENT_COOLDOWN"
            next_state = "EVENT_COOLDOWN"
            reason = "TRUE_CROSS_CONSUMED_DURING_EVENT_COOLDOWN"
            self.counters["TRUE_CROSS_CONSUMED_DURING_COOLDOWN"] += count
        return [
            self._transition(
                scenario_id=scenario_id,
                bar=bar,
                event_type="NONTRADABLE_TRUE_CROSS_CONSUMED",
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason,
                reference_price=bar.mid_close,
                details={
                    "source_ids": sorted(shelf.shelf_id for shelf in crossed),
                    "sides": sorted({shelf.side for shelf in crossed}),
                },
            ),
        ]

    def _consume_target_by_id(self, target_id: str) -> bool:
        """Consume an originally active target even if this bar did so earlier."""
        for shelf in self.shelves:
            if shelf.shelf_id == target_id:
                shelf.active = False
                shelf.reserved = False
                return True
        return False

    def _process_probe(
        self,
        bar: LiquidityResponseBar,
        features: dict[str, float],
    ) -> tuple[list[LiquidityResponseTransition], LiquidityResponsePlan | None]:
        """Make accepted-auction invalidation independent of quote features."""
        events, plan = super()._process_probe(bar, features)
        if events or plan is not None or self.active_probe is None:
            return events, plan

        probe = self.active_probe
        age = self.sequence - probe.initiated_sequence
        outside = (
            bar.mid_close >= probe.source_price + probe.source_zone
            if probe.move_direction > 0
            else bar.mid_close <= probe.source_price - probe.source_zone
        )
        same_flow = (
            probe.move_direction * bar.signed_trade_quote
            >= features["confirmation_flow_floor"]
        )
        if age < 3 or not outside or not same_flow:
            return events, plan

        event = self._transition(
            scenario_id=probe.scenario_id,
            bar=bar,
            event_type="SCENARIO_INVALIDATED",
            previous_state="FAILED_AUCTION_WAIT",
            next_state="ACCEPTED_AUCTION",
            reason_code="SHELF_ACCEPTED_WITH_PERSISTENT_SAME_SIDE_FLOW",
            reference_price=bar.mid_close,
            details={
                "age_bars": age,
                "quote_independent_acceptance": True,
            },
        )
        self.counters["ACCEPTED_AUCTION_NO_REVERSAL"] += 1
        self._release_probe(probe)
        return [event], None

    def on_bar(
        self,
        bar: LiquidityResponseBar,
    ) -> tuple[list[LiquidityResponseTransition], LiquidityResponsePlan | None]:
        background = self._consume_background_crosses(bar)
        target_id = (
            self.active_probe.target_id
            if self.active_probe is not None
            else None
        )
        events, plan = super().on_bar(bar)
        if target_id and any(
            event.reason_code == "PREEXISTING_TARGET_REACHED_BEFORE_CONFIRMATION"
            for event in events
        ):
            if self._consume_target_by_id(target_id):
                self.counters["TARGET_SHELF_CONSUMED_BEFORE_ENTRY"] += 1
        return [*background, *events], plan


__all__ = ["StrictLiquidityResponseStateMachine"]
