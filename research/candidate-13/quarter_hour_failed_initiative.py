#!/usr/bin/env python3
"""Market-wide failed quarter-hour initiative reversal for Candidate 13 V11.

This module reuses the V10 causal common-flow detector but not its continuation
state or entries. Two same-direction common-flow events establish an attempted
initiative. A majority close back through the second event origins confirms a
failed auction. Only then can the same participating markets propose a passive
retest of the failed origin, with the second event extreme as invalidation and
the first event origin as the objective.

No fills, fees, margin, positions, cash or NAV are simulated here.
NautilusTrader remains the sole execution and accounting engine.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

from logic import BarObs, Direction, LogicConfig, MINUTE_NS, ResearchEvent, Scenario, TradePlan
from quarter_hour_persistent_initiative import (
    CommonFlowEvent,
    FOUR_HOURS_NS,
    PersistentQuarterHourRouter,
    QuarterHourImpulse,
    SYMBOLS,
)

QHF_LOGIC_KEY = "PORTFOLIO::QUARTER_HOUR_FAILED_INITIATIVE"
QHF_MODULE = "QUARTER_HOUR_FAILED_INITIATIVE_REVERSAL"


@dataclass(frozen=True, slots=True)
class EventSnapshot:
    event: CommonFlowEvent
    impulses: Mapping[str, QuarterHourImpulse]


@dataclass(frozen=True, slots=True)
class AttemptedInitiative:
    scenario_id: str
    first: EventSnapshot
    second: EventSnapshot
    activated_ts_ns: int
    expires_ts_ns: int

    @property
    def direction(self) -> Direction:
        return self.second.event.direction


class QuarterHourFailedInitiativeEngine(PersistentQuarterHourRouter):
    """Detect two-event common-flow failure and emit reversal plans."""

    def __init__(
        self,
        config: LogicConfig,
        instrument_id: str = "PORTFOLIO.GLOBAL",
    ) -> None:
        super().__init__(config, instrument_id)
        self.skips = Counter()
        self._first: EventSnapshot | None = None
        self._attempt: AttemptedInitiative | None = None
        self._attempt_sequence = 0
        self._plan_sequence = 0
        self._states: dict[str, str] = {}
        self._active_scenario_id: str | None = None

    @staticmethod
    def _reverse(direction: Direction) -> Direction:
        return Direction.SHORT if direction is Direction.LONG else Direction.LONG

    def _snapshot(self, event: CommonFlowEvent) -> EventSnapshot | None:
        impulses: dict[str, QuarterHourImpulse] = {}
        for symbol in event.accepted_symbols:
            impulse = self._impulse(symbol, event.observed_ts_ns)
            if impulse is None:
                self.skips["QHF_EVENT_IMPULSE_SNAPSHOT_MISSING"] += 1
                return None
            impulses[symbol] = impulse
        return EventSnapshot(event=event, impulses=impulses)

    def _emit_attempt_event(
        self,
        *,
        event_type: str,
        reason: str,
        observed_ts_ns: int,
        previous_state: str,
        next_state: str,
        attempt: AttemptedInitiative,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._event(
            scenario_id=attempt.scenario_id,
            event_type=event_type,
            event_time_ns=attempt.first.event.observed_ts_ns,
            observed_time_ns=observed_ts_ns,
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=attempt.second.event.closes.get(
                attempt.second.event.owner_symbol,
            ),
            details={
                "direction": attempt.direction.value,
                "first_event_id": attempt.first.event.event_id,
                "second_event_id": attempt.second.event.event_id,
                "first_owner_symbol": attempt.first.event.owner_symbol,
                "second_owner_symbol": attempt.second.event.owner_symbol,
                "first_accepted_symbols": list(
                    attempt.first.event.accepted_symbols,
                ),
                "second_accepted_symbols": list(
                    attempt.second.event.accepted_symbols,
                ),
                "expires_ts_ns": attempt.expires_ts_ns,
                **(details or {}),
            },
        )

    def _start_attempt(
        self,
        first: EventSnapshot,
        second: EventSnapshot,
    ) -> None:
        self._attempt_sequence += 1
        attempt = AttemptedInitiative(
            scenario_id=(
                f"QHF-ATTEMPT-{second.event.observed_ts_ns}-"
                f"{self._attempt_sequence:06d}-{second.event.direction.value}"
            ),
            first=first,
            second=second,
            activated_ts_ns=second.event.observed_ts_ns,
            expires_ts_ns=second.event.observed_ts_ns + FOUR_HOURS_NS,
        )
        self._attempt = attempt
        self._emit_attempt_event(
            event_type="QHF_ATTEMPTED_INITIATIVE_ACTIVATED",
            reason="SECOND_DISTINCT_SAME_DIRECTION_COMMON_FLOW_EVENT",
            observed_ts_ns=second.event.observed_ts_ns,
            previous_state="IDLE",
            next_state="ATTEMPTING_DELIVERY",
            attempt=attempt,
        )

    def _refresh_attempt(self, second: EventSnapshot) -> None:
        attempt = self._attempt
        if attempt is None:
            return
        refreshed = AttemptedInitiative(
            scenario_id=attempt.scenario_id,
            first=attempt.first,
            second=second,
            activated_ts_ns=attempt.activated_ts_ns,
            expires_ts_ns=second.event.observed_ts_ns + FOUR_HOURS_NS,
        )
        self._attempt = refreshed
        self._emit_attempt_event(
            event_type="QHF_ATTEMPTED_INITIATIVE_EXTENDED",
            reason="FRESH_SAME_DIRECTION_COMMON_FLOW_EVENT",
            observed_ts_ns=second.event.observed_ts_ns,
            previous_state="ATTEMPTING_DELIVERY",
            next_state="ATTEMPTING_DELIVERY",
            attempt=refreshed,
        )

    def _terminate_attempt(
        self,
        ts_ns: int,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        attempt = self._attempt
        if attempt is None:
            return
        self._emit_attempt_event(
            event_type="QHF_ATTEMPTED_INITIATIVE_TERMINATED",
            reason=reason,
            observed_ts_ns=ts_ns,
            previous_state="ATTEMPTING_DELIVERY",
            next_state="TERMINAL",
            attempt=attempt,
            details=details,
        )
        self._attempt = None

    def _handle_common_event(self, event: CommonFlowEvent) -> None:
        snapshot = self._snapshot(event)
        if snapshot is None:
            return
        attempt = self._attempt
        if attempt is not None:
            if event.direction is attempt.direction:
                self._refresh_attempt(snapshot)
                return
            self._terminate_attempt(
                event.observed_ts_ns,
                "OPPOSITE_COMMON_FLOW_EVENT_BEFORE_REACCEPTANCE",
                {"opposite_event_id": event.event_id},
            )
            self._first = snapshot
            return

        first = self._first
        if (
            first is None
            or event.direction is not first.event.direction
            or event.observed_ts_ns - first.event.observed_ts_ns > FOUR_HOURS_NS
        ):
            self._first = snapshot
            return
        if event.event_id == first.event.event_id:
            return
        self._start_attempt(first, snapshot)

    def _costed_failure_plan(
        self,
        *,
        attempt: AttemptedInitiative,
        symbol: str,
        observed_ts_ns: int,
        current: BarObs,
        reaccepted_symbols: tuple[str, ...],
    ) -> TradePlan | None:
        first = attempt.first.impulses.get(symbol)
        second = attempt.second.impulses.get(symbol)
        if first is None or second is None:
            self.skips["QHF_SYMBOL_NOT_IN_BOTH_EVENTS"] += 1
            return None
        failed_direction = attempt.direction
        direction = self._reverse(failed_direction)
        entry = second.open
        target = first.open
        allowance = self.config.stop_buffer_atr * second.atr
        if direction is Direction.SHORT:
            stop = second.high + allowance
            causal_prices = target < current.close < entry < stop
        else:
            stop = second.low - allowance
            causal_prices = stop < entry < current.close < target
        if not causal_prices:
            self.skips["QHF_TARGET_CONSUMED_OR_NON_CAUSAL_PRICE_ORDER"] += 1
            return None

        risk = abs(stop - entry)
        gross_gain = abs(entry - target)
        if risk <= 0.0 or gross_gain <= 0.0:
            self.skips["QHF_NON_POSITIVE_STRUCTURAL_GEOMETRY"] += 1
            return None
        if risk / second.atr < self.config.min_stop_atr:
            self.skips["QHF_STOP_DISTANCE_BELOW_EXECUTION_FLOOR"] += 1
            return None
        loss = (
            risk
            + entry * self.config.effective_maker_rate
            + stop * self.config.effective_taker_rate
        )
        net_gain = (
            gross_gain
            - entry * self.config.effective_maker_rate
            - target * self.config.effective_maker_rate
        )
        net_r = net_gain / loss if loss > 0.0 else float("-inf")
        if not isfinite(net_r) or net_gain <= 0.0 or net_r < self.config.min_net_r:
            self.skips["QHF_INSUFFICIENT_COSTED_TWO_EVENT_R"] += 1
            return None

        self._plan_sequence += 1
        scenario_id = (
            f"QHF-PLAN-{symbol}-{observed_ts_ns}-"
            f"{self._plan_sequence:06d}-{direction.value}"
        )
        expire_ts_ns = (
            observed_ts_ns
            + self.config.retrace_expiry_bars
            * self.config.internal_tf_bars
            * MINUTE_NS
        )
        details = {
            "_logic_key": QHF_LOGIC_KEY,
            "module": QHF_MODULE,
            "route": "TWO_EVENT_COMMON_FLOW_FAILED_AUCTION_REVERSAL",
            "attempt_id": attempt.scenario_id,
            "failed_direction": failed_direction.value,
            "reversal_direction": direction.value,
            "first_event_id": attempt.first.event.event_id,
            "second_event_id": attempt.second.event.event_id,
            "first_event_origin": first.open,
            "second_event_origin": second.open,
            "second_event_high": second.high,
            "second_event_low": second.low,
            "second_event_atr": second.atr,
            "second_event_standardized_body": second.standardized_body,
            "second_event_signed_flow": second.signed_flow,
            "first_owner_symbol": attempt.first.event.owner_symbol,
            "second_owner_symbol": attempt.second.event.owner_symbol,
            "accepted_in_both_events": sorted(
                set(attempt.first.event.accepted_symbols)
                & set(attempt.second.event.accepted_symbols)
            ),
            "majority_reaccepted_symbols": list(reaccepted_symbols),
            "entry_model": "SECOND_EVENT_ORIGIN_PASSIVE_RETEST",
            "stop_model": "SECOND_EVENT_EXTREME_INVALIDATION",
            "target_model": "FIRST_EVENT_ORIGIN_FULL_CAMPAIGN_RETRACE",
            "entry_cost_assumption": "MAKER",
            "stop_cost_assumption": "TAKER",
            "target_cost_assumption": "MAKER",
            "independent_episode_key": f"{attempt.scenario_id}:{symbol}",
        }
        plan = TradePlan(
            scenario_id=scenario_id,
            scenario=Scenario.FAR,
            direction=direction,
            observed_ts_ns=observed_ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            atr=second.atr,
            loss_per_unit=loss,
            gain_per_unit=net_gain,
            net_r=net_r,
            reason_code="MARKETWIDE_SECOND_IMPULSE_ORIGIN_REACCEPTED",
            expire_ts_ns=expire_ts_ns,
            entry_order_type="LIMIT",
            entry_post_only=True,
            details=details,
        )
        self._states[scenario_id] = "PENDING_ENTRY"
        self._event(
            scenario_id=scenario_id,
            event_type="QHF_REVERSAL_PLAN_CONFIRMED",
            event_time_ns=attempt.second.event.observed_ts_ns,
            observed_time_ns=observed_ts_ns,
            previous_state="IDLE",
            next_state="PENDING_ENTRY",
            reason_code=plan.reason_code,
            reference_price=entry,
            details={
                "entry": entry,
                "stop": stop,
                "target": target,
                "net_r": net_r,
                **details,
            },
        )
        return plan

    def _failure_plans(
        self,
        ts_ns: int,
        bars: Mapping[str, BarObs],
    ) -> list[tuple[str, TradePlan]]:
        attempt = self._attempt
        if attempt is None:
            return []
        if ts_ns >= attempt.expires_ts_ns:
            self._terminate_attempt(ts_ns, "FOUR_HOUR_ATTEMPT_EXPIRED")
            self._first = None
            return []
        if not self._is_five_minute_boundary(ts_ns):
            return []

        reaccepted: list[str] = []
        for symbol in attempt.second.event.accepted_symbols:
            observation = bars.get(symbol)
            origin = attempt.second.event.origins.get(symbol)
            if observation is None or origin is None:
                continue
            crossed = (
                observation.close <= origin
                if attempt.direction is Direction.LONG
                else observation.close >= origin
            )
            if crossed:
                reaccepted.append(symbol)
        majority = len(attempt.second.event.accepted_symbols) // 2 + 1
        if len(reaccepted) < majority:
            return []

        common = (
            set(attempt.first.event.accepted_symbols)
            & set(attempt.second.event.accepted_symbols)
            & set(reaccepted)
        )
        plans: list[tuple[str, TradePlan]] = []
        for symbol in sorted(common):
            observation = bars.get(symbol)
            if observation is None:
                continue
            plan = self._costed_failure_plan(
                attempt=attempt,
                symbol=symbol,
                observed_ts_ns=ts_ns,
                current=observation,
                reaccepted_symbols=tuple(sorted(reaccepted)),
            )
            if plan is not None:
                plans.append((symbol, plan))

        self._emit_attempt_event(
            event_type="QHF_MARKETWIDE_FAILURE_CONFIRMED",
            reason="MAJORITY_SECOND_EVENT_ORIGINS_REACCEPTED",
            observed_ts_ns=ts_ns,
            previous_state="ATTEMPTING_DELIVERY",
            next_state="FAILED_AUCTION",
            attempt=attempt,
            details={
                "reaccepted_symbols": sorted(reaccepted),
                "required_majority": majority,
                "executable_plan_symbols": [symbol for symbol, _ in plans],
            },
        )
        self._attempt = None
        self._first = None
        if not plans:
            self.skips["QHF_FAILURE_WITHOUT_COSTED_EXECUTABLE_PLAN"] += 1
        return plans

    def on_batch(
        self,
        ts_ns: int,
        bars: Mapping[str, BarObs],
    ) -> list[tuple[str, TradePlan]]:
        for symbol in SYMBOLS:
            observation = bars.get(symbol)
            if observation is None:
                self.skips["QHF_SYNCHRONIZED_SYMBOL_MISSING"] += 1
                return []
            self._bars[symbol].append(observation)

        if self._first is not None and (
            ts_ns - self._first.event.observed_ts_ns > FOUR_HOURS_NS
        ):
            self._first = None

        plans = self._failure_plans(ts_ns, bars)
        if plans:
            return plans

        event = self._detect_event(ts_ns)
        if event is not None:
            self._handle_common_event(event)
        return []

    @staticmethod
    def _ts(value: Any) -> int:
        return int(getattr(value, "ts_ns", value))

    def _transition(
        self,
        plan: TradePlan,
        *,
        ts_ns: int,
        next_state: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        previous = self._states.get(plan.scenario_id)
        if previous is None or previous == "TERMINAL":
            return
        self._event(
            scenario_id=plan.scenario_id,
            event_type="QHF_PLAN_LIFECYCLE",
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=plan.expected_entry,
            details=details or {},
        )
        self._states[plan.scenario_id] = next_state

    def mark_rejected(
        self,
        plan: TradePlan,
        ts_or_bar: Any,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._transition(
            plan,
            ts_ns=self._ts(ts_or_bar),
            next_state="TERMINAL",
            reason=reason,
            details=details,
        )

    def mark_submitted(
        self,
        plan: TradePlan,
        quantity: Any,
        details: dict[str, Any],
    ) -> None:
        payload = dict(details)
        payload.update({"quantity": str(quantity), "module": QHF_MODULE})
        self._transition(
            plan,
            ts_ns=plan.observed_ts_ns,
            next_state="SUBMITTED",
            reason="NAUTILUS_BRACKET_SUBMITTED",
            details=payload,
        )
        self._active_scenario_id = plan.scenario_id

    def mark_entry_filled(self, ts_ns: int, details: dict[str, Any]) -> None:
        scenario_id = str(details.get("scenario_id", self._active_scenario_id or ""))
        if self._states.get(scenario_id) != "SUBMITTED":
            return
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type="QHF_ENTRY_FILLED",
                event_time_ns=int(ts_ns),
                observed_time_ns=int(ts_ns),
                previous_state="SUBMITTED",
                next_state="POSITION_OPEN",
                reason_code="NAUTILUS_PARENT_FILLED",
                reference_price=None,
                details=dict(details),
            ),
        )
        self._states[scenario_id] = "POSITION_OPEN"
        self._active_scenario_id = scenario_id

    def mark_trade_terminal(self, ts_ns: int, reason: str) -> None:
        scenario_id = self._active_scenario_id
        if not scenario_id:
            return
        previous = self._states.get(scenario_id)
        if previous not in {"SUBMITTED", "POSITION_OPEN"}:
            return
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type="QHF_TRADE_TERMINAL",
                event_time_ns=int(ts_ns),
                observed_time_ns=int(ts_ns),
                previous_state=previous,
                next_state="TERMINAL",
                reason_code=reason,
                reference_price=None,
                details={"module": QHF_MODULE},
            ),
        )
        self._states[scenario_id] = "TERMINAL"
        self._active_scenario_id = None
