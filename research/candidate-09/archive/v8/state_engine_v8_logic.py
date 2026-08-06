"""Candidate 09 v8: completed-session acceptance failure rotation.

The previous UTC activity session is the dealing range. The detector records a
breach and genuine outside acceptance separately. A reversal scenario exists
only when that accepted auction fails back inside with opposite displacement and
micro-structure shift. Baseline enters on the confirmed failure close, keeps the
stop beyond the entire accepted excursion, and targets the opposite edge of the
completed source range. Ablations remove acceptance, add a failure retest, or
replace the external-liquidity target with the source midpoint.
"""

from __future__ import annotations

from collections import deque
from hashlib import sha256
from statistics import median
from typing import Any, Mapping

from state_engine_v8_model import (
    DAY_NS,
    MINUTE_NS,
    DiagnosticEvent,
    EngineConfig,
    EngineResult,
    FlowBar,
    LiquidityLevel,
    PendingAcceptanceFailure,
    SessionBuilder,
    SessionRange,
    SessionSpec,
    Signal,
)


class LiquidityStateEngine:
    def __init__(self, config: EngineConfig):
        self.config = config
        history_size = max(512, config.volume_period + 8, config.approach_period + 8, config.mss_lookback_bars + 8)
        self._bars: deque[FlowBar] = deque(maxlen=history_size)
        self._true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self._volumes: deque[float] = deque(maxlen=config.volume_period)
        self._builder: SessionBuilder | None = None
        self._source_range: SessionRange | None = None
        self._levels: dict[str, LiquidityLevel] = {}
        self._pending: PendingAcceptanceFailure | None = None
        self._index = -1
        self._cooldown = 0
        self._atr = 0.0
        self._volume_median = 0.0
        self._last_timestamp = -1
        self._current_session_key = -1
        self._current_session: SessionSpec | None = None

    @property
    def atr(self) -> float:
        return self._atr

    @property
    def source_range(self) -> SessionRange | None:
        return self._source_range

    @property
    def active_levels(self) -> tuple[LiquidityLevel, ...]:
        return tuple(level for level in self._levels.values() if not level.consumed)

    def on_bar(self, bar: FlowBar) -> EngineResult:
        if bar.ts_ns <= self._last_timestamp:
            raise ValueError("bars must be strictly increasing by observation timestamp")
        self._last_timestamp = bar.ts_ns
        self._index += 1
        previous_close = self._bars[-1].close if self._bars else bar.close
        self._true_ranges.append(max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close)))
        self._atr = sum(self._true_ranges) / len(self._true_ranges)
        self._volume_median = median(self._volumes) if self._volumes else max(bar.volume, 1e-12)
        self._bars.append(bar)
        events: list[DiagnosticEvent] = []

        key, _, session, start_ns, end_ns = self._session_identity(bar.ts_ns)
        if self._pending is not None and key != self._pending.active_session_key:
            self._expire(self._pending, bar, "ACTIVE_SESSION_ENDED_BEFORE_ACCEPTED_FAILURE_RESOLVED", events)
        self._update_session(bar, key, session, start_ns, end_ns, events)

        signal: Signal | None = None
        if self._cooldown > 0:
            self._cooldown -= 1
        elif self._pending is not None:
            signal = self._advance_pending(bar, events)
        elif self._ready:
            self._detect_breach(bar, events)
        self._volumes.append(bar.volume)
        return EngineResult(tuple(events), signal)

    @property
    def _ready(self) -> bool:
        return (
            self._current_session is not None
            and self._current_session.tradable
            and self._source_range is not None
            and len(self._bars) >= max(self.config.volume_period, self.config.approach_period + 1, self.config.mss_lookback_bars + 2)
            and self._atr > 0.0
            and bool(self.active_levels)
        )

    def _session_identity(self, ts_ns: int) -> tuple[int, int, SessionSpec, int, int]:
        observed_ns = max(0, ts_ns - 1)
        day_index = observed_ns // DAY_NS
        minute_of_day = (observed_ns % DAY_NS) // MINUTE_NS
        for index, session in enumerate(self.config.sessions):
            if session.start_minute_utc <= minute_of_day < session.end_minute_utc:
                key = int(day_index) * len(self.config.sessions) + index
                start_ns = int(day_index) * DAY_NS + session.start_minute_utc * MINUTE_NS
                end_ns = int(day_index) * DAY_NS + session.end_minute_utc * MINUTE_NS
                return key, index, session, start_ns, end_ns
        raise AssertionError("validated session schedule did not cover timestamp")

    def _update_session(
        self,
        bar: FlowBar,
        key: int,
        session: SessionSpec,
        start_ns: int,
        end_ns: int,
        events: list[DiagnosticEvent],
    ) -> None:
        self._current_session_key = key
        self._current_session = session
        builder = self._builder
        if builder is None:
            self._builder = SessionBuilder(key, key % len(self.config.sessions), session.name, start_ns, end_ns,
                                           bar.high, bar.low, bar.close)
            return
        if key == builder.key:
            builder.high = max(builder.high, bar.high)
            builder.low = min(builder.low, bar.low)
            builder.close = bar.close
            builder.bars += 1
            return
        if key < builder.key:
            raise ValueError("session key moved backward")
        completed = self._finalize_builder(builder, bar, events)
        self._builder = SessionBuilder(key, key % len(self.config.sessions), session.name, start_ns, end_ns,
                                       bar.high, bar.low, bar.close)
        self._source_range = completed
        self._levels = {}
        if completed is not None:
            for kind, price in (("HIGH", completed.high), ("LOW", completed.low)):
                level_id = sha256(f"{completed.range_id}|{kind}".encode()).hexdigest()[:16]
                self._levels[kind] = LiquidityLevel(level_id, kind, price, completed)
                events.append(DiagnosticEvent(
                    scenario_id=f"level-{level_id}",
                    event_type="SESSION_LIQUIDITY_LEVEL_ARMED",
                    event_time_ns=completed.end_ns,
                    observed_time_ns=bar.ts_ns,
                    previous_state="SESSION_COMPLETED",
                    next_state="ARMED" if session.tradable else "OBSERVE_ONLY",
                    reason_code=f"PREVIOUS_{completed.session_name}_SESSION_{kind}",
                    reference_price=price,
                    details={
                        "source_session": completed.session_name,
                        "active_session": session.name,
                        "active_session_tradable": session.tradable,
                        "source_high": completed.high,
                        "source_low": completed.low,
                        "source_midpoint": completed.midpoint,
                        "source_width": completed.width,
                    },
                ))

    def _finalize_builder(
        self,
        builder: SessionBuilder,
        observed_bar: FlowBar,
        events: list[DiagnosticEvent],
    ) -> SessionRange | None:
        expected_minutes = max(1, (builder.end_ns - builder.start_ns) // MINUTE_NS)
        if builder.bars < max(3, expected_minutes // 2) or builder.high <= builder.low:
            events.append(DiagnosticEvent(
                scenario_id=f"session-{builder.key}",
                event_type="SESSION_RANGE_REJECTED",
                event_time_ns=builder.end_ns,
                observed_time_ns=observed_bar.ts_ns,
                previous_state="FORMING",
                next_state="NO_LEVEL",
                reason_code="INCOMPLETE_OR_DEGENERATE_SESSION_RANGE",
                reference_price=builder.close,
                details={"session": builder.session_name, "bars": builder.bars, "expected_minutes": expected_minutes},
            ))
            return None
        range_id = sha256(
            f"{builder.key}|{builder.session_name}|{builder.high:.10f}|{builder.low:.10f}|{builder.close:.10f}".encode()
        ).hexdigest()[:16]
        completed = SessionRange(
            range_id=range_id,
            session_name=builder.session_name,
            start_ns=builder.start_ns,
            end_ns=builder.end_ns,
            high=builder.high,
            low=builder.low,
            close=builder.close,
            bars=builder.bars,
        )
        events.append(DiagnosticEvent(
            scenario_id=f"session-{range_id}",
            event_type="SESSION_RANGE_CONFIRMED",
            event_time_ns=completed.end_ns,
            observed_time_ns=observed_bar.ts_ns,
            previous_state="FORMING",
            next_state="COMPLETED",
            reason_code=f"COMPLETED_{completed.session_name}_UTC_ACTIVITY_RANGE",
            reference_price=completed.close,
            details={"session": completed.session_name, "high": completed.high, "low": completed.low,
                     "midpoint": completed.midpoint, "width": completed.width, "bars": completed.bars},
        ))
        return completed

    def _detect_breach(self, bar: FlowBar, events: list[DiagnosticEvent]) -> None:
        previous = list(self._bars)[-2]
        extension = self.config.minimum_breach_atr * self._atr
        high_level = self._levels.get("HIGH")
        low_level = self._levels.get("LOW")
        high_breach = bool(high_level and not high_level.consumed and previous.close <= high_level.price
                           and bar.high >= high_level.price + extension)
        low_breach = bool(low_level and not low_level.consumed and previous.close >= low_level.price
                          and bar.low <= low_level.price - extension)
        if high_breach and low_breach:
            assert high_level is not None and low_level is not None
            high_level.consumed = True
            low_level.consumed = True
            events.append(DiagnosticEvent(
                scenario_id=f"ambiguous-{bar.ts_ns}",
                event_type="AMBIGUOUS_TWO_SIDED_BREACH",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                previous_state="ARMED",
                next_state="NO_TRADE",
                reason_code="BOTH_SESSION_EXTREMES_BREACHED_IN_ONE_OBSERVATION",
                reference_price=bar.close,
                details={"source_session": self._source_range.session_name if self._source_range else None},
            ))
            return
        if not high_breach and not low_breach:
            return
        if high_breach:
            assert high_level is not None
            level, direction, extreme = high_level, "UP", bar.high
        else:
            assert low_level is not None
            level, direction, extreme = low_level, "DOWN", bar.low
        level.consumed = True
        efficiency, flow = self._approach_pressure(direction)
        approach_ok = efficiency >= self.config.minimum_approach_efficiency
        if self.config.use_flow_confirmation:
            flow_ok = flow >= self.config.minimum_approach_flow if direction == "UP" else flow <= -self.config.minimum_approach_flow
            approach_ok = approach_ok and flow_ok
        scenario_id = f"session-acceptance-{level.level_id}-{direction.lower()}-{bar.ts_ns}"
        if not approach_ok:
            events.append(DiagnosticEvent(
                scenario_id=scenario_id,
                event_type="BREACH_REJECTED",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                previous_state="ARMED",
                next_state="NO_TRADE",
                reason_code="NO_DIRECTIONAL_APPROACH_INTO_SESSION_LIQUIDITY",
                reference_price=level.price,
                details={"breach_direction": direction, "approach_efficiency": efficiency,
                         "approach_flow": flow, "source_session": level.source.session_name,
                         "active_session": self._current_session.name if self._current_session else None},
            ))
            return
        assert self._current_session is not None
        pending = PendingAcceptanceFailure(
            scenario_id=scenario_id,
            level=level,
            breach_direction=direction,
            state="BREACHED",
            breach_index=self._index,
            extreme=extreme,
            approach_efficiency=efficiency,
            approach_flow=flow,
            active_session_key=self._current_session_key,
            active_session_name=self._current_session.name,
        )
        self._accumulate_acceptance(pending, bar)
        if self._outside(bar, pending):
            pending.outside_closes = 1
        self._pending = pending
        events.append(self._event(
            pending, bar, "SESSION_RANGE_BREACH", "ARMED", "BREACHED",
            "PREVIOUS_SESSION_EXTREME_TAKEN_WITH_DIRECTIONAL_APPROACH",
        ))
        if not self.config.require_acceptance_confirmation:
            pending.state = "ACCEPTED"
            pending.acceptance_index = self._index
            events.append(self._event(
                pending, bar, "ACCEPTANCE_CONFIRMATION_BYPASSED", "BREACHED", "ACCEPTED",
                "ABLATION_REMOVED_OUTSIDE_ACCEPTANCE_REQUIREMENT",
            ))

    def _advance_pending(self, bar: FlowBar, events: list[DiagnosticEvent]) -> Signal | None:
        pending = self._pending
        assert pending is not None
        pending.extreme = max(pending.extreme, bar.high) if pending.breach_direction == "UP" else min(pending.extreme, bar.low)

        if pending.state == "BREACHED":
            self._accumulate_acceptance(pending, bar)
            if self._outside(bar, pending):
                pending.outside_closes += 1
            else:
                pending.outside_closes = 0
                self._expire(pending, bar, "BREACH_REENTERED_SOURCE_RANGE_BEFORE_ACCEPTANCE", events)
                return None
            if self._acceptance_ready(pending):
                pending.state = "ACCEPTED"
                pending.acceptance_index = self._index
                events.append(self._event(
                    pending, bar, "OUTSIDE_ACCEPTANCE_CONFIRMED", "BREACHED", "ACCEPTED",
                    "SESSION_BREAKOUT_ACCEPTED_WITH_DISPLACEMENT_VOLUME_AND_FLOW",
                ))
            elif self._index - pending.breach_index > self.config.acceptance_timeout_bars:
                self._expire(pending, bar, "SESSION_BREAKOUT_DID_NOT_ACHIEVE_ACCEPTANCE", events)
            return None

        if pending.state == "ACCEPTED":
            assert pending.acceptance_index is not None
            if self._index > pending.acceptance_index and self._failure_confirmed(pending, bar):
                pending.failure_index = self._index
                pending.failure_high = bar.high
                pending.failure_low = bar.low
                events.append(self._event(
                    pending, bar, "ACCEPTED_SESSION_BREAKOUT_FAILED", "ACCEPTED",
                    "FAILED" if self.config.require_failure_retest else "ENTERABLE",
                    "ACCEPTED_SESSION_AUCTION_LOST_WITH_OPPOSITE_MSS",
                ))
                if self.config.require_failure_retest:
                    pending.state = "FAILED"
                    return None
                signal, reason, diagnostic = self._build_signal(pending, bar, entry_model="FAILURE_CLOSE")
                return self._finish(pending, bar, signal, reason, diagnostic, events)
            if self._index - pending.acceptance_index > self.config.failure_timeout_bars:
                self._expire(pending, bar, "ACCEPTED_SESSION_BREAKOUT_DID_NOT_FAIL_WITHIN_WINDOW", events)
            return None

        assert pending.state == "FAILED" and pending.failure_index is not None
        if self._outside(bar, pending):
            self._expire(pending, bar, "FAILED_SESSION_LEVEL_REACCEPTED_OUTSIDE_RANGE", events)
            return None
        if self._index > pending.failure_index and self._failure_retest_rejected(pending, bar):
            events.append(self._event(
                pending, bar, "FAILED_LEVEL_RETEST_REJECTED", "FAILED", "ENTERABLE",
                "FAILED_SESSION_BOUNDARY_RETEST_CLOSED_BACK_INSIDE_RANGE",
            ))
            signal, reason, diagnostic = self._build_signal(pending, bar, entry_model="FAILURE_RETEST")
            return self._finish(pending, bar, signal, reason, diagnostic, events)
        if self._index - pending.failure_index > self.config.failure_retest_timeout_bars:
            self._expire(pending, bar, "FAILED_SESSION_LEVEL_DID_NOT_RETEST_AND_REJECT", events)
        return None

    def _accumulate_acceptance(self, pending: PendingAcceptanceFailure, bar: FlowBar) -> None:
        body = abs(bar.close - bar.open) / max(self._atr, 1e-12)
        if pending.breach_direction == "UP":
            aligned_body = bar.close > bar.open
            aligned_flow = bar.flow_imbalance >= self.config.directional_imbalance
        else:
            aligned_body = bar.close < bar.open
            aligned_flow = bar.flow_imbalance <= -self.config.directional_imbalance
        pending.acceptance_displacement_seen = pending.acceptance_displacement_seen or (
            aligned_body and body >= self.config.minimum_acceptance_displacement_atr
        )
        pending.acceptance_flow_seen = pending.acceptance_flow_seen or aligned_flow
        pending.max_volume_ratio = max(pending.max_volume_ratio, self._volume_ratio(bar))

    def _acceptance_ready(self, pending: PendingAcceptanceFailure) -> bool:
        if pending.outside_closes < self.config.acceptance_closes:
            return False
        flow_ok = pending.acceptance_flow_seen if self.config.use_flow_confirmation else True
        return (
            pending.acceptance_displacement_seen
            and flow_ok
            and pending.max_volume_ratio >= self.config.minimum_volume_ratio
        )

    def _failure_confirmed(self, pending: PendingAcceptanceFailure, bar: FlowBar) -> bool:
        buffer = self.config.failure_buffer_atr * self._atr
        body = abs(bar.close - bar.open) / max(self._atr, 1e-12)
        prior = list(self._bars)[-(self.config.mss_lookback_bars + 1) : -1]
        if not prior:
            return False
        if pending.breach_direction == "UP":
            inside = bar.close <= pending.level.price - buffer
            directional = bar.close < bar.open
            mss = bar.close < min(item.low for item in prior)
            flow_ok = bar.flow_imbalance <= -self.config.directional_imbalance
        else:
            inside = bar.close >= pending.level.price + buffer
            directional = bar.close > bar.open
            mss = bar.close > max(item.high for item in prior)
            flow_ok = bar.flow_imbalance >= self.config.directional_imbalance
        if not self.config.use_flow_confirmation:
            flow_ok = True
        return inside and directional and mss and body >= self.config.minimum_failure_displacement_atr and flow_ok

    def _failure_retest_rejected(self, pending: PendingAcceptanceFailure, bar: FlowBar) -> bool:
        tolerance = self.config.retest_tolerance_atr * self._atr
        body = abs(bar.close - bar.open) / max(self._atr, 1e-12)
        if pending.breach_direction == "UP":
            touched = bar.high >= pending.level.price - tolerance
            rejected = bar.close < pending.level.price and bar.close < bar.open
            flow_ok = bar.flow_imbalance <= -self.config.directional_imbalance
        else:
            touched = bar.low <= pending.level.price + tolerance
            rejected = bar.close > pending.level.price and bar.close > bar.open
            flow_ok = bar.flow_imbalance >= self.config.directional_imbalance
        if not self.config.use_flow_confirmation:
            flow_ok = True
        return touched and rejected and body >= self.config.minimum_failure_displacement_atr and flow_ok

    def _outside(self, bar: FlowBar, pending: PendingAcceptanceFailure) -> bool:
        buffer = self.config.acceptance_buffer_atr * self._atr
        return bar.close >= pending.level.price + buffer if pending.breach_direction == "UP" else bar.close <= pending.level.price - buffer

    def _build_signal(
        self,
        pending: PendingAcceptanceFailure,
        bar: FlowBar,
        *,
        entry_model: str,
    ) -> tuple[Signal | None, str, Mapping[str, Any]]:
        entry = bar.close
        atr = max(self._atr, 1e-12)
        source = pending.level.source
        if pending.breach_direction == "UP":
            side = "SELL"
            stop = pending.extreme + self.config.stop_buffer_atr * atr
            target = source.midpoint if self.config.use_midpoint_target else source.low
        else:
            side = "BUY"
            stop = pending.extreme - self.config.stop_buffer_atr * atr
            target = source.midpoint if self.config.use_midpoint_target else source.high
        diagnostic: dict[str, Any] = {
            "side": side, "entry": entry, "stop": stop, "target": target,
            "source_high": source.high, "source_low": source.low,
            "source_midpoint": source.midpoint, "source_width": source.width,
            "entry_model": entry_model,
            "target_model": "MIDPOINT" if self.config.use_midpoint_target else "OPPOSITE_SESSION_EDGE",
        }
        geometry_ok = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry_ok:
            return None, "SESSION_OBJECTIVE_NOT_BEYOND_FAILURE_ENTRY_WITH_VALID_STOP", diagnostic
        cost = self.config.composite_cost_per_fill
        price_risk = abs(entry - stop)
        if price_risk < 2.0 * cost * entry:
            diagnostic["price_risk"] = price_risk
            return None, "PRICE_RISK_TOO_SMALL_RELATIVE_TO_ROUND_TRIP_COST", diagnostic
        risk = price_risk + cost * entry + cost * stop
        reward = abs(target - entry) - cost * entry - cost * target
        diagnostic.update({"price_risk": price_risk, "net_risk_per_unit": risk, "net_reward_per_unit": reward})
        if risk <= 0.0 or reward <= 0.0:
            return None, "STRUCTURAL_TARGET_HAS_NONPOSITIVE_REWARD_AFTER_COST", diagnostic
        net_rr = reward / risk
        diagnostic["net_reward_to_risk"] = net_rr
        if net_rr < self.config.minimum_net_reward_to_risk:
            return None, "STRUCTURAL_TARGET_NET_REWARD_TO_RISK_BELOW_GATE", diagnostic
        reason = "ACCEPTED_SESSION_BREAKOUT_FAILURE_ROTATION_TO_OPPOSITE_LIQUIDITY"
        signal = Signal(
            scenario_id=pending.scenario_id,
            branch="REVERSAL",
            side=side,
            observed_time_ns=bar.ts_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            net_reward_to_risk=net_rr,
            reason_code=reason,
            details={
                **diagnostic,
                "source_session": source.session_name,
                "active_session": pending.active_session_name,
                "breach_level": pending.level.price,
                "accepted_extreme": pending.extreme,
                "approach_efficiency": pending.approach_efficiency,
                "approach_flow": pending.approach_flow,
                "outside_closes": pending.outside_closes,
                "max_volume_ratio": pending.max_volume_ratio,
            },
        )
        return signal, reason, diagnostic

    def _finish(
        self,
        pending: PendingAcceptanceFailure,
        bar: FlowBar,
        signal: Signal | None,
        reason: str,
        diagnostic: Mapping[str, Any],
        events: list[DiagnosticEvent],
    ) -> Signal | None:
        if signal is None:
            events.append(self._event(pending, bar, "SCENARIO_REJECTED", pending.state, "NO_TRADE", reason, diagnostic))
        else:
            events.append(self._event(
                pending, bar, "ENTRY_APPROVED", pending.state, "ENTERABLE", signal.reason_code,
                {"side": signal.side, "stop": signal.stop_price, "target": signal.target_price,
                 "net_reward_to_risk": signal.net_reward_to_risk},
            ))
        self._pending = None
        self._cooldown = self.config.cooldown_bars
        return signal

    def _approach_pressure(self, direction: str) -> tuple[float, float]:
        window = list(self._bars)[:-1][-self.config.approach_period :]
        if len(window) < 2:
            return 0.0, 0.0
        move = window[-1].close - window[0].close
        path = sum(abs(right.close - left.close) for left, right in zip(window, window[1:]))
        efficiency = move / max(path, 1e-12)
        if direction == "DOWN":
            efficiency = -efficiency
        signed_flow = sum(item.signed_flow for item in window)
        total_volume = sum(item.volume for item in window)
        return efficiency, signed_flow / max(total_volume, 1e-12)

    def _volume_ratio(self, bar: FlowBar) -> float:
        return bar.volume / max(self._volume_median, 1e-12)

    def _event(
        self,
        pending: PendingAcceptanceFailure,
        bar: FlowBar,
        event_type: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        extra: Mapping[str, Any] | None = None,
    ) -> DiagnosticEvent:
        source = pending.level.source
        details: dict[str, Any] = {
            "breach_direction": pending.breach_direction,
            "level_price": pending.level.price,
            "accepted_extreme": pending.extreme,
            "source_session": source.session_name,
            "active_session": pending.active_session_name,
            "source_high": source.high,
            "source_low": source.low,
            "source_midpoint": source.midpoint,
            "source_width": source.width,
            "approach_efficiency": pending.approach_efficiency,
            "approach_flow": pending.approach_flow,
            "outside_closes": pending.outside_closes,
            "max_volume_ratio": pending.max_volume_ratio,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "flow_imbalance": bar.flow_imbalance,
            "volume_ratio": self._volume_ratio(bar),
            "atr": self._atr,
        }
        if extra:
            details.update(extra)
        return DiagnosticEvent(
            pending.scenario_id, event_type, bar.ts_ns, bar.ts_ns, previous_state, next_state,
            reason_code, bar.close, details,
        )

    def _expire(
        self,
        pending: PendingAcceptanceFailure,
        bar: FlowBar,
        reason: str,
        events: list[DiagnosticEvent],
    ) -> None:
        events.append(self._event(pending, bar, "SCENARIO_EXPIRED", pending.state, "NO_TRADE", reason))
        self._pending = None
        self._cooldown = self.config.cooldown_bars
