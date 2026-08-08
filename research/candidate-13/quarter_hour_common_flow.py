#!/usr/bin/env python3
"""Quarter-hour common-flow follower continuation logic for Candidate 13 V9."""
from __future__ import annotations
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
import statistics
from typing import Any, Mapping
from logic import BarObs, Direction, LogicConfig, MINUTE_NS, ResearchEvent, Scenario, TradePlan
QH_LOGIC_KEY = "PORTFOLIO::QUARTER_HOUR_COMMON_FLOW"
QH_MODULE = "QUARTER_HOUR_COMMON_FLOW_CONTINUATION"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
@dataclass(frozen=True, slots=True)
class FiveMinuteImpulse:
    symbol: str
    start_ts_ns: int
    end_ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    atr: float

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def direction(self) -> Direction | None:
        if self.body > 0.0:
            return Direction.LONG
        if self.body < 0.0:
            return Direction.SHORT
        return None

    @property
    def signed_flow(self) -> float:
        if self.volume <= 0.0:
            return 0.0
        return max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))

    @property
    def standardized_body(self) -> float:
        return abs(self.body) / max(self.atr, self.close * 1e-12)

class QuarterHourCommonFlowEngine:
    """Detect market-wide quarter-hour impulses and emit follower retest plans."""

    def __init__(self, config: LogicConfig, instrument_id: str='PORTFOLIO.GLOBAL') -> None:
        self.config = config
        self.instrument_id = instrument_id
        self.events: list[ResearchEvent] = []
        self.skips: Counter[str] = Counter()
        self._bars: dict[str, deque[BarObs]] = {symbol: deque(maxlen=max(90, config.atr_period + 10)) for symbol in SYMBOLS}
        self._states: dict[str, str] = {}
        self._active_scenario_id: str | None = None
        self._sequence = 0
        self._last_window_end_ns = -1

    @staticmethod
    def _ts(value: Any) -> int:
        return int(getattr(value, 'ts_ns', value))

    def _event(self, *, scenario_id: str, event_type: str, event_time_ns: int, observed_time_ns: int, previous_state: str, next_state: str, reason_code: str, reference_price: float | None, details: dict[str, Any]) -> None:
        self.events.append(ResearchEvent(scenario_id=scenario_id, instrument_id=self.instrument_id, event_type=event_type, event_time_ns=int(event_time_ns), observed_time_ns=int(observed_time_ns), previous_state=previous_state, next_state=next_state, reason_code=reason_code, reference_price=None if reference_price is None else format(reference_price, '.10f'), details=details))

    @staticmethod
    def _true_range(bar: BarObs, previous_close: float | None) -> float:
        if previous_close is None:
            return max(bar.high - bar.low, 1e-12)
        return max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close), 1e-12)

    def _atr(self, symbol: str) -> float | None:
        bars = list(self._bars[symbol])
        if len(bars) < self.config.atr_period + 1:
            return None
        ranges: list[float] = []
        for index in range(len(bars) - self.config.atr_period, len(bars)):
            previous = bars[index - 1].close if index > 0 else None
            ranges.append(self._true_range(bars[index], previous))
        return statistics.fmean(ranges) if ranges else None

    @staticmethod
    def _is_quarter_hour_window_end(ts_ns: int) -> bool:
        stamp = datetime.fromtimestamp(ts_ns / 1000000000, tz=timezone.utc)
        return stamp.second == 0 and stamp.microsecond == 0 and (stamp.minute % 15 == 5)

    def _impulse(self, symbol: str, ts_ns: int) -> FiveMinuteImpulse | None:
        bars = list(self._bars[symbol])
        if len(bars) < max(5, self.config.atr_period + 1):
            return None
        parts = bars[-5:]
        if parts[-1].ts_ns != ts_ns:
            return None
        expected = [parts[0].ts_ns + offset * MINUTE_NS for offset in range(5)]
        if [bar.ts_ns for bar in parts] != expected:
            self.skips['QH_NONCONTIGUOUS_FIVE_MINUTE_WINDOW'] += 1
            return None
        atr = self._atr(symbol)
        if atr is None or atr <= 0.0:
            return None
        return FiveMinuteImpulse(symbol=symbol, start_ts_ns=parts[0].ts_ns - MINUTE_NS, end_ts_ns=parts[-1].ts_ns, open=parts[0].open, high=max((bar.high for bar in parts)), low=min((bar.low for bar in parts)), close=parts[-1].close, volume=sum((bar.volume for bar in parts)), taker_buy_volume=sum((bar.taker_buy_volume for bar in parts)), atr=atr)

    def _qualified(self, impulse: FiveMinuteImpulse, direction: Direction) -> bool:
        same_body = impulse.body > 0.0 if direction is Direction.LONG else impulse.body < 0.0
        same_flow = impulse.signed_flow >= self.config.displacement_flow_min if direction is Direction.LONG else impulse.signed_flow <= -self.config.displacement_flow_min
        return same_body and same_flow and (impulse.standardized_body >= self.config.displacement_body_atr)

    def _plan(self, *, impulse: FiveMinuteImpulse, direction: Direction, owner: FiveMinuteImpulse, accepted: list[FiveMinuteImpulse], ts_ns: int, event_id: str) -> TradePlan | None:
        entry = (impulse.open + impulse.close) / 2.0
        allowance = self.config.stop_buffer_atr * impulse.atr
        distance = abs(impulse.close - impulse.open)
        if distance <= 0.0:
            return None
        if direction is Direction.LONG:
            stop = min(impulse.open, impulse.low) - allowance
            target = impulse.close + distance
            passive = stop < entry < impulse.close < target
        else:
            stop = max(impulse.open, impulse.high) + allowance
            target = impulse.close - distance
            passive = target < impulse.close < entry < stop
        if not passive:
            self.skips['QH_NON_CAUSAL_PRICE_ORDER'] += 1
            return None
        risk = abs(entry - stop)
        gross_gain = abs(target - entry)
        if risk <= 0.0 or risk / impulse.atr < self.config.min_stop_atr:
            self.skips['QH_STOP_DISTANCE_BELOW_EXECUTION_FLOOR'] += 1
            return None
        loss = risk + entry * self.config.effective_maker_rate + stop * self.config.effective_taker_rate
        net_gain = gross_gain - entry * self.config.effective_maker_rate - target * self.config.effective_maker_rate
        net_r = net_gain / loss if loss > 0.0 else float('-inf')
        if not isfinite(net_r) or net_gain <= 0.0 or net_r < self.config.min_net_r:
            self.skips['QH_INSUFFICIENT_COSTED_STRUCTURAL_R'] += 1
            return None
        scenario_id = f'{event_id}-{impulse.symbol}-FOLLOWER'
        accepted_symbols = tuple((item.symbol for item in accepted))
        details = {'_logic_key': QH_LOGIC_KEY, 'module': QH_MODULE, 'route': 'QUARTER_HOUR_OWNER_FOLLOWER_RETEST', 'independent_episode_key': event_id, 'clock_phase': 'UTC_QUARTER_HOUR_FIRST_FIVE_MINUTES', 'owner_symbol': owner.symbol, 'accepted_symbols': accepted_symbols, 'direction': direction.value, 'impulse_start_ts_ns': impulse.start_ts_ns, 'impulse_end_ts_ns': impulse.end_ts_ns, 'follower_standardized_body': impulse.standardized_body, 'owner_standardized_body': owner.standardized_body, 'follower_signed_flow': impulse.signed_flow, 'owner_signed_flow': owner.signed_flow, 'entry_model': 'PASSIVE_IMPULSE_CONSEQUENT_ENCROACHMENT', 'stop_model': 'IMPULSE_ORIGIN_INVALIDATION', 'target_model': 'ONE_FULL_IMPULSE_MEASURED_MOVE_EXTENSION', 'entry_cost_assumption': 'MAKER', 'stop_cost_assumption': 'TAKER', 'target_cost_assumption': 'MAKER'}
        plan = TradePlan(scenario_id=scenario_id, scenario=Scenario.AAC, direction=direction, observed_ts_ns=ts_ns, expected_entry=entry, stop_price=stop, target_price=target, atr=impulse.atr, loss_per_unit=loss, gain_per_unit=net_gain, net_r=net_r, reason_code='QUARTER_HOUR_COMMON_FLOW_FOLLOWER_RETEST', expire_ts_ns=ts_ns + 10 * MINUTE_NS, entry_order_type='LIMIT', entry_post_only=True, details=details)
        self._states[scenario_id] = 'PENDING_ENTRY'
        self._event(scenario_id=scenario_id, event_type='QH_FOLLOWER_PLAN_CONFIRMED', event_time_ns=impulse.start_ts_ns, observed_time_ns=ts_ns, previous_state='IDLE', next_state='PENDING_ENTRY', reason_code=plan.reason_code, reference_price=entry, details={'entry': entry, 'stop': stop, 'target': target, 'net_r': net_r, **details})
        return plan

    def on_batch(self, ts_ns: int, bars: Mapping[str, BarObs]) -> list[tuple[str, TradePlan]]:
        for symbol in SYMBOLS:
            observation = bars.get(symbol)
            if observation is None:
                self.skips['QH_SYNCHRONIZED_SYMBOL_MISSING'] += 1
                return []
            self._bars[symbol].append(observation)
        if not self._is_quarter_hour_window_end(ts_ns):
            return []
        if ts_ns == self._last_window_end_ns:
            return []
        self._last_window_end_ns = ts_ns
        impulses = [self._impulse(symbol, ts_ns) for symbol in SYMBOLS]
        if any((item is None for item in impulses)):
            self.skips['QH_WARMUP_OR_INCOMPLETE_WINDOW'] += 1
            return []
        materialized = [item for item in impulses if item is not None]
        long = [item for item in materialized if self._qualified(item, Direction.LONG)]
        short = [item for item in materialized if self._qualified(item, Direction.SHORT)]
        if len(long) >= 3 and len(short) >= 3:
            self.skips['QH_AMBIGUOUS_DUAL_DIRECTION_BREADTH'] += 1
            return []
        if len(long) >= 3:
            direction, accepted = (Direction.LONG, long)
        elif len(short) >= 3:
            direction, accepted = (Direction.SHORT, short)
        else:
            self.skips['QH_COMMON_FLOW_BREADTH_BELOW_THREE'] += 1
            return []
        owner = max(accepted, key=lambda item: (item.standardized_body, abs(item.signed_flow), item.symbol))
        self._sequence += 1
        event_id = f'QH-{ts_ns}-{self._sequence:06d}-{direction.value}-{owner.symbol}'
        self._event(scenario_id=event_id, event_type='QH_COMMON_FLOW_INITIATIVE', event_time_ns=min((item.start_ts_ns for item in accepted)), observed_time_ns=ts_ns, previous_state='IDLE', next_state='ACTIVE', reason_code='THREE_MARKET_QUARTER_HOUR_COMMON_FLOW', reference_price=owner.close, details={'direction': direction.value, 'owner_symbol': owner.symbol, 'accepted_symbols': [item.symbol for item in accepted], 'standardized_bodies': {item.symbol: item.standardized_body for item in accepted}, 'signed_flows': {item.symbol: item.signed_flow for item in accepted}})
        plans: list[tuple[str, TradePlan]] = []
        for impulse in accepted:
            if impulse.symbol == owner.symbol:
                continue
            plan = self._plan(impulse=impulse, direction=direction, owner=owner, accepted=accepted, ts_ns=ts_ns, event_id=event_id)
            if plan is not None:
                plans.append((impulse.symbol, plan))
        if not plans:
            self.skips['QH_NO_COSTED_FOLLOWER_PLAN'] += 1
        return plans

    def _transition(self, plan: TradePlan, *, ts_ns: int, next_state: str, reason: str, details: dict[str, Any] | None=None) -> None:
        previous = self._states.get(plan.scenario_id)
        if previous is None or previous == 'TERMINAL':
            return
        self._event(scenario_id=plan.scenario_id, event_type='QH_PLAN_LIFECYCLE', event_time_ns=ts_ns, observed_time_ns=ts_ns, previous_state=previous, next_state=next_state, reason_code=reason, reference_price=plan.expected_entry, details=details or {})
        self._states[plan.scenario_id] = next_state

    def mark_rejected(self, plan: TradePlan, ts_or_bar: Any, reason: str, details: dict[str, Any] | None=None) -> None:
        self._transition(plan, ts_ns=self._ts(ts_or_bar), next_state='TERMINAL', reason=reason, details=details)

    def mark_submitted(self, plan: TradePlan, quantity: Any, details: dict[str, Any]) -> None:
        payload = dict(details)
        payload.update({'quantity': str(quantity), 'module': QH_MODULE})
        self._transition(plan, ts_ns=plan.observed_ts_ns, next_state='SUBMITTED', reason='NAUTILUS_BRACKET_SUBMITTED', details=payload)
        self._active_scenario_id = plan.scenario_id

    def mark_entry_filled(self, ts_ns: int, details: dict[str, Any]) -> None:
        scenario_id = str(details.get('scenario_id', self._active_scenario_id or ''))
        if self._states.get(scenario_id) != 'SUBMITTED':
            return
        self.events.append(ResearchEvent(scenario_id=scenario_id, instrument_id=self.instrument_id, event_type='QH_ENTRY_FILLED', event_time_ns=int(ts_ns), observed_time_ns=int(ts_ns), previous_state='SUBMITTED', next_state='POSITION_OPEN', reason_code='NAUTILUS_PARENT_FILLED', reference_price=None, details=dict(details)))
        self._states[scenario_id] = 'POSITION_OPEN'
        self._active_scenario_id = scenario_id

    def mark_trade_terminal(self, ts_ns: int, reason: str) -> None:
        scenario_id = self._active_scenario_id
        if not scenario_id:
            return
        previous = self._states.get(scenario_id)
        if previous not in {'SUBMITTED', 'POSITION_OPEN'}:
            return
        self.events.append(ResearchEvent(scenario_id=scenario_id, instrument_id=self.instrument_id, event_type='QH_TRADE_TERMINAL', event_time_ns=int(ts_ns), observed_time_ns=int(ts_ns), previous_state=previous, next_state='TERMINAL', reason_code=reason, reference_price=None, details={'module': QH_MODULE}))
        self._states[scenario_id] = 'TERMINAL'
        self._active_scenario_id = None
