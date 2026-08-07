"""Second microstructure family: aggressor exhaustion and fair-value return.

The existing external-pool AR/EAC engine remains unchanged.  This module adds a
separate state for intervals where price is far from a causally known 15-minute
VWAP, same-direction aggressive flow remains extreme, but marginal price impact
collapses.  Only an opposite-flow structure break may convert that exhaustion
into a retracement order toward the frozen pre-confirmation VWAP.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from math import log, sqrt
from statistics import median
from typing import Literal

from microstructure import (
    SECOND_NS,
    AggressorImpactAuctionEngine,
    FlowBar,
    MicroPlan,
)

SOURCE = "AGGRESSOR_EXHAUSTION_VWAP_RETURN"


@dataclass(slots=True)
class _Exhaustion:
    impulse_direction: Literal["LONG", "SHORT"]
    reversal_direction: Literal["LONG", "SHORT"]
    detected_ts_ns: int
    frozen_vwap: float
    extreme: float
    deviation_score: float
    flow_score: float
    impact_decay: float
    age: int = 0


class VWAPExhaustionEngine:
    """Causal exhaustion detector independent of execution and account state."""

    def __init__(
        self,
        instrument_id: str,
        *,
        effective_maker_rate: float = 0.0004,
        effective_taker_rate: float = 0.0008,
        minimum_net_r: float = 1.25,
    ) -> None:
        self.instrument_id = str(instrument_id)
        self.effective_maker_rate = float(effective_maker_rate)
        self.effective_taker_rate = float(effective_taker_rate)
        self.minimum_net_r = max(1.25, float(minimum_net_r))
        self.bars: deque[FlowBar] = deque(maxlen=7200)
        self.active: _Exhaustion | None = None
        self.pending_plan_id: str | None = None
        self.position_open = False
        self.cooldown_until_ns = -1
        self.sequence = 0
        self.skips: Counter[str] = Counter()
        self.events: list[dict[str, object]] = []

    def _record(self, event_type: str, ts_ns: int, **details: object) -> None:
        self.events.append({
            "type": event_type,
            "instrument_id": self.instrument_id,
            "observed_ts_ns": int(ts_ns),
            **details,
        })
        if len(self.events) > 20_000:
            self.events = self.events[-20_000:]

    def _return_rms(self, lookback: int = 900) -> float | None:
        if len(self.bars) < lookback + 2:
            return None
        sample = list(self.bars)[-(lookback + 2):-1]
        values = [log(curr.close / prev.close) for prev, curr in zip(sample, sample[1:])]
        return sqrt(sum(value * value for value in values) / len(values)) if values else None

    def _flow_rms(self, lookback: int = 900) -> float | None:
        if len(self.bars) < lookback + 1:
            return None
        values = [bar.signed_notional for bar in list(self.bars)[-(lookback + 1):-1]]
        return sqrt(sum(value * value for value in values) / len(values)) if values else None

    def _vwap(self, seconds: int, exclude_current: bool = True) -> float | None:
        required = seconds + int(exclude_current)
        if len(self.bars) < required:
            return None
        sample = list(self.bars)[-required:-1] if exclude_current else list(self.bars)[-seconds:]
        quote = sum(bar.quote_notional for bar in sample)
        volume = sum(bar.volume for bar in sample)
        if quote <= 0 or volume <= 0:
            return median(bar.close for bar in sample)
        return quote / volume

    def _atr_60(self) -> float | None:
        if len(self.bars) < 301:
            return None
        sample = list(self.bars)[-301:]
        values: list[float] = []
        previous_close = sample[0].close
        for start in range(1, 301, 60):
            part = sample[start:start + 60]
            if len(part) < 60:
                continue
            high = max(bar.high for bar in part)
            low = min(bar.low for bar in part)
            values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
            previous_close = part[-1].close
        return sum(values) / len(values) if values else None

    def _window_score(
        self,
        direction: Literal["LONG", "SHORT"],
        seconds: int,
        return_rms: float,
        flow_rms: float,
    ) -> tuple[float, float]:
        sample = list(self.bars)[-(seconds + 1):]
        sign = 1.0 if direction == "LONG" else -1.0
        move = sign * log(sample[-1].close / sample[0].close)
        flow = sign * sum(bar.signed_notional for bar in sample[1:])
        return (
            move / max(return_rms * sqrt(seconds), 1e-12),
            flow / max(flow_rms * sqrt(seconds), 1e-12),
        )

    def _impact_decay(
        self,
        direction: Literal["LONG", "SHORT"],
        return_rms: float,
        flow_rms: float,
    ) -> float | None:
        if len(self.bars) < 31:
            return None
        sample = list(self.bars)[-31:]
        sign = 1.0 if direction == "LONG" else -1.0
        first = sample[:16]
        second = sample[15:]

        def efficiency(part: list[FlowBar]) -> tuple[float, float, float]:
            move = sign * log(part[-1].close / part[0].close)
            flow = sign * sum(bar.signed_notional for bar in part[1:])
            move_score = move / max(return_rms * sqrt(15), 1e-12)
            flow_score = flow / max(flow_rms * sqrt(15), 1e-12)
            return move_score, flow_score, move_score / max(flow_score, 1e-12)

        first_move, first_flow, first_efficiency = efficiency(first)
        second_move, second_flow, second_efficiency = efficiency(second)
        if first_flow < 0.75 or second_flow < 0.75 or first_move <= 0:
            return None
        # Same-direction flow persists, but the second half buys/sells much less
        # directional price progress per standardized unit of aggressive flow.
        return second_efficiency / max(first_efficiency, 1e-12)

    def _detect(self, bar: FlowBar, return_rms: float, flow_rms: float, atr: float) -> None:
        if self.active is not None or bar.ts_ns < self.cooldown_until_ns:
            return
        if self.pending_plan_id is not None or self.position_open:
            return
        vwap = self._vwap(900, exclude_current=True)
        if vwap is None:
            return
        deviation = log(bar.close / vwap)
        deviation_score = deviation / max(return_rms * sqrt(900), 1e-12)
        if abs(deviation_score) < 1.25:
            return
        impulse_direction: Literal["LONG", "SHORT"] = "LONG" if deviation_score > 0 else "SHORT"
        move_score, flow_score = self._window_score(impulse_direction, 30, return_rms, flow_rms)
        decay = self._impact_decay(impulse_direction, return_rms, flow_rms)
        if move_score < 1.0 or flow_score < 2.0 or decay is None or decay > 0.55:
            return
        sample = list(self.bars)[-16:]
        if impulse_direction == "LONG":
            extreme = max(value.high for value in sample)
            rejection = (extreme - bar.close) >= 0.05 * atr
            reversal: Literal["LONG", "SHORT"] = "SHORT"
        else:
            extreme = min(value.low for value in sample)
            rejection = (bar.close - extreme) >= 0.05 * atr
            reversal = "LONG"
        if not rejection:
            return
        self.active = _Exhaustion(
            impulse_direction=impulse_direction,
            reversal_direction=reversal,
            detected_ts_ns=bar.ts_ns,
            frozen_vwap=vwap,
            extreme=extreme,
            deviation_score=deviation_score,
            flow_score=flow_score,
            impact_decay=decay,
        )
        self._record(
            "AGGRESSOR_EXHAUSTION_DETECTED",
            bar.ts_ns,
            impulse_direction=impulse_direction,
            reversal_direction=reversal,
            frozen_vwap=vwap,
            extreme=extreme,
            deviation_score=deviation_score,
            flow_score=flow_score,
            impact_decay=decay,
        )

    def _confirmation_scores(
        self,
        direction: Literal["LONG", "SHORT"],
        return_rms: float,
        flow_rms: float,
    ) -> tuple[float, float]:
        return self._window_score(direction, 5, return_rms, flow_rms)

    def _costed_plan(
        self,
        bar: FlowBar,
        active: _Exhaustion,
        atr: float,
        move_score: float,
        flow_score: float,
    ) -> MicroPlan | None:
        direction = active.reversal_direction
        entry = (bar.open + bar.close) / 2.0
        stop = (
            active.extreme - 0.08 * atr
            if direction == "LONG"
            else active.extreme + 0.08 * atr
        )
        target = active.frozen_vwap
        valid = stop < entry < target if direction == "LONG" else target < entry < stop
        if not valid:
            self.skips["EXHAUSTION_NON_CAUSAL_PRICE_ORDER"] += 1
            return None
        stop_distance = abs(entry - stop)
        if not 0.08 * atr <= stop_distance <= 1.50 * atr:
            self.skips["EXHAUSTION_STOP_GEOMETRY"] += 1
            return None
        loss_per_unit = (
            stop_distance
            + entry * self.effective_maker_rate
            + stop * self.effective_taker_rate
        )
        reward = (
            abs(target - entry)
            - entry * self.effective_maker_rate
            - target * self.effective_maker_rate
        )
        net_r = reward / max(loss_per_unit, 1e-12)
        if reward <= 0 or net_r < self.minimum_net_r:
            self.skips["EXHAUSTION_INSUFFICIENT_COSTED_R"] += 1
            return None
        self.sequence += 1
        scenario_id = f"{self.instrument_id}-VEA-{bar.ts_ns}-{self.sequence:07d}"
        return MicroPlan(
            scenario_id=scenario_id,
            scenario="AR",
            direction=direction,
            observed_ts_ns=bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            loss_per_unit=loss_per_unit,
            net_r=net_r,
            expire_ts_ns=bar.ts_ns + 20 * SECOND_NS,
            details={
                "source": SOURCE,
                "impulse_direction": active.impulse_direction,
                "detected_ts_ns": active.detected_ts_ns,
                "frozen_vwap": active.frozen_vwap,
                "exhaustion_extreme": active.extreme,
                "deviation_score": active.deviation_score,
                "impulse_flow_score": active.flow_score,
                "impact_decay": active.impact_decay,
                "confirmation_move_score": move_score,
                "confirmation_flow_score": flow_score,
                "entry_cost_assumption": "MAKER",
            },
        )

    def _update_active(self, bar: FlowBar, return_rms: float, flow_rms: float, atr: float) -> MicroPlan | None:
        active = self.active
        if active is None:
            return None
        active.age += 1
        if active.impulse_direction == "LONG":
            active.extreme = max(active.extreme, bar.high)
        else:
            active.extreme = min(active.extreme, bar.low)
        direction = active.reversal_direction
        move_score, flow_score = self._confirmation_scores(direction, return_rms, flow_rms)
        previous = list(self.bars)[-11:-1]
        structure = (
            bar.close > max(value.high for value in previous)
            if direction == "LONG"
            else bar.close < min(value.low for value in previous)
        ) if previous else False
        distance_now = abs(log(bar.close / active.frozen_vwap))
        distance_at_detection = abs(log(
            (active.extreme if active.impulse_direction == "LONG" else active.extreme)
            / active.frozen_vwap
        ))
        mean_reentry = distance_now <= 0.85 * max(distance_at_detection, 1e-12)
        if structure and mean_reentry and move_score >= 0.90 and flow_score >= 0.75:
            plan = self._costed_plan(bar, active, atr, move_score, flow_score)
            if plan is not None:
                self._record(
                    "AGGRESSOR_EXHAUSTION_PLAN_EMITTED",
                    bar.ts_ns,
                    scenario_id=plan.scenario_id,
                    direction=plan.direction,
                    net_r=plan.net_r,
                )
                self.active = None
                return plan
        if active.age > 60:
            self.skips["EXHAUSTION_CONFIRMATION_EXPIRED"] += 1
            self._record(
                "AGGRESSOR_EXHAUSTION_TERMINATED",
                bar.ts_ns,
                reason="EXHAUSTION_CONFIRMATION_EXPIRED",
            )
            self.active = None
        return None

    def on_bar(self, bar: FlowBar) -> MicroPlan | None:
        if self.bars and bar.ts_ns <= self.bars[-1].ts_ns:
            raise ValueError("VWAP exhaustion bars must be strictly increasing")
        self.bars.append(bar)
        return_rms = self._return_rms()
        flow_rms = self._flow_rms()
        atr = self._atr_60()
        if return_rms is None or flow_rms is None or atr is None:
            return None
        plan = self._update_active(bar, return_rms, flow_rms, atr)
        if plan is not None:
            return plan
        self._detect(bar, return_rms, flow_rms, atr)
        return None

    def mark_submitted(self, plan: MicroPlan) -> None:
        self.pending_plan_id = plan.scenario_id
        self._record("EXHAUSTION_PLAN_SUBMITTED", plan.observed_ts_ns, scenario_id=plan.scenario_id)

    def mark_rejected(self, plan: MicroPlan, ts_ns: int, reason: str) -> None:
        self.pending_plan_id = None
        self.position_open = False
        self.cooldown_until_ns = int(ts_ns) + 60 * SECOND_NS
        self.skips[str(reason)] += 1
        self._record("EXHAUSTION_PLAN_REJECTED", ts_ns, scenario_id=plan.scenario_id, reason=str(reason))

    def mark_entry_filled(self, ts_ns: int) -> None:
        if self.pending_plan_id is None:
            return
        self.position_open = True
        self._record("EXHAUSTION_ENTRY_FILLED", ts_ns, scenario_id=self.pending_plan_id)

    def mark_terminal(self, ts_ns: int, reason: str) -> None:
        scenario_id = self.pending_plan_id
        self.pending_plan_id = None
        self.position_open = False
        self.cooldown_until_ns = int(ts_ns) + 120 * SECOND_NS
        self._record("EXHAUSTION_TRADE_TERMINAL", ts_ns, scenario_id=scenario_id, reason=str(reason))


class CombinedMicrostructureEngine:
    """Arbitrate independent pool-impact and VWAP-exhaustion plans."""

    def __init__(self, instrument_id: str, **kwargs: object) -> None:
        self.pool = AggressorImpactAuctionEngine(instrument_id, **kwargs)
        self.exhaustion = VWAPExhaustionEngine(instrument_id, **kwargs)
        self.owner: Literal["POOL", "EXHAUSTION"] | None = None

    @property
    def events(self) -> list[dict[str, object]]:
        merged = list(self.pool.events) + list(self.exhaustion.events)
        return sorted(merged, key=lambda value: (int(value.get("observed_ts_ns", -1)), str(value.get("type", ""))))

    @property
    def skips(self) -> Counter[str]:
        result: Counter[str] = Counter(self.pool.skips)
        result.update(self.exhaustion.skips)
        return result

    def on_bar(self, bar: FlowBar) -> MicroPlan | None:
        pool_plan = self.pool.on_bar(bar)
        exhaustion_plan = self.exhaustion.on_bar(bar)
        if pool_plan is None:
            return exhaustion_plan
        if exhaustion_plan is None:
            return pool_plan
        if pool_plan.net_r >= exhaustion_plan.net_r:
            self.exhaustion.mark_rejected(
                exhaustion_plan,
                bar.ts_ns,
                "LOWER_MICROSTRUCTURE_PRIORITY",
            )
            return pool_plan
        self.pool.mark_rejected(pool_plan, bar.ts_ns, "LOWER_MICROSTRUCTURE_PRIORITY")
        return exhaustion_plan

    @staticmethod
    def _is_exhaustion(plan: MicroPlan) -> bool:
        return plan.details.get("source") == SOURCE

    def mark_submitted(self, plan: MicroPlan) -> None:
        if self._is_exhaustion(plan):
            self.owner = "EXHAUSTION"
            self.exhaustion.mark_submitted(plan)
        else:
            self.owner = "POOL"
            self.pool.mark_submitted(plan)

    def mark_rejected(self, plan: MicroPlan, ts_ns: int, reason: str) -> None:
        if self._is_exhaustion(plan):
            self.exhaustion.mark_rejected(plan, ts_ns, reason)
        else:
            self.pool.mark_rejected(plan, ts_ns, reason)

    def mark_entry_filled(self, ts_ns: int) -> None:
        if self.owner == "EXHAUSTION":
            self.exhaustion.mark_entry_filled(ts_ns)
        elif self.owner == "POOL":
            self.pool.mark_entry_filled(ts_ns)

    def mark_terminal(self, ts_ns: int, reason: str) -> None:
        try:
            if self.owner == "EXHAUSTION":
                self.exhaustion.mark_terminal(ts_ns, reason)
            elif self.owner == "POOL":
                self.pool.mark_terminal(ts_ns, reason)
        finally:
            self.owner = None
