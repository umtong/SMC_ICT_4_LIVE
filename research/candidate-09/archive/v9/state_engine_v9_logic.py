"""Candidate 09 v9: failed-failure session breakout continuation.

A completed previous-session range supplies neutral external liquidity. The
breakout must first achieve outside acceptance. Baseline then requires an
opposite micro-structure shift back inside (an apparent failure), followed by
renewed acceptance beyond the original boundary with original-direction
micro-structure break, displacement, volume and order flow. This second-order
state traps countertrend participants rather than entering the first accepted
retest as v3 did. The objective is a measured source-range expansion.
"""

from __future__ import annotations

from typing import Any, Mapping

from state_engine_v8_logic import LiquidityStateEngine as V8LiquidityStateEngine
from state_engine_v9_model import DiagnosticEvent, EngineConfig, FlowBar, PendingAcceptanceFailure, Signal


class LiquidityStateEngine(V8LiquidityStateEngine):
    config: EngineConfig

    def __init__(self, config: EngineConfig):
        super().__init__(config)

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
                    pending,
                    bar,
                    "OUTSIDE_ACCEPTANCE_CONFIRMED",
                    "BREACHED",
                    "ACCEPTED",
                    "SESSION_BREAKOUT_ACCEPTED_WITH_DISPLACEMENT_VOLUME_AND_FLOW",
                ))
                if not self.config.require_failure_trap:
                    signal, reason, diagnostic = self._build_signal(pending, bar, entry_model="OUTSIDE_ACCEPTANCE")
                    return self._finish(pending, bar, signal, reason, diagnostic, events)
            elif self._index - pending.breach_index > self.config.acceptance_timeout_bars:
                self._expire(pending, bar, "SESSION_BREAKOUT_DID_NOT_ACHIEVE_ACCEPTANCE", events)
            return None

        if pending.state == "ACCEPTED":
            assert pending.acceptance_index is not None
            if self._index > pending.acceptance_index and self._failure_confirmed(pending, bar):
                pending.state = "FAILED_ATTEMPT"
                pending.failure_index = self._index
                pending.failure_high = bar.high
                pending.failure_low = bar.low
                events.append(self._event(
                    pending,
                    bar,
                    "ACCEPTED_BREAKOUT_FAILURE_ATTEMPT",
                    "ACCEPTED",
                    "FAILED_ATTEMPT",
                    "ACCEPTED_SESSION_LEVEL_LOST_WITH_COUNTERTREND_MSS",
                ))
            elif self._index - pending.acceptance_index > self.config.failure_timeout_bars:
                self._expire(pending, bar, "ACCEPTED_BREAKOUT_NEITHER_FAILED_NOR_TRAPPED_COUNTERTREND", events)
            return None

        if pending.state == "FAILED_ATTEMPT":
            assert pending.failure_index is not None
            if self._reacceptance_confirmed(pending, bar):
                pending.state = "REACCEPTED"
                pending.acceptance_index = self._index
                events.append(self._event(
                    pending,
                    bar,
                    "FAILED_FAILURE_REACCEPTED",
                    "FAILED_ATTEMPT",
                    "WAIT_REACCEPTANCE_RETEST" if self.config.require_reacceptance_retest else "ENTERABLE",
                    "COUNTERTREND_FAILURE_TRAPPED_AND_ORIGINAL_AUCTION_REACCEPTED",
                ))
                if not self.config.require_reacceptance_retest:
                    signal, reason, diagnostic = self._build_signal(pending, bar, entry_model="REACCEPTANCE_CLOSE")
                    return self._finish(pending, bar, signal, reason, diagnostic, events)
            elif self._index - pending.failure_index > self.config.failure_timeout_bars:
                self._expire(pending, bar, "APPARENT_FAILURE_DID_NOT_REACCEPT_ORIGINAL_BREAKOUT", events)
            return None

        assert pending.state == "REACCEPTED"
        assert pending.acceptance_index is not None
        if self._reacceptance_invalidated(pending, bar):
            self._expire(pending, bar, "REACCEPTED_BREAKOUT_CLOSED_BACK_INSIDE_SOURCE_RANGE", events)
            return None
        if self._index > pending.acceptance_index and self._reacceptance_retest_rejected(pending, bar):
            events.append(self._event(
                pending,
                bar,
                "REACCEPTED_LEVEL_RETEST_DEFENDED",
                "REACCEPTED",
                "ENTERABLE",
                "ORIGINAL_BREAKOUT_BOUNDARY_RETEST_DEFENDED_AFTER_FAILED_FAILURE",
            ))
            signal, reason, diagnostic = self._build_signal(pending, bar, entry_model="REACCEPTANCE_RETEST")
            return self._finish(pending, bar, signal, reason, diagnostic, events)
        if self._index - pending.acceptance_index > self.config.failure_retest_timeout_bars:
            self._expire(pending, bar, "REACCEPTED_BREAKOUT_NOT_RETESTED_WITHIN_ENTRY_WINDOW", events)
        return None

    def _reacceptance_confirmed(self, pending: PendingAcceptanceFailure, bar: FlowBar) -> bool:
        buffer = self.config.acceptance_buffer_atr * self._atr
        body = abs(bar.close - bar.open) / max(self._atr, 1e-12)
        prior = list(self._bars)[-(self.config.mss_lookback_bars + 1) : -1]
        if not prior:
            return False
        if pending.breach_direction == "UP":
            outside = bar.close >= pending.level.price + buffer
            directional = bar.close > bar.open
            mss = bar.close > max(item.high for item in prior)
            flow_ok = bar.flow_imbalance >= self.config.directional_imbalance
        else:
            outside = bar.close <= pending.level.price - buffer
            directional = bar.close < bar.open
            mss = bar.close < min(item.low for item in prior)
            flow_ok = bar.flow_imbalance <= -self.config.directional_imbalance
        if not self.config.use_flow_confirmation:
            flow_ok = True
        return (
            outside
            and directional
            and mss
            and body >= self.config.minimum_acceptance_displacement_atr
            and self._volume_ratio(bar) >= self.config.minimum_volume_ratio
            and flow_ok
        )

    def _reacceptance_retest_rejected(self, pending: PendingAcceptanceFailure, bar: FlowBar) -> bool:
        tolerance = self.config.retest_tolerance_atr * self._atr
        buffer = self.config.failure_buffer_atr * self._atr
        body = abs(bar.close - bar.open) / max(self._atr, 1e-12)
        if pending.breach_direction == "UP":
            touched = bar.low <= pending.level.price + tolerance
            defended = bar.close >= pending.level.price + buffer and bar.close > bar.open
            flow_ok = bar.flow_imbalance >= self.config.directional_imbalance
        else:
            touched = bar.high >= pending.level.price - tolerance
            defended = bar.close <= pending.level.price - buffer and bar.close < bar.open
            flow_ok = bar.flow_imbalance <= -self.config.directional_imbalance
        if not self.config.use_flow_confirmation:
            flow_ok = True
        return touched and defended and body >= self.config.minimum_failure_displacement_atr and flow_ok

    def _reacceptance_invalidated(self, pending: PendingAcceptanceFailure, bar: FlowBar) -> bool:
        buffer = self.config.failure_buffer_atr * self._atr
        if pending.breach_direction == "UP":
            return bar.close <= pending.level.price - buffer
        return bar.close >= pending.level.price + buffer

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
        extension = 0.5 if self.config.use_half_range_extension else 1.0
        if pending.breach_direction == "UP":
            side = "BUY"
            failure_anchor = pending.failure_low if pending.failure_low is not None else pending.level.price
            stop = min(failure_anchor, pending.level.price) - self.config.stop_buffer_atr * atr
            target = source.high + extension * source.width
        else:
            side = "SELL"
            failure_anchor = pending.failure_high if pending.failure_high is not None else pending.level.price
            stop = max(failure_anchor, pending.level.price) + self.config.stop_buffer_atr * atr
            target = source.low - extension * source.width
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
            "target_model": "HALF_RANGE_EXTENSION" if self.config.use_half_range_extension else "FULL_RANGE_EXTENSION",
        }
        geometry_ok = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry_ok:
            return None, "MEASURED_EXPANSION_NOT_BEYOND_CONTINUATION_ENTRY_WITH_VALID_STOP", diagnostic
        cost = self.config.composite_cost_per_fill
        price_risk = abs(entry - stop)
        if price_risk < 2.0 * cost * entry:
            diagnostic["price_risk"] = price_risk
            return None, "PRICE_RISK_TOO_SMALL_RELATIVE_TO_ROUND_TRIP_COST", diagnostic
        risk = price_risk + cost * entry + cost * stop
        reward = abs(target - entry) - cost * entry - cost * target
        diagnostic.update({"price_risk": price_risk, "net_risk_per_unit": risk, "net_reward_per_unit": reward})
        if risk <= 0.0 or reward <= 0.0:
            return None, "MEASURED_EXPANSION_HAS_NONPOSITIVE_REWARD_AFTER_COST", diagnostic
        net_rr = reward / risk
        diagnostic["net_reward_to_risk"] = net_rr
        if net_rr < self.config.minimum_net_reward_to_risk:
            return None, "MEASURED_EXPANSION_NET_REWARD_TO_RISK_BELOW_GATE", diagnostic
        reason = "FAILED_FAILURE_REACCEPTANCE_CONTINUATION_TO_SESSION_RANGE_EXPANSION"
        signal = Signal(
            scenario_id=pending.scenario_id,
            branch="CONTINUATION",
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
                "breakout_level": pending.level.price,
                "accepted_extreme": pending.extreme,
                "failure_high": pending.failure_high,
                "failure_low": pending.failure_low,
                "approach_efficiency": pending.approach_efficiency,
                "approach_flow": pending.approach_flow,
            },
        )
        return signal, reason, diagnostic
