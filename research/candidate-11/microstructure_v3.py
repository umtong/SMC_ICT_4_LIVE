"""Third microstructure family: balance acceptance and measured move.

A completed five-minute auction is registered as a balance only when its path is
rotational rather than directional and its range is sensible relative to prior
realized volatility.  A balance is consumed on its first efficient aggressor
breakout.  The breakout must establish acceptance with multiple closes, survive
a low-opposition retest, and reaccelerate before a maker retracement order is
allowed.  The target is the frozen balance-height extension; no future pool or
post-event parameter is used.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from math import log, sqrt
from statistics import median
from typing import Literal

from microstructure import SECOND_NS, FlowBar, MicroPlan
from microstructure_v2 import CombinedMicrostructureEngine

SOURCE = "BALANCE_ACCEPTANCE_MEASURED_MOVE"


@dataclass(slots=True)
class _BuildingBalance:
    block_id: int
    start_ts_ns: int
    open: float
    high: float
    low: float
    close: float
    quote_notional: float
    volume: float
    absolute_log_path: float = 0.0
    previous_close: float | None = None


@dataclass(slots=True)
class _Balance:
    start_ts_ns: int
    completed_ts_ns: int
    open: float
    high: float
    low: float
    close: float
    vwap: float
    range: float
    path_efficiency: float
    consumed: bool = False


@dataclass(slots=True)
class _Acceptance:
    balance: _Balance
    direction: Literal["LONG", "SHORT"]
    boundary: float
    breakout_ts_ns: int
    breakout_extreme: float
    breakout_move_score: float
    breakout_flow_score: float
    breakout_impact_efficiency: float
    phase: str = "WAIT_ACCEPTANCE"
    age: int = 0
    outside_closes: int = 1
    retest_extreme: float | None = None
    retest_ts_ns: int | None = None


class BalanceAcceptanceEngine:
    """Causal detector independent of order, account, and portfolio state."""

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
        self.building: _BuildingBalance | None = None
        self.balances: list[_Balance] = []
        self.active: _Acceptance | None = None
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

    def _atr_60_excluding_current(self) -> float | None:
        if len(self.bars) < 302:
            return None
        sample = list(self.bars)[-302:-1]
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

    def _complete_balance(self, completed_ts_ns: int, atr: float | None) -> None:
        building = self.building
        if building is None or atr is None or building.volume <= 0:
            return
        range_value = building.high - building.low
        range_atr = range_value / atr
        net = abs(log(building.close / building.open))
        efficiency = net / max(building.absolute_log_path, 1e-12)
        if not 0.50 <= range_atr <= 3.50:
            self.skips["FIVE_MINUTE_RANGE_NOT_BALANCED"] += 1
            return
        if efficiency > 0.35:
            self.skips["FIVE_MINUTE_PATH_DIRECTIONAL"] += 1
            return
        balance = _Balance(
            start_ts_ns=building.start_ts_ns,
            completed_ts_ns=completed_ts_ns,
            open=building.open,
            high=building.high,
            low=building.low,
            close=building.close,
            vwap=building.quote_notional / building.volume,
            range=range_value,
            path_efficiency=efficiency,
        )
        self.balances.append(balance)
        if len(self.balances) > 48:
            self.balances = self.balances[-48:]
        self._record(
            "FIVE_MINUTE_BALANCE_COMPLETED",
            completed_ts_ns,
            start_ts_ns=balance.start_ts_ns,
            high=balance.high,
            low=balance.low,
            vwap=balance.vwap,
            range_atr=range_atr,
            path_efficiency=efficiency,
        )

    def _roll_balance(self, bar: FlowBar, atr: float | None) -> None:
        block_id = (bar.ts_ns - 1) // (300 * SECOND_NS)
        current = self.building
        if current is None:
            self.building = _BuildingBalance(
                block_id=block_id,
                start_ts_ns=bar.ts_ns,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                quote_notional=bar.quote_notional,
                volume=bar.volume,
                previous_close=bar.close,
            )
            return
        if current.block_id != block_id:
            self._complete_balance(bar.ts_ns, atr)
            self.building = _BuildingBalance(
                block_id=block_id,
                start_ts_ns=bar.ts_ns,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                quote_notional=bar.quote_notional,
                volume=bar.volume,
                previous_close=bar.close,
            )
            return
        if current.previous_close is not None and current.previous_close > 0 and bar.close > 0:
            current.absolute_log_path += abs(log(bar.close / current.previous_close))
        current.previous_close = bar.close
        current.high = max(current.high, bar.high)
        current.low = min(current.low, bar.low)
        current.close = bar.close
        current.quote_notional += bar.quote_notional
        current.volume += bar.volume

    def _latest_balance(self, bar: FlowBar, atr: float) -> _Balance | None:
        for balance in reversed(self.balances):
            if balance.consumed or balance.completed_ts_ns >= bar.ts_ns:
                continue
            if (bar.ts_ns - balance.completed_ts_ns) > 3600 * SECOND_NS:
                continue
            if balance.low - 1.5 * atr <= bar.open <= balance.high + 1.5 * atr:
                return balance
        return None

    def _detect_breakout(
        self,
        bar: FlowBar,
        atr: float,
        return_rms: float,
        flow_rms: float,
    ) -> None:
        if self.active is not None or bar.ts_ns < self.cooldown_until_ns:
            return
        if self.pending_plan_id is not None or self.position_open:
            return
        balance = self._latest_balance(bar, atr)
        if balance is None:
            return
        direction: Literal["LONG", "SHORT"] | None = None
        boundary = 0.0
        penetration = 0.0
        if bar.close >= balance.high + 0.03 * atr:
            direction = "LONG"
            boundary = balance.high
            penetration = (bar.close - balance.high) / atr
        elif bar.close <= balance.low - 0.03 * atr:
            direction = "SHORT"
            boundary = balance.low
            penetration = (balance.low - bar.close) / atr
        if direction is None:
            return
        move_score, flow_score = self._window_score(direction, 5, return_rms, flow_rms)
        impact = move_score / max(flow_score, 1e-12)
        if move_score < 1.0 or flow_score < 1.5 or impact < 0.45:
            return
        balance.consumed = True
        extreme = bar.high if direction == "LONG" else bar.low
        self.active = _Acceptance(
            balance=balance,
            direction=direction,
            boundary=boundary,
            breakout_ts_ns=bar.ts_ns,
            breakout_extreme=extreme,
            breakout_move_score=move_score,
            breakout_flow_score=flow_score,
            breakout_impact_efficiency=impact,
        )
        self._record(
            "BALANCE_FIRST_BREAKOUT_ACCEPTED_FOR_TEST",
            bar.ts_ns,
            direction=direction,
            boundary=boundary,
            balance_start_ts_ns=balance.start_ts_ns,
            balance_completed_ts_ns=balance.completed_ts_ns,
            balance_range=balance.range,
            penetration_atr=penetration,
            breakout_move_score=move_score,
            breakout_flow_score=flow_score,
            breakout_impact_efficiency=impact,
        )

    def _costed_plan(
        self,
        bar: FlowBar,
        event: _Acceptance,
        atr: float,
        move_score: float,
        flow_score: float,
    ) -> MicroPlan | None:
        direction = event.direction
        entry = event.boundary + (0.01 * atr if direction == "LONG" else -0.01 * atr)
        if event.retest_extreme is None:
            return None
        stop = (
            event.retest_extreme - 0.08 * atr
            if direction == "LONG"
            else event.retest_extreme + 0.08 * atr
        )
        target = (
            event.balance.high + event.balance.range
            if direction == "LONG"
            else event.balance.low - event.balance.range
        )
        valid = stop < entry < target if direction == "LONG" else target < entry < stop
        if not valid:
            self.skips["BALANCE_NON_CAUSAL_PRICE_ORDER"] += 1
            return None
        stop_distance = abs(entry - stop)
        if not 0.08 * atr <= stop_distance <= 1.50 * atr:
            self.skips["BALANCE_STOP_GEOMETRY"] += 1
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
            self.skips["BALANCE_INSUFFICIENT_COSTED_R"] += 1
            return None
        self.sequence += 1
        scenario_id = f"{self.instrument_id}-BAM-{bar.ts_ns}-{self.sequence:07d}"
        return MicroPlan(
            scenario_id=scenario_id,
            scenario="EAC",
            direction=direction,
            observed_ts_ns=bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            loss_per_unit=loss_per_unit,
            net_r=net_r,
            expire_ts_ns=bar.ts_ns + 30 * SECOND_NS,
            details={
                "source": SOURCE,
                "balance_start_ts_ns": event.balance.start_ts_ns,
                "balance_completed_ts_ns": event.balance.completed_ts_ns,
                "balance_high": event.balance.high,
                "balance_low": event.balance.low,
                "balance_vwap": event.balance.vwap,
                "balance_range": event.balance.range,
                "balance_path_efficiency": event.balance.path_efficiency,
                "breakout_ts_ns": event.breakout_ts_ns,
                "breakout_extreme": event.breakout_extreme,
                "retest_ts_ns": event.retest_ts_ns,
                "retest_extreme": event.retest_extreme,
                "breakout_move_score": event.breakout_move_score,
                "breakout_flow_score": event.breakout_flow_score,
                "breakout_impact_efficiency": event.breakout_impact_efficiency,
                "confirmation_move_score": move_score,
                "confirmation_flow_score": flow_score,
                "target_method": "FROZEN_BALANCE_HEIGHT_EXTENSION",
                "entry_cost_assumption": "MAKER",
            },
        )

    def _update_active(
        self,
        bar: FlowBar,
        atr: float,
        return_rms: float,
        flow_rms: float,
    ) -> MicroPlan | None:
        event = self.active
        if event is None:
            return None
        event.age += 1
        if event.direction == "LONG":
            event.breakout_extreme = max(event.breakout_extreme, bar.high)
            accepted_close = bar.close >= event.boundary + 0.01 * atr
            invalid = bar.close < event.balance.low
        else:
            event.breakout_extreme = min(event.breakout_extreme, bar.low)
            accepted_close = bar.close <= event.boundary - 0.01 * atr
            invalid = bar.close > event.balance.high
        if invalid:
            self.skips["BALANCE_BREAKOUT_FULL_FAILURE"] += 1
            self._record("BALANCE_ACCEPTANCE_TERMINATED", bar.ts_ns, reason="BALANCE_BREAKOUT_FULL_FAILURE")
            self.active = None
            return None

        if event.phase == "WAIT_ACCEPTANCE":
            if accepted_close:
                event.outside_closes += 1
            if event.outside_closes >= 2:
                event.phase = "WAIT_RETEST"
                self._record(
                    "BALANCE_BREAKOUT_MULTI_CLOSE_ACCEPTED",
                    bar.ts_ns,
                    direction=event.direction,
                    boundary=event.boundary,
                    outside_closes=event.outside_closes,
                )
            elif event.age > 15:
                self.skips["BALANCE_ACCEPTANCE_EXPIRED"] += 1
                self.active = None
            return None

        if event.phase == "WAIT_RETEST":
            if event.direction == "LONG":
                retest = bar.low <= event.boundary + 0.15 * atr and bar.close >= event.boundary + 0.005 * atr
                deep_failure = bar.low < event.boundary - 0.30 * atr
                candidate_extreme = bar.low
                opposite: Literal["LONG", "SHORT"] = "SHORT"
            else:
                retest = bar.high >= event.boundary - 0.15 * atr and bar.close <= event.boundary - 0.005 * atr
                deep_failure = bar.high > event.boundary + 0.30 * atr
                candidate_extreme = bar.high
                opposite = "LONG"
            if deep_failure:
                self.skips["BALANCE_RETEST_INVALIDATED"] += 1
                self.active = None
                return None
            if retest:
                _, opposition = self._window_score(opposite, 5, return_rms, flow_rms)
                if opposition <= 0.75:
                    event.retest_extreme = candidate_extreme
                    event.retest_ts_ns = bar.ts_ns
                    event.phase = "WAIT_REACCELERATION"
                    self._record(
                        "BALANCE_BOUNDARY_RETEST_HELD",
                        bar.ts_ns,
                        direction=event.direction,
                        boundary=event.boundary,
                        opposition_flow_score=opposition,
                        retest_extreme=candidate_extreme,
                    )
            if event.age > 120:
                self.skips["BALANCE_RETEST_EXPIRED"] += 1
                self.active = None
            return None

        if event.phase == "WAIT_REACCELERATION":
            move_score, flow_score = self._window_score(event.direction, 5, return_rms, flow_rms)
            previous = list(self.bars)[-9:-1]
            structure = (
                bar.close > max(value.high for value in previous)
                if event.direction == "LONG"
                else bar.close < min(value.low for value in previous)
            ) if previous else False
            if structure and move_score >= 0.75 and flow_score >= 0.75:
                plan = self._costed_plan(bar, event, atr, move_score, flow_score)
                if plan is not None:
                    self._record(
                        "BALANCE_MEASURED_MOVE_PLAN_EMITTED",
                        bar.ts_ns,
                        scenario_id=plan.scenario_id,
                        direction=plan.direction,
                        net_r=plan.net_r,
                    )
                    self.active = None
                    return plan
            if event.age > 180:
                self.skips["BALANCE_REACCELERATION_EXPIRED"] += 1
                self.active = None
        return None

    def on_bar(self, bar: FlowBar) -> MicroPlan | None:
        if self.bars and bar.ts_ns <= self.bars[-1].ts_ns:
            raise ValueError("balance-acceptance bars must be strictly increasing")
        self.bars.append(bar)
        return_rms = self._return_rms()
        flow_rms = self._flow_rms()
        atr = self._atr_60_excluding_current()
        self._roll_balance(bar, atr)
        if return_rms is None or flow_rms is None or atr is None:
            return None
        plan = self._update_active(bar, atr, return_rms, flow_rms)
        if plan is not None:
            return plan
        self._detect_breakout(bar, atr, return_rms, flow_rms)
        return None

    def mark_submitted(self, plan: MicroPlan) -> None:
        self.pending_plan_id = plan.scenario_id
        self._record("BALANCE_PLAN_SUBMITTED", plan.observed_ts_ns, scenario_id=plan.scenario_id)

    def mark_rejected(self, plan: MicroPlan, ts_ns: int, reason: str) -> None:
        self.pending_plan_id = None
        self.position_open = False
        self.cooldown_until_ns = int(ts_ns) + 60 * SECOND_NS
        self.skips[str(reason)] += 1
        self._record("BALANCE_PLAN_REJECTED", ts_ns, scenario_id=plan.scenario_id, reason=str(reason))

    def mark_entry_filled(self, ts_ns: int) -> None:
        if self.pending_plan_id is None:
            return
        self.position_open = True
        self._record("BALANCE_ENTRY_FILLED", ts_ns, scenario_id=self.pending_plan_id)

    def mark_terminal(self, ts_ns: int, reason: str) -> None:
        scenario_id = self.pending_plan_id
        self.pending_plan_id = None
        self.position_open = False
        self.cooldown_until_ns = int(ts_ns) + 120 * SECOND_NS
        self._record("BALANCE_TRADE_TERMINAL", ts_ns, scenario_id=scenario_id, reason=str(reason))


class CombinedMicrostructureV3Engine:
    """Arbitrate pool impact, VWAP exhaustion, and balance acceptance."""

    def __init__(self, instrument_id: str, **kwargs: object) -> None:
        self.existing = CombinedMicrostructureEngine(instrument_id, **kwargs)
        self.balance = BalanceAcceptanceEngine(instrument_id, **kwargs)
        self.owner: Literal["EXISTING", "BALANCE"] | None = None

    @property
    def events(self) -> list[dict[str, object]]:
        return sorted(
            list(self.existing.events) + list(self.balance.events),
            key=lambda value: (int(value.get("observed_ts_ns", -1)), str(value.get("type", ""))),
        )

    @property
    def skips(self) -> Counter[str]:
        result = Counter(self.existing.skips)
        result.update(self.balance.skips)
        return result

    def on_bar(self, bar: FlowBar) -> MicroPlan | None:
        existing_plan = self.existing.on_bar(bar)
        balance_plan = self.balance.on_bar(bar)
        if existing_plan is None:
            return balance_plan
        if balance_plan is None:
            return existing_plan
        if existing_plan.net_r >= balance_plan.net_r:
            self.balance.mark_rejected(balance_plan, bar.ts_ns, "LOWER_MICROSTRUCTURE_PRIORITY")
            return existing_plan
        self.existing.mark_rejected(existing_plan, bar.ts_ns, "LOWER_MICROSTRUCTURE_PRIORITY")
        return balance_plan

    @staticmethod
    def _is_balance(plan: MicroPlan) -> bool:
        return plan.details.get("source") == SOURCE

    def mark_submitted(self, plan: MicroPlan) -> None:
        if self._is_balance(plan):
            self.owner = "BALANCE"
            self.balance.mark_submitted(plan)
        else:
            self.owner = "EXISTING"
            self.existing.mark_submitted(plan)

    def mark_rejected(self, plan: MicroPlan, ts_ns: int, reason: str) -> None:
        if self._is_balance(plan):
            self.balance.mark_rejected(plan, ts_ns, reason)
        else:
            self.existing.mark_rejected(plan, ts_ns, reason)

    def mark_entry_filled(self, ts_ns: int) -> None:
        if self.owner == "BALANCE":
            self.balance.mark_entry_filled(ts_ns)
        elif self.owner == "EXISTING":
            self.existing.mark_entry_filled(ts_ns)

    def mark_terminal(self, ts_ns: int, reason: str) -> None:
        try:
            if self.owner == "BALANCE":
                self.balance.mark_terminal(ts_ns, reason)
            elif self.owner == "EXISTING":
                self.existing.mark_terminal(ts_ns, reason)
        finally:
            self.owner = None
