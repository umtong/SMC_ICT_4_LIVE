"""Market-owned initiative state and independent MSS/FVG continuation plans.

This module is strategy logic only. It never simulates orders, fills, fees,
margin, positions, cash, or NAV. NautilusTrader remains the sole execution and
accounting engine.

The state transition is deliberately hierarchical:

1. A completed SCDAM FAR/AAC scenario must be admitted by the V6 leadership
   gate, which requires the candidate to own the cross-market event rather than
   borrow a synchronized peer move.
2. That event transfers global initiative in its confirmed direction. The state
   remains valid until the source market reaccepts the swept boundary, the
   declared external target is delivered, or an opposite owned event replaces
   it.
3. While initiative is valid, each market may emit a new continuation only from
   a fresh completed five-minute displacement which breaks a protected swing
   and leaves a strict three-candle fair-value gap. The first retrace is a
   passive consequent-encroachment limit; the last opposing candle is the
   structural invalidation and the next live 4H/daily pool is the target.

No elapsed-time cooldown, PnL condition, loss count, model score, notional cap,
or fitted direction multiplier is used. Multiple entries can occur in one
initiative leg only through distinct displacement identities, while the shared
portfolio still enforces one pending entry or open position globally.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from math import isfinite
from statistics import fmean
from typing import Any, Mapping

from logic import (
    BarObs,
    Direction,
    LogicConfig,
    MINUTE_NS,
    ResearchEvent,
    Scenario,
    Side,
    StructuralBar,
    TradePlan,
    _TimeAggregator,
)

GLOBAL_INITIATIVE_KEY = "PORTFOLIO::GLOBAL_INITIATIVE"
CONTINUATION_MODULE = "GLOBAL_INITIATIVE_CONTINUATION"


@dataclass(frozen=True, slots=True)
class InitiativeState:
    scenario_id: str
    source_plan_id: str
    source_symbol: str
    direction: Direction
    source_level: float
    target_level: float
    activated_ts_ns: int
    source_scenario: str
    leadership: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.scenario_id or not self.source_plan_id or not self.source_symbol:
            raise ValueError("initiative identity must not be empty")
        if self.activated_ts_ns < 0:
            raise ValueError("activated timestamp must be non-negative")
        if not all(isfinite(value) and value > 0.0 for value in (self.source_level, self.target_level)):
            raise ValueError("initiative prices must be finite and positive")
        if self.direction is Direction.LONG:
            if self.target_level <= self.source_level:
                raise ValueError("long initiative target must exceed source")
        elif self.target_level >= self.source_level:
            raise ValueError("short initiative target must be below source")

    def source_reaccepted(self, close: float) -> bool:
        if self.direction is Direction.LONG:
            return close <= self.source_level
        return close >= self.source_level

    def target_delivered(self, high: float, low: float) -> bool:
        if self.direction is Direction.LONG:
            return high >= self.target_level
        return low <= self.target_level


class GlobalInitiativeRouter:
    """Own exactly one marketwide initiative state from observable events."""

    def __init__(self, instrument_id: str = "PORTFOLIO.GLOBAL") -> None:
        self.instrument_id = instrument_id
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        self._state: InitiativeState | None = None
        self._sequence = 0

    @property
    def state(self) -> InitiativeState | None:
        return self._state

    def _event(
        self,
        *,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None,
        details: dict[str, Any],
    ) -> None:
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type=event_type,
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=(
                    None if reference_price is None else format(reference_price, ".10f")
                ),
                details=details,
            ),
        )

    @staticmethod
    def _boundary(plan: TradePlan) -> float | None:
        for key in ("pool_level", "source_boundary"):
            value = plan.details.get(key)
            if value is not None:
                return float(value)
        return None

    def observe_batch(self, ts_ns: int, bars: Mapping[str, BarObs]) -> None:
        state = self._state
        if state is None:
            return
        source = bars.get(state.source_symbol)
        if source is None:
            self.skips["INITIATIVE_SOURCE_BAR_MISSING"] += 1
            return
        if state.source_reaccepted(source.close):
            self._event(
                scenario_id=state.scenario_id,
                event_type="GLOBAL_INITIATIVE_TERMINATED",
                event_time_ns=ts_ns,
                observed_time_ns=ts_ns,
                previous_state="ACTIVE",
                next_state="TERMINAL",
                reason_code="SOURCE_BOUNDARY_REACCEPTED",
                reference_price=state.source_level,
                details={
                    "source_plan_id": state.source_plan_id,
                    "source_symbol": state.source_symbol,
                    "direction": state.direction.value,
                    "source_close": source.close,
                    "source_level": state.source_level,
                },
            )
            self._state = None
            return
        if state.target_delivered(source.high, source.low):
            self._event(
                scenario_id=state.scenario_id,
                event_type="GLOBAL_INITIATIVE_TERMINATED",
                event_time_ns=ts_ns,
                observed_time_ns=ts_ns,
                previous_state="ACTIVE",
                next_state="TERMINAL",
                reason_code="DECLARED_EXTERNAL_TARGET_DELIVERED",
                reference_price=state.target_level,
                details={
                    "source_plan_id": state.source_plan_id,
                    "source_symbol": state.source_symbol,
                    "direction": state.direction.value,
                    "source_high": source.high,
                    "source_low": source.low,
                    "target_level": state.target_level,
                },
            )
            self._state = None

    def observe_owned_plan(
        self,
        *,
        plan: TradePlan,
        symbol: str,
        leadership: Mapping[str, Any],
        observed_ts_ns: int,
    ) -> InitiativeState | None:
        """Activate initiative from a V6-approved, event-owning core plan."""
        boundary = self._boundary(plan)
        if boundary is None:
            self.skips["OWNED_PLAN_WITHOUT_SOURCE_BOUNDARY"] += 1
            return None
        target = float(plan.target_price)
        if plan.direction is Direction.LONG:
            causal_order = boundary < plan.expected_entry < target
        else:
            causal_order = target < plan.expected_entry < boundary
        if not causal_order:
            self.skips["OWNED_PLAN_NON_CAUSAL_STATE_GEOMETRY"] += 1
            return None

        current = self._state
        if current is not None and current.source_plan_id == plan.scenario_id:
            return current
        if current is not None:
            self._event(
                scenario_id=current.scenario_id,
                event_type="GLOBAL_INITIATIVE_TERMINATED",
                event_time_ns=observed_ts_ns,
                observed_time_ns=observed_ts_ns,
                previous_state="ACTIVE",
                next_state="TERMINAL",
                reason_code="OPPOSITE_OR_FRESH_OWNED_EVENT_TRANSFER",
                reference_price=boundary,
                details={
                    "old_source_plan_id": current.source_plan_id,
                    "old_source_symbol": current.source_symbol,
                    "old_direction": current.direction.value,
                    "new_source_plan_id": plan.scenario_id,
                    "new_source_symbol": symbol,
                    "new_direction": plan.direction.value,
                },
            )

        self._sequence += 1
        state_id = (
            f"GI-{observed_ts_ns}-{self._sequence:06d}-"
            f"{symbol}-{plan.direction.value}"
        )
        state = InitiativeState(
            scenario_id=state_id,
            source_plan_id=plan.scenario_id,
            source_symbol=symbol,
            direction=plan.direction,
            source_level=boundary,
            target_level=target,
            activated_ts_ns=observed_ts_ns,
            source_scenario=plan.scenario.value,
            leadership=dict(leadership),
        )
        self._state = state
        sweep_ts = int(plan.details.get("sweep_ts_ns", observed_ts_ns))
        self._event(
            scenario_id=state.scenario_id,
            event_type="GLOBAL_INITIATIVE_ACTIVATED",
            event_time_ns=sweep_ts,
            observed_time_ns=observed_ts_ns,
            previous_state="IDLE",
            next_state="ACTIVE",
            reason_code="EVENT_OWNER_CONFIRMS_MARKET_INITIATIVE",
            reference_price=boundary,
            details={
                "source_plan_id": plan.scenario_id,
                "source_symbol": symbol,
                "source_scenario": plan.scenario.value,
                "direction": plan.direction.value,
                "source_level": boundary,
                "target_level": target,
                "leadership": dict(leadership),
            },
        )
        return state


@dataclass(frozen=True, slots=True)
class _Pivot:
    known_ts_ns: int
    price: float


class InitiativeContinuationEngine:
    """Fresh five-minute MSS/FVG continuation entries inside global initiative."""

    def __init__(
        self,
        config: LogicConfig,
        instrument_id: str,
        *,
        symbol: str,
        logic_key: str,
    ) -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.symbol = symbol
        self.logic_key = logic_key
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        self._aggregate = _TimeAggregator(config.internal_tf_bars)
        self._bars: list[StructuralBar] = []
        self._ranges: deque[float] = deque(maxlen=max(12, config.atr_period))
        self._pivot_highs: list[_Pivot] = []
        self._pivot_lows: list[_Pivot] = []
        self._sequence = 0
        self._states: dict[str, str] = {}
        self._last_emitted_bar_ns = -1

    def _event(
        self,
        *,
        scenario_id: str,
        event_type: str,
        event_time_ns: int,
        observed_time_ns: int,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None,
        details: dict[str, Any],
    ) -> None:
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type=event_type,
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=(
                    None if reference_price is None else format(reference_price, ".10f")
                ),
                details=details,
            ),
        )

    @staticmethod
    def _true_range(bar: StructuralBar, previous_close: float | None) -> float:
        if previous_close is None:
            return bar.span
        return max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )

    def _confirm_pivot_after_detection(self, known_ts_ns: int) -> None:
        if len(self._bars) < 3:
            return
        left, center, right = self._bars[-3:]
        if center.high > left.high and center.high > right.high:
            self._pivot_highs.append(_Pivot(known_ts_ns, center.high))
        if center.low < left.low and center.low < right.low:
            self._pivot_lows.append(_Pivot(known_ts_ns, center.low))
        if len(self._pivot_highs) > 256:
            del self._pivot_highs[:-128]
        if len(self._pivot_lows) > 256:
            del self._pivot_lows[:-128]

    @staticmethod
    def _bar_body(bar: StructuralBar) -> float:
        return abs(bar.close - bar.open)

    @staticmethod
    def _close_location(bar: StructuralBar) -> float:
        return (bar.close - bar.low) / bar.span

    def _latest_protected(self, direction: Direction, before_ts_ns: int) -> float | None:
        points = self._pivot_highs if direction is Direction.LONG else self._pivot_lows
        for point in reversed(points):
            if point.known_ts_ns < before_ts_ns:
                return point.price
        return None

    def _last_opposing_bar(self, direction: Direction) -> StructuralBar | None:
        for bar in reversed(self._bars[-5:-1]):
            if direction is Direction.LONG and bar.close < bar.open:
                return bar
            if direction is Direction.SHORT and bar.close > bar.open:
                return bar
        return self._bars[-2] if len(self._bars) >= 2 else None

    def _target_pool(
        self,
        *,
        direction: Direction,
        entry: float,
        impulse_close: float,
        atr: float,
        observed_ts_ns: int,
        external_engine: Any,
    ) -> Any | None:
        candidates = []
        for pool in getattr(external_engine, "pools", ()):  # causal public state
            if getattr(pool, "consumed", True) or not getattr(pool, "external", False):
                continue
            if int(getattr(pool, "confirmed_ts_ns", observed_ts_ns + 1)) > observed_ts_ns:
                continue
            source = str(getattr(pool, "source", ""))
            if source == "ROUND_NUMBER" or "SHELF" in source:
                continue
            level = float(getattr(pool, "level"))
            side = getattr(pool, "side")
            if direction is Direction.LONG:
                if side is not Side.HIGH or level <= max(entry, impulse_close):
                    continue
            elif side is not Side.LOW or level >= min(entry, impulse_close):
                continue
            distance = abs(level - entry)
            strength = max(1, int(getattr(pool, "strength", 1)))
            hazard = strength / max(distance / max(atr, 1e-12), 0.20)
            candidates.append((hazard, strength, -distance, pool))
        return max(candidates, default=(0.0, 0, 0.0, None))[-1]

    def _build_plan(
        self,
        *,
        completed: StructuralBar,
        observed_ts_ns: int,
        state: InitiativeState,
        external_engine: Any,
    ) -> TradePlan | None:
        if len(self._bars) < 3 or len(self._ranges) < self._ranges.maxlen:
            self.skips["CONTINUATION_WARMUP"] += 1
            return None
        if completed.start_ts_ns <= state.activated_ts_ns:
            self.skips["CONTINUATION_BAR_NOT_AFTER_INITIATIVE"] += 1
            return None
        if completed.end_ts_ns == self._last_emitted_bar_ns:
            return None

        atr = max(fmean(self._ranges), completed.close * 1e-9)
        direction = state.direction
        protected = self._latest_protected(direction, completed.start_ts_ns)
        if protected is None:
            self.skips["CONTINUATION_NO_PROTECTED_SWING"] += 1
            return None

        body = self._bar_body(completed)
        close_location = self._close_location(completed)
        directional_body = (
            completed.close > completed.open
            if direction is Direction.LONG
            else completed.close < completed.open
        )
        directional_flow = (
            completed.signed_flow >= self.config.displacement_flow_min
            if direction is Direction.LONG
            else completed.signed_flow <= -self.config.displacement_flow_min
        )
        structural_break = (
            completed.close > protected
            if direction is Direction.LONG
            else completed.close < protected
        )
        close_extreme = (
            close_location >= self.config.acceptance_close_location
            if direction is Direction.LONG
            else close_location <= 1.0 - self.config.acceptance_close_location
        )
        if not (
            directional_body
            and directional_flow
            and structural_break
            and close_extreme
            and body / atr >= self.config.displacement_body_atr
        ):
            self.skips["CONTINUATION_MSS_DISPLACEMENT_INCOMPLETE"] += 1
            return None

        first = self._bars[-3]
        if direction is Direction.LONG:
            zone_low, zone_high = first.high, completed.low
            strict_fvg = zone_high > zone_low
        else:
            zone_low, zone_high = completed.high, first.low
            strict_fvg = zone_high > zone_low
        if not strict_fvg:
            self.skips["CONTINUATION_STRICT_FVG_ABSENT"] += 1
            return None

        opposing = self._last_opposing_bar(direction)
        if opposing is None:
            self.skips["CONTINUATION_ORDER_BLOCK_ABSENT"] += 1
            return None
        entry = (zone_low + zone_high) / 2.0
        allowance = self.config.stop_buffer_atr * atr
        if direction is Direction.LONG:
            stop = min(opposing.low, protected) - allowance
            causal_entry = stop < entry < completed.close
        else:
            stop = max(opposing.high, protected) + allowance
            causal_entry = completed.close < entry < stop
        if not causal_entry:
            self.skips["CONTINUATION_NON_CAUSAL_ENTRY_STOP"] += 1
            return None

        target_pool = self._target_pool(
            direction=direction,
            entry=entry,
            impulse_close=completed.close,
            atr=atr,
            observed_ts_ns=observed_ts_ns,
            external_engine=external_engine,
        )
        if target_pool is None:
            self.skips["CONTINUATION_EXTERNAL_TARGET_ABSENT"] += 1
            return None
        target = float(target_pool.level)
        if direction is Direction.LONG:
            risk = entry - stop
            gross_gain = target - entry
        else:
            risk = stop - entry
            gross_gain = entry - target
        if risk <= 0.0 or gross_gain <= 0.0 or risk / atr < self.config.min_stop_atr:
            self.skips["CONTINUATION_INVALID_STRUCTURAL_GEOMETRY"] += 1
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
        if net_gain <= 0.0 or net_r < self.config.min_net_r:
            self.skips["CONTINUATION_INSUFFICIENT_COSTED_STRUCTURAL_R"] += 1
            return None

        self._sequence += 1
        scenario_id = (
            f"GIC-{self.symbol}-{completed.end_ts_ns}-"
            f"{self._sequence:06d}-{direction.value}"
        )
        expire_ts_ns = (
            observed_ts_ns
            + self.config.retrace_expiry_bars
            * self.config.internal_tf_bars
            * MINUTE_NS
        )
        details = {
            "_logic_key": self.logic_key,
            "module": CONTINUATION_MODULE,
            "market_initiative_id": state.scenario_id,
            "market_initiative_source_plan_id": state.source_plan_id,
            "market_initiative_source_symbol": state.source_symbol,
            "market_initiative_direction": state.direction.value,
            "initiative_activated_ts_ns": state.activated_ts_ns,
            "protected_swing": protected,
            "mss_bar_start_ts_ns": completed.start_ts_ns,
            "mss_bar_end_ts_ns": completed.end_ts_ns,
            "mss_body_atr": body / atr,
            "mss_signed_flow": completed.signed_flow,
            "fvg_low": zone_low,
            "fvg_high": zone_high,
            "entry_model": "FIRST_FVG_CONSEQUENT_ENCROACHMENT",
            "stop_model": "LAST_OPPOSING_BAR_OR_PROTECTED_SWING",
            "target_model": "NEXT_LIVE_EXTERNAL_4H_OR_DAILY_POOL",
            "target_pool_id": str(target_pool.scenario_id),
            "target_pool_source": str(target_pool.source),
            "entry_cost_assumption": "MAKER",
            "stop_cost_assumption": "TAKER",
            "target_cost_assumption": "MAKER",
            "independent_episode_key": f"{state.scenario_id}:{completed.end_ts_ns}",
        }
        plan = TradePlan(
            scenario_id=scenario_id,
            scenario=Scenario.AAC,
            direction=direction,
            observed_ts_ns=observed_ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            atr=atr,
            loss_per_unit=loss,
            gain_per_unit=net_gain,
            net_r=net_r,
            reason_code="GLOBAL_INITIATIVE_MSS_FVG_RETEST",
            expire_ts_ns=expire_ts_ns,
            entry_order_type="LIMIT",
            entry_post_only=True,
            details=details,
        )
        self._states[scenario_id] = "PENDING_ENTRY"
        self._last_emitted_bar_ns = completed.end_ts_ns
        self._event(
            scenario_id=scenario_id,
            event_type="CONTINUATION_PLAN_CONFIRMED",
            event_time_ns=completed.start_ts_ns,
            observed_time_ns=observed_ts_ns,
            previous_state="IDLE",
            next_state="PENDING_ENTRY",
            reason_code=plan.reason_code,
            reference_price=entry,
            details={
                "direction": direction.value,
                "entry": entry,
                "stop": stop,
                "target": target,
                "net_r": net_r,
                **details,
            },
        )
        return plan

    def on_bar(
        self,
        observation: BarObs,
        *,
        state: InitiativeState | None,
        external_engine: Any,
    ) -> TradePlan | None:
        completed = self._aggregate.update(observation)
        if completed is None:
            return None
        previous_close = self._bars[-1].close if self._bars else None
        self._ranges.append(self._true_range(completed, previous_close))
        self._bars.append(completed)
        if len(self._bars) > 512:
            del self._bars[:-384]

        plan = None
        if state is not None:
            plan = self._build_plan(
                completed=completed,
                observed_ts_ns=observation.ts_ns,
                state=state,
                external_engine=external_engine,
            )
        else:
            self.skips["CONTINUATION_WITHOUT_GLOBAL_INITIATIVE"] += 1
        self._confirm_pivot_after_detection(observation.ts_ns)
        return plan

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
            event_type="CONTINUATION_LIFECYCLE",
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
        payload.update({"quantity": str(quantity), "module": CONTINUATION_MODULE})
        self._transition(
            plan,
            ts_ns=plan.observed_ts_ns,
            next_state="SUBMITTED",
            reason="NAUTILUS_BRACKET_SUBMITTED",
            details=payload,
        )

    def mark_entry_filled(self, ts_ns: int, details: dict[str, Any]) -> None:
        scenario_id = str(details.get("scenario_id", ""))
        state = self._states.get(scenario_id)
        if state != "SUBMITTED":
            return
        # Active plan is held by the portfolio; reconstruct a lightweight event
        # without inventing price information.
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type="CONTINUATION_ENTRY_FILLED",
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

    def mark_trade_terminal(self, ts_ns: int, reason: str) -> None:
        active = [
            scenario_id
            for scenario_id, state in self._states.items()
            if state in {"SUBMITTED", "POSITION_OPEN"}
        ]
        if not active:
            return
        scenario_id = active[-1]
        previous = self._states[scenario_id]
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type="CONTINUATION_TERMINAL",
                event_time_ns=int(ts_ns),
                observed_time_ns=int(ts_ns),
                previous_state=previous,
                next_state="TERMINAL",
                reason_code=reason,
                reference_price=None,
                details={"module": CONTINUATION_MODULE},
            ),
        )
        self._states[scenario_id] = "TERMINAL"
