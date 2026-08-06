"""Causal first-retrace confirmation for candidate 10 v2.3."""

from __future__ import annotations

from dataclasses import dataclass, replace

from c10_micro_state import AuctionStateMachine as MicroStructureStateMachine
from c10_model import BarView
from c10_model import TradePlan
from c10_model import Transition


@dataclass(slots=True)
class RetestPending:
    plan: TradePlan
    armed_index: int
    armed_ns: int
    zone_low: float
    zone_high: float
    state: str = "RETRACE_WAIT"
    touched: bool = False
    touch_index: int | None = None
    touch_ns: int | None = None
    touch_extreme: float | None = None


class AuctionStateMachine(MicroStructureStateMachine):
    """Require an observable rejection after the first corridor retrace.

    A displacement corridor identifies a location, not an order. The full
    candidate waits for price to touch that corridor and then close through the
    preceding completed minute's opposing extreme. Only then is a passive entry
    armed inside the corridor. The ablation preserves the old immediate 61.8%
    parent exactly.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.retest_pending: RetestPending | None = None

    def _process_rejection(
        self,
        bar: BarView,
        atr: float,
    ) -> tuple[list[Transition], TradePlan | None]:
        events, plan = super()._process_rejection(bar, atr)
        if plan is None or not self.params.enable_retrace_confirmation:
            return events, plan

        zone_low = float(plan.details["zone_low"])
        zone_high = float(plan.details["zone_high"])
        self.retest_pending = RetestPending(
            plan=plan,
            armed_index=self.bar_index,
            armed_ns=bar.ts_ns,
            zone_low=min(zone_low, zone_high),
            zone_high=max(zone_low, zone_high),
        )

        rewritten: list[Transition] = []
        for event in events:
            if event.event_type == "ENTRY_READY" and event.scenario_id == plan.scenario_id:
                rewritten.append(
                    replace(
                        event,
                        event_type="RETRACE_ARMED",
                        next_state="RETRACE_WAIT",
                        reason_code="FIRST_DISPLACEMENT_RETRACE_REQUIRED",
                        details={
                            **event.details,
                            "zone_low": self.retest_pending.zone_low,
                            "zone_high": self.retest_pending.zone_high,
                            "original_resting_entry": plan.entry_estimate,
                            "confirmation_rule": (
                                "touch corridor then close through preceding minute opposing extreme"
                            ),
                        },
                    ),
                )
            else:
                rewritten.append(event)
        return rewritten, None

    def _retest_event(
        self,
        pending: RetestPending,
        bar: BarView,
        *,
        event_type: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, object] | None = None,
    ) -> Transition:
        event = self._transition(
            scenario_id=pending.plan.scenario_id,
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            event_type=event_type,
            previous_state=pending.state,
            next_state=next_state,
            reason_code=reason_code,
            reference_price=reference_price,
            details=details,
        )
        pending.state = next_state
        return event

    def _process_retest(
        self,
        bar: BarView,
    ) -> tuple[list[Transition], TradePlan | None]:
        pending = self.retest_pending
        if pending is None:
            return [], None
        plan = pending.plan
        events: list[Transition] = []
        age = self.bar_index - pending.armed_index

        invalidated = (
            bar.close <= plan.invalidation_price
            if plan.direction > 0
            else bar.close >= plan.invalidation_price
        )
        if invalidated:
            events.append(
                self._retest_event(
                    pending,
                    bar,
                    event_type="SCENARIO_INVALIDATED",
                    next_state="INVALIDATED",
                    reason_code="STRUCTURE_INVALIDATED_BEFORE_RETRACE_CONFIRMATION",
                    reference_price=bar.close,
                ),
            )
            self.retest_pending = None
            return events, None

        if age > plan.entry_expiry_bars:
            events.append(
                self._retest_event(
                    pending,
                    bar,
                    event_type="SCENARIO_EXPIRED",
                    next_state="EXPIRED",
                    reason_code="NO_CONFIRMED_FIRST_RETRACE",
                    reference_price=bar.close,
                    details={"age_bars": age},
                ),
            )
            self.retest_pending = None
            return events, None

        history = list(self.history)
        if len(history) < 2:
            return events, None
        prior = history[-2]
        intersects = bar.high >= pending.zone_low and bar.low <= pending.zone_high
        if intersects and not pending.touched:
            pending.touched = True
            pending.touch_index = self.bar_index
            pending.touch_ns = bar.ts_ns
            pending.touch_extreme = bar.low if plan.direction > 0 else bar.high
            events.append(
                self._retest_event(
                    pending,
                    bar,
                    event_type="RETRACE_TOUCHED",
                    next_state="RETRACE_TOUCHED",
                    reason_code="FIRST_DISPLACEMENT_CORRIDOR_TOUCH",
                    reference_price=bar.close,
                    details={
                        "zone_low": pending.zone_low,
                        "zone_high": pending.zone_high,
                        "touch_extreme": pending.touch_extreme,
                        "age_bars": age,
                    },
                ),
            )
        elif pending.touched:
            if plan.direction > 0:
                pending.touch_extreme = min(
                    float(pending.touch_extreme),
                    bar.low,
                )
            else:
                pending.touch_extreme = max(
                    float(pending.touch_extreme),
                    bar.high,
                )

        if not pending.touched:
            return events, None

        if plan.direction > 0:
            confirmed = (
                bar.close > prior.high
                and bar.close > pending.zone_high
            )
        else:
            confirmed = (
                bar.close < prior.low
                and bar.close < pending.zone_low
            )
        if not confirmed:
            return events, None

        midpoint = (bar.open + bar.close) / 2.0
        entry = min(max(midpoint, pending.zone_low), pending.zone_high)
        net_rr = self._net_rr(
            direction=plan.direction,
            entry=entry,
            stop=plan.stop_price,
            target=plan.target_price,
        )
        if net_rr < self.params.min_net_rr:
            events.append(
                self._retest_event(
                    pending,
                    bar,
                    event_type="SCENARIO_INVALIDATED",
                    next_state="INVALIDATED",
                    reason_code="CONFIRMED_RETRACE_FAILS_NET_RR",
                    reference_price=entry,
                    details={
                        "cost_adjusted_net_rr": net_rr,
                        "minimum_net_rr": self.params.min_net_rr,
                    },
                ),
            )
            self.retest_pending = None
            return events, None

        events.append(
            self._retest_event(
                pending,
                bar,
                event_type="RETRACE_CONFIRMED",
                next_state="RETRACE_CONFIRMED",
                reason_code="CORRIDOR_REJECTION_BROKE_PRECEDING_MINUTE_EXTREME",
                reference_price=bar.close,
                details={
                    "prior_high": prior.high,
                    "prior_low": prior.low,
                    "touch_extreme": pending.touch_extreme,
                    "confirmation_close": bar.close,
                    "passive_entry": entry,
                    "cost_adjusted_net_rr": net_rr,
                    "age_bars": age,
                },
            ),
        )
        events.append(
            self._retest_event(
                pending,
                bar,
                event_type="ENTRY_READY",
                next_state="ENTRY_READY",
                reason_code="CONFIRMED_MICRO_REJECTION_RETRACE_ENTRY",
                reference_price=entry,
                details={
                    "target": plan.target_price,
                    "stop": plan.stop_price,
                    "cost_adjusted_net_rr": net_rr,
                    "remaining_expiry_bars": max(1, plan.entry_expiry_bars - age),
                },
            ),
        )
        confirmed_plan = replace(
            plan,
            observed_ns=bar.ts_ns,
            entry_estimate=entry,
            entry_expiry_bars=max(1, plan.entry_expiry_bars - age),
            details={
                **plan.details,
                "original_resting_entry": plan.entry_estimate,
                "retrace_touch_ns": pending.touch_ns,
                "retrace_touch_extreme": pending.touch_extreme,
                "retrace_confirmation_ns": bar.ts_ns,
                "retrace_confirmation_close": bar.close,
                "retrace_prior_high": prior.high,
                "retrace_prior_low": prior.low,
                "cost_adjusted_net_rr_after_confirmation": net_rr,
            },
        )
        self.retest_pending = None
        return events, confirmed_plan

    def on_bar(
        self,
        bar: BarView,
        *,
        allow_new_setup: bool = True,
    ) -> tuple[list[Transition], TradePlan | None]:
        pending_before_bar = self.retest_pending is not None
        events, plan = super().on_bar(
            bar,
            allow_new_setup=allow_new_setup and not pending_before_bar,
        )
        if pending_before_bar:
            retest_events, retest_plan = self._process_retest(bar)
            events.extend(retest_events)
            return events, retest_plan
        return events, plan
