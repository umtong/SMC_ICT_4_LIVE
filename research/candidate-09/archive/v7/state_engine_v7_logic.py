"""Candidate 09 v7: session liquidity sweep, displacement/FVG, and mitigation entry.

Pattern detection and the trade scenario are separated. Completed UTC activity
sessions define neutral external liquidity. A scenario becomes tradable only
when an immediately preceding session extreme is swept during a configured
active session, price reclaims the source range, opposite displacement breaks
micro structure and leaves a three-candle fair-value gap, and the first causal
mitigation of that gap rejects in the reversal direction. The stop remains
outside the observed sweep and the default objective is the opposite edge of
the same completed source session range.
"""

from __future__ import annotations

from collections import deque
from hashlib import sha256
from statistics import median
from typing import Any, Mapping

from state_engine_v7_model import (
    DAY_NS,
    MINUTE_NS,
    DiagnosticEvent,
    EngineConfig,
    EngineResult,
    FlowBar,
    LiquidityLevel,
    PendingSweep,
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
        self._pending: PendingSweep | None = None
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
            self._expire(self._pending, bar, "ACTIVE_SESSION_ENDED_BEFORE_ENTRY", events)
        self._update_session(bar, key, session, start_ns, end_ns, events)

        signal: Signal | None = None
        if self._cooldown > 0:
            self._cooldown -= 1
        elif self._pending is not None:
            signal = self._advance_pending(bar, events)
        elif self._ready:
            self._detect_sweep(bar, events)
        self._volumes.append(bar.volume)
        return EngineResult(tuple(events), signal)

    @property
    def _ready(self) -> bool:
        return (
            self._current_session is not None
            and self._current_session.tradable
            and self._source_range is not None
            and len(self._bars) >= max(self.config.volume_period, self.config.approach_period + 1, self.config.mss_lookback_bars + 3)
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
            details={
                "session": completed.session_name,
                "high": completed.high,
                "low": completed.low,
                "midpoint": completed.midpoint,
                "width": completed.width,
                "bars": completed.bars,
            },
        ))
        return completed

    def _detect_sweep(self, bar: FlowBar, events: list[DiagnosticEvent]) -> None:
        previous = list(self._bars)[-2]
        extension = self.config.minimum_sweep_atr * self._atr
        reclaim = self.config.reclaim_buffer_atr * self._atr
        high_level = self._levels.get("HIGH")
        low_level = self._levels.get("LOW")
        high_sweep = bool(
            high_level and not high_level.consumed and previous.close <= high_level.price
            and bar.high >= high_level.price + extension and bar.close <= high_level.price + reclaim
        )
        low_sweep = bool(
            low_level and not low_level.consumed and previous.close >= low_level.price
            and bar.low <= low_level.price - extension and bar.close >= low_level.price - reclaim
        )
        if high_sweep and low_sweep:
            assert high_level is not None and low_level is not None
            high_level.consumed = True
            low_level.consumed = True
            events.append(DiagnosticEvent(
                scenario_id=f"ambiguous-{bar.ts_ns}",
                event_type="AMBIGUOUS_TWO_SIDED_SWEEP",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                previous_state="ARMED",
                next_state="NO_TRADE",
                reason_code="BOTH_SESSION_EXTREMES_SWEPT_IN_ONE_OBSERVATION",
                reference_price=bar.close,
                details={"source_session": self._source_range.session_name if self._source_range else None},
            ))
            return
        if not high_sweep and not low_sweep:
            return
        if high_sweep:
            assert high_level is not None
            level = high_level
            sweep_direction = "UP"
            extreme = bar.high
        else:
            assert low_level is not None
            level = low_level
            sweep_direction = "DOWN"
            extreme = bar.low
        level.consumed = True
        efficiency, flow = self._approach_pressure(sweep_direction)
        approach_ok = efficiency >= self.config.minimum_approach_efficiency
        if self.config.use_flow_confirmation:
            directional_flow = flow >= self.config.minimum_approach_flow if sweep_direction == "UP" else flow <= -self.config.minimum_approach_flow
            approach_ok = approach_ok and directional_flow
        scenario_id = f"session-sweep-{level.level_id}-{sweep_direction.lower()}-{bar.ts_ns}"
        if not approach_ok:
            events.append(DiagnosticEvent(
                scenario_id=scenario_id,
                event_type="SWEEP_REJECTED",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                previous_state="ARMED",
                next_state="NO_TRADE",
                reason_code="NO_DIRECTIONAL_APPROACH_INTO_SESSION_LIQUIDITY",
                reference_price=level.price,
                details={
                    "sweep_direction": sweep_direction,
                    "approach_efficiency": efficiency,
                    "approach_flow": flow,
                    "source_session": level.source.session_name,
                    "active_session": self._current_session.name if self._current_session else None,
                },
            ))
            return
        assert self._current_session is not None
        pending = PendingSweep(
            scenario_id=scenario_id,
            level=level,
            sweep_direction=sweep_direction,
            state="SWEPT",
            sweep_index=self._index,
            sweep_extreme=extreme,
            approach_efficiency=efficiency,
            approach_flow=flow,
            active_session_key=self._current_session_key,
            active_session_name=self._current_session.name,
        )
        self._pending = pending
        events.append(self._event(
            pending,
            bar,
            "SESSION_LIQUIDITY_SWEEP",
            "ARMED",
            "SWEPT",
            "PREVIOUS_SESSION_EXTREME_TAKEN_AND_RECLAIMED",
        ))

    def _advance_pending(self, bar: FlowBar, events: list[DiagnosticEvent]) -> Signal | None:
        pending = self._pending
        assert pending is not None
        if pending.sweep_direction == "UP":
            pending.sweep_extreme = max(pending.sweep_extreme, bar.high)
        else:
            pending.sweep_extreme = min(pending.sweep_extreme, bar.low)

        if pending.state == "SWEPT":
            if self._index - pending.sweep_index > self.config.displacement_timeout_bars:
                self._expire(pending, bar, "NO_OPPOSITE_DISPLACEMENT_AFTER_SESSION_SWEEP", events)
                return None
            fvg = self._opposite_displacement_fvg(pending, bar)
            if fvg is None:
                return None
            lower, upper = fvg
            pending.state = "WAIT_FVG_RETEST"
            pending.displacement_index = self._index
            pending.displacement_high = bar.high
            pending.displacement_low = bar.low
            pending.fvg_lower = lower
            pending.fvg_upper = upper
            events.append(self._event(
                pending,
                bar,
                "REVERSAL_DISPLACEMENT_CONFIRMED",
                "SWEPT",
                "WAIT_FVG_RETEST" if self.config.require_fvg_retest else "ENTERABLE",
                "MICRO_STRUCTURE_SHIFT_LEFT_OPPOSITE_FAIR_VALUE_GAP",
                {"fvg_lower": lower, "fvg_upper": upper, "fvg_width": upper - lower},
            ))
            if not self.config.require_fvg_retest:
                signal, reason, diagnostic = self._build_signal(pending, bar, entry_model="DISPLACEMENT_CLOSE")
                return self._finish(pending, bar, signal, reason, diagnostic, events)
            return None

        assert pending.state == "WAIT_FVG_RETEST"
        assert pending.displacement_index is not None
        assert pending.fvg_lower is not None and pending.fvg_upper is not None
        buffer = self.config.fvg_invalidation_buffer_atr * self._atr
        if pending.sweep_direction == "DOWN":
            if bar.low < pending.sweep_extreme - buffer:
                self._expire(pending, bar, "SWEEP_LOW_BROKEN_AFTER_BULLISH_DISPLACEMENT", events)
                return None
            if bar.close < pending.fvg_lower - buffer:
                self._expire(pending, bar, "BULLISH_FVG_CLOSED_THROUGH_BEFORE_ENTRY", events)
                return None
        else:
            if bar.high > pending.sweep_extreme + buffer:
                self._expire(pending, bar, "SWEEP_HIGH_BROKEN_AFTER_BEARISH_DISPLACEMENT", events)
                return None
            if bar.close > pending.fvg_upper + buffer:
                self._expire(pending, bar, "BEARISH_FVG_CLOSED_THROUGH_BEFORE_ENTRY", events)
                return None
        if self._fvg_retest_rejected(pending, bar):
            events.append(self._event(
                pending,
                bar,
                "FVG_MITIGATION_REJECTED",
                "WAIT_FVG_RETEST",
                "ENTERABLE",
                "FIRST_CAUSAL_FVG_RETEST_REJECTED_IN_REVERSAL_DIRECTION",
            ))
            signal, reason, diagnostic = self._build_signal(pending, bar, entry_model="FVG_RETEST")
            return self._finish(pending, bar, signal, reason, diagnostic, events)
        if self._index - pending.displacement_index > self.config.fvg_retest_timeout_bars:
            self._expire(pending, bar, "FVG_NOT_MITIGATED_WITHIN_ENTRY_WINDOW", events)
        return None

    def _opposite_displacement_fvg(self, pending: PendingSweep, bar: FlowBar) -> tuple[float, float] | None:
        bars = list(self._bars)
        if len(bars) < max(3, self.config.mss_lookback_bars + 1):
            return None
        previous_window = bars[-(self.config.mss_lookback_bars + 1) : -1]
        two_back = bars[-3]
        body = abs(bar.close - bar.open) / max(self._atr, 1e-12)
        volume_ok = self._volume_ratio(bar) >= self.config.minimum_volume_ratio
        if pending.sweep_direction == "DOWN":
            structure = bar.close > max(item.high for item in previous_window)
            direction = bar.close > bar.open
            flow_ok = bar.flow_imbalance >= self.config.directional_imbalance
            lower, upper = two_back.high, bar.low
        else:
            structure = bar.close < min(item.low for item in previous_window)
            direction = bar.close < bar.open
            flow_ok = bar.flow_imbalance <= -self.config.directional_imbalance
            lower, upper = bar.high, two_back.low
        if not self.config.use_flow_confirmation:
            flow_ok = True
        gap_ok = upper - lower >= self.config.minimum_fvg_atr * self._atr
        if direction and structure and body >= self.config.minimum_displacement_atr and volume_ok and flow_ok and gap_ok:
            return lower, upper
        return None

    def _fvg_retest_rejected(self, pending: PendingSweep, bar: FlowBar) -> bool:
        assert pending.fvg_lower is not None and pending.fvg_upper is not None
        touched = bar.low <= pending.fvg_upper and bar.high >= pending.fvg_lower
        midpoint = (pending.fvg_lower + pending.fvg_upper) / 2.0
        if pending.sweep_direction == "DOWN":
            rejected = bar.close >= midpoint and bar.close > bar.open
            flow_ok = bar.flow_imbalance >= self.config.directional_imbalance
        else:
            rejected = bar.close <= midpoint and bar.close < bar.open
            flow_ok = bar.flow_imbalance <= -self.config.directional_imbalance
        if not self.config.use_flow_confirmation:
            flow_ok = True
        return touched and rejected and flow_ok

    def _build_signal(
        self,
        pending: PendingSweep,
        bar: FlowBar,
        *,
        entry_model: str,
    ) -> tuple[Signal | None, str, Mapping[str, Any]]:
        entry = bar.close
        atr = max(self._atr, 1e-12)
        source = pending.level.source
        if pending.sweep_direction == "DOWN":
            side = "BUY"
            stop = pending.sweep_extreme - self.config.stop_buffer_atr * atr
            target = source.midpoint if self.config.use_midpoint_target else source.high
        else:
            side = "SELL"
            stop = pending.sweep_extreme + self.config.stop_buffer_atr * atr
            target = source.midpoint if self.config.use_midpoint_target else source.low
        diagnostic: dict[str, Any] = {
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "source_high": source.high,
            "source_low": source.low,
            "source_midpoint": source.midpoint,
            "source_width": source.width,
            "entry_model": entry_model,
            "target_model": "MIDPOINT" if self.config.use_midpoint_target else "OPPOSITE_SESSION_EDGE",
        }
        geometry_ok = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry_ok:
            return None, "SESSION_OBJECTIVE_NOT_BEYOND_ENTRY_WITH_VALID_STOP", diagnostic
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
        reason = "SESSION_SWEEP_DISPLACEMENT_FVG_MITIGATION_TO_OPPOSITE_LIQUIDITY"
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
                "sweep_level": pending.level.price,
                "sweep_extreme": pending.sweep_extreme,
                "approach_efficiency": pending.approach_efficiency,
                "approach_flow": pending.approach_flow,
                "fvg_lower": pending.fvg_lower,
                "fvg_upper": pending.fvg_upper,
            },
        )
        return signal, reason, diagnostic

    def _finish(
        self,
        pending: PendingSweep,
        bar: FlowBar,
        signal: Signal | None,
        reason: str,
        diagnostic: Mapping[str, Any],
        events: list[DiagnosticEvent],
    ) -> Signal | None:
        if signal is None:
            events.append(self._event(
                pending,
                bar,
                "SCENARIO_REJECTED",
                pending.state,
                "NO_TRADE",
                reason,
                diagnostic,
            ))
        else:
            events.append(self._event(
                pending,
                bar,
                "ENTRY_APPROVED",
                pending.state,
                "ENTERABLE",
                signal.reason_code,
                {"side": signal.side, "stop": signal.stop_price, "target": signal.target_price,
                 "net_reward_to_risk": signal.net_reward_to_risk},
            ))
        self._pending = None
        self._cooldown = self.config.cooldown_bars
        return signal

    def _approach_pressure(self, sweep_direction: str) -> tuple[float, float]:
        window = list(self._bars)[:-1][-self.config.approach_period :]
        if len(window) < 2:
            return 0.0, 0.0
        move = window[-1].close - window[0].close
        path = sum(abs(right.close - left.close) for left, right in zip(window, window[1:]))
        efficiency = move / max(path, 1e-12)
        if sweep_direction == "DOWN":
            efficiency = -efficiency
        signed_flow = sum(item.signed_flow for item in window)
        total_volume = sum(item.volume for item in window)
        return efficiency, signed_flow / max(total_volume, 1e-12)

    def _volume_ratio(self, bar: FlowBar) -> float:
        return bar.volume / max(self._volume_median, 1e-12)

    def _event(
        self,
        pending: PendingSweep,
        bar: FlowBar,
        event_type: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        extra: Mapping[str, Any] | None = None,
    ) -> DiagnosticEvent:
        source = pending.level.source
        details: dict[str, Any] = {
            "sweep_direction": pending.sweep_direction,
            "level_price": pending.level.price,
            "sweep_extreme": pending.sweep_extreme,
            "source_session": source.session_name,
            "active_session": pending.active_session_name,
            "source_high": source.high,
            "source_low": source.low,
            "source_midpoint": source.midpoint,
            "source_width": source.width,
            "approach_efficiency": pending.approach_efficiency,
            "approach_flow": pending.approach_flow,
            "fvg_lower": pending.fvg_lower,
            "fvg_upper": pending.fvg_upper,
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
            pending.scenario_id,
            event_type,
            bar.ts_ns,
            bar.ts_ns,
            previous_state,
            next_state,
            reason_code,
            bar.close,
            details,
        )

    def _expire(self, pending: PendingSweep, bar: FlowBar, reason: str, events: list[DiagnosticEvent]) -> None:
        events.append(self._event(pending, bar, "SCENARIO_EXPIRED", pending.state, "NO_TRADE", reason))
        self._pending = None
        self._cooldown = self.config.cooldown_bars
