"""Aggressor-flow price-impact auction states for Candidate 11.

The detector consumes causally completed one-second bars built from Binance
USD-M aggregate trades.  It does not simulate execution, size positions, or
calculate PnL.  It describes two economically distinct auction outcomes:

* AR (absorption reversal): aggressive flow reaches a previously live external
  pool, but realized price impact is abnormally weak; price then reclaims the
  pool with opposite flow and local displacement.
* EAC (efficient acceptance continuation): aggressive flow crosses a live pool
  with unusually efficient price discovery, a low-opposition retest holds the
  boundary, and same-direction flow reaccelerates toward another live pool.

Every statistic excludes the bar that it evaluates from its own baseline.
Thresholds are dimensionless ranks or volatility/flow-normalized quantities so
that the logic is portable across instruments and liquidity regimes.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from math import isfinite, log, sqrt
from statistics import median
from typing import Literal

SECOND_NS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class FlowBar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float
    sell_volume: float
    quote_notional: float
    signed_notional: float
    trade_count: int
    max_trade_notional: float


@dataclass(frozen=True, slots=True)
class MicroPlan:
    scenario_id: str
    scenario: Literal["AR", "EAC"]
    direction: Literal["LONG", "SHORT"]
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    loss_per_unit: float
    net_r: float
    expire_ts_ns: int
    details: dict[str, object]


@dataclass(slots=True)
class _Pool:
    side: Literal["HIGH", "LOW"]
    price: float
    source: str
    created_ts_ns: int
    strength: int
    consumed: bool = False


@dataclass(slots=True)
class _Block:
    block_id: int
    high: float
    low: float


@dataclass(slots=True)
class _Event:
    pool: _Pool
    breakout_direction: Literal["LONG", "SHORT"]
    start_ts_ns: int
    origin_price: float
    extreme: float
    cumulative_signed_notional: float = 0.0
    cumulative_notional: float = 0.0
    bars: int = 0
    phase: str = "PROBING"
    classification_ts_ns: int | None = None
    retest_extreme: float | None = None


class AggressorImpactAuctionEngine:
    """Causal one-second detector independent of account/execution state."""

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
        self.bars: deque[FlowBar] = deque(maxlen=12_000)
        self.pools: list[_Pool] = []
        self.blocks: dict[int, _Block] = {}
        self.active: _Event | None = None
        self.sequence = 0
        self.pending_plan_id: str | None = None
        self.position_open = False
        self.cooldown_until_ns = -1
        self.skips: Counter[str] = Counter()
        self.events: list[dict[str, object]] = []

    @staticmethod
    def _validate(bar: FlowBar) -> None:
        numeric = (
            bar.open, bar.high, bar.low, bar.close, bar.volume,
            bar.buy_volume, bar.sell_volume, bar.quote_notional,
            bar.signed_notional, bar.max_trade_notional,
        )
        if not all(isfinite(value) for value in numeric):
            raise ValueError("non-finite microstructure bar")
        if bar.ts_ns <= 0 or bar.open <= 0 or bar.close <= 0 or bar.low <= 0:
            raise ValueError("invalid microstructure price/time")
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            raise ValueError("invalid OHLC ordering")
        if min(bar.volume, bar.buy_volume, bar.sell_volume, bar.quote_notional) < 0:
            raise ValueError("negative flow")

    def _record(self, event_type: str, ts_ns: int, **details: object) -> None:
        self.events.append({
            "type": event_type,
            "instrument_id": self.instrument_id,
            "observed_ts_ns": int(ts_ns),
            **details,
        })
        if len(self.events) > 20_000:
            self.events = self.events[-20_000:]

    def _roll_block(self, bar: FlowBar, seconds: int, source: str, strength: int) -> None:
        # A timestamp on the exact boundary belongs to the interval just closed.
        block_id = (bar.ts_ns - 1) // (seconds * SECOND_NS)
        current = self.blocks.get(seconds)
        if current is None:
            self.blocks[seconds] = _Block(block_id, bar.high, bar.low)
            return
        if current.block_id == block_id:
            current.high = max(current.high, bar.high)
            current.low = min(current.low, bar.low)
            return
        self._add_pool("HIGH", current.high, source, bar.ts_ns, strength)
        self._add_pool("LOW", current.low, source, bar.ts_ns, strength)
        self.blocks[seconds] = _Block(block_id, bar.high, bar.low)

    def _add_pool(
        self,
        side: Literal["HIGH", "LOW"],
        price: float,
        source: str,
        created_ts_ns: int,
        strength: int,
    ) -> None:
        if not isfinite(price) or price <= 0:
            return
        for pool in reversed(self.pools[-120:]):
            if pool.side == side and abs(pool.price / price - 1.0) <= 2e-6:
                if strength > pool.strength:
                    pool.source = source
                    pool.strength = strength
                    pool.created_ts_ns = created_ts_ns
                return
        self.pools.append(_Pool(side, price, source, created_ts_ns, strength))
        if len(self.pools) > 500:
            self.pools = self.pools[-500:]

    def _return_rms(self, lookback: int = 900) -> float | None:
        if len(self.bars) < lookback + 2:
            return None
        sample = list(self.bars)[-(lookback + 2):-1]
        returns = [log(curr.close / prev.close) for prev, curr in zip(sample, sample[1:])]
        if len(returns) != lookback:
            return None
        return sqrt(sum(value * value for value in returns) / len(returns))

    def _flow_rms(self, lookback: int = 900) -> float | None:
        if len(self.bars) < lookback + 1:
            return None
        values = [bar.signed_notional for bar in list(self.bars)[-(lookback + 1):-1]]
        if len(values) != lookback:
            return None
        return sqrt(sum(value * value for value in values) / len(values))

    def _atr_60(self) -> float | None:
        if len(self.bars) < 301:
            return None
        sample = list(self.bars)[-301:]
        # Sixty-second closes give a regime-scaled intraday true range while the
        # event itself still evolves on completed one-second observations.
        minute_bars: list[tuple[float, float, float]] = []
        for offset in range(0, 300, 60):
            part = sample[offset:offset + 61]
            if len(part) < 61:
                continue
            minute_bars.append((max(x.high for x in part), min(x.low for x in part), part[-1].close))
        if len(minute_bars) < 4:
            return None
        values: list[float] = []
        previous_close = sample[0].close
        for high, low, close in minute_bars:
            values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
            previous_close = close
        atr = sum(values) / len(values)
        return atr if atr > 0 else None

    def _touch_pool(self, bar: FlowBar, atr: float) -> _Pool | None:
        touched: list[tuple[int, float, int, _Pool]] = []
        for pool in self.pools:
            if pool.consumed or pool.created_ts_ns >= bar.ts_ns:
                continue
            distance = abs(pool.price - bar.open)
            if distance > 3.0 * atr:
                continue
            if pool.side == "HIGH" and bar.high >= pool.price:
                touched.append((-pool.strength, distance, pool.created_ts_ns, pool))
            elif pool.side == "LOW" and bar.low <= pool.price:
                touched.append((-pool.strength, distance, pool.created_ts_ns, pool))
        if not touched:
            return None
        touched.sort(key=lambda item: (item[0], item[1], item[2]))
        winner = touched[0][3]
        # First access consumes every coincident pool; none may silently seed a
        # later setup after its information has already entered price.
        tolerance = max(0.03 * atr, winner.price * 2e-6)
        for _, _, _, pool in touched:
            if abs(pool.price - winner.price) <= tolerance:
                pool.consumed = True
        return winner

    def _next_target(
        self,
        direction: Literal["LONG", "SHORT"],
        entry: float,
        stop: float,
        atr: float,
    ) -> _Pool | None:
        side = "HIGH" if direction == "LONG" else "LOW"
        candidates: list[tuple[float, int, int, _Pool]] = []
        risk = abs(entry - stop)
        for pool in self.pools:
            if pool.consumed or pool.side != side:
                continue
            distance = pool.price - entry if direction == "LONG" else entry - pool.price
            if distance <= 0 or distance > 12.0 * atr:
                continue
            if distance < max(1.0 * atr, 1.10 * risk):
                continue
            candidates.append((distance, -pool.strength, pool.created_ts_ns, pool))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][3]

    def _micro_vwap(self, seconds: int = 300) -> float | None:
        if len(self.bars) < seconds:
            return None
        sample = list(self.bars)[-seconds:]
        total = sum(bar.quote_notional for bar in sample)
        volume = sum(bar.volume for bar in sample)
        if total <= 0 or volume <= 0:
            return median(bar.close for bar in sample)
        return total / volume

    def _start_event(self, bar: FlowBar, pool: _Pool) -> None:
        breakout_direction: Literal["LONG", "SHORT"] = "LONG" if pool.side == "HIGH" else "SHORT"
        extreme = bar.high if breakout_direction == "LONG" else bar.low
        self.active = _Event(
            pool=pool,
            breakout_direction=breakout_direction,
            start_ts_ns=bar.ts_ns,
            origin_price=bar.open,
            extreme=extreme,
        )
        self._record(
            "EXTERNAL_POOL_FIRST_ACCESSED",
            bar.ts_ns,
            pool_side=pool.side,
            pool_price=pool.price,
            pool_source=pool.source,
            breakout_direction=breakout_direction,
        )

    def _update_probe(self, bar: FlowBar, atr: float, return_rms: float, flow_rms: float) -> None:
        event = self.active
        if event is None:
            return
        event.bars += 1
        event.cumulative_signed_notional += bar.signed_notional
        event.cumulative_notional += bar.quote_notional
        if event.breakout_direction == "LONG":
            event.extreme = max(event.extreme, bar.high)
            signed_flow = event.cumulative_signed_notional
            signed_move = log(max(event.extreme, event.pool.price) / event.pool.price)
        else:
            event.extreme = min(event.extreme, bar.low)
            signed_flow = -event.cumulative_signed_notional
            signed_move = log(event.pool.price / min(event.extreme, event.pool.price))
        flow_score = signed_flow / max(flow_rms * sqrt(max(event.bars, 1)), 1e-12)
        move_score = signed_move / max(return_rms * sqrt(max(event.bars, 1)), 1e-12)
        impact_ratio = move_score / max(flow_score, 1e-12)
        penetration = abs(event.extreme - event.pool.price) / atr

        if event.bars >= 3 and flow_score >= 2.0:
            absorption = (
                penetration <= 0.70
                and move_score <= 1.25
                and impact_ratio <= 0.55
            )
            acceptance = (
                penetration >= 0.08
                and move_score >= 1.25
                and impact_ratio >= 0.55
            )
            if absorption:
                event.phase = "ABSORPTION_WAIT_RECLAIM"
                event.classification_ts_ns = bar.ts_ns
                self._record(
                    "AGGRESSOR_FLOW_ABSORBED",
                    bar.ts_ns,
                    pool_price=event.pool.price,
                    flow_score=flow_score,
                    move_score=move_score,
                    impact_ratio=impact_ratio,
                    penetration_atr=penetration,
                )
                return
            if acceptance:
                event.phase = "ACCEPTANCE_WAIT_RETEST"
                event.classification_ts_ns = bar.ts_ns
                event.retest_extreme = event.extreme
                self._record(
                    "EXTERNAL_POOL_EFFICIENTLY_ACCEPTED",
                    bar.ts_ns,
                    pool_price=event.pool.price,
                    flow_score=flow_score,
                    move_score=move_score,
                    impact_ratio=impact_ratio,
                    penetration_atr=penetration,
                )
                return
        if event.bars > 20 or penetration > 2.0:
            self._terminate("UNCLASSIFIED_OR_VIOLENT_POOL_ACCESS", bar.ts_ns)

    def _local_flow_score(self, direction: Literal["LONG", "SHORT"], seconds: int = 5) -> float | None:
        flow_rms = self._flow_rms()
        if flow_rms is None or len(self.bars) < seconds:
            return None
        signed = sum(bar.signed_notional for bar in list(self.bars)[-seconds:])
        if direction == "SHORT":
            signed = -signed
        return signed / max(flow_rms * sqrt(seconds), 1e-12)

    def _confirmation_impulse(self, direction: Literal["LONG", "SHORT"], seconds: int = 3) -> float | None:
        rms = self._return_rms()
        if rms is None or len(self.bars) < seconds + 1:
            return None
        sample = list(self.bars)[-(seconds + 1):]
        value = log(sample[-1].close / sample[0].close)
        if direction == "SHORT":
            value = -value
        return value / max(rms * sqrt(seconds), 1e-12)

    def _costed_plan(
        self,
        *,
        scenario: Literal["AR", "EAC"],
        direction: Literal["LONG", "SHORT"],
        observed_ts_ns: int,
        entry: float,
        stop: float,
        target: float,
        expiry_seconds: int,
        details: dict[str, object],
    ) -> MicroPlan | None:
        valid = stop < entry < target if direction == "LONG" else target < entry < stop
        if not valid:
            self.skips["NON_CAUSAL_PRICE_ORDER"] += 1
            return None
        stop_distance = abs(entry - stop)
        loss_per_unit = (
            stop_distance
            + entry * self.effective_maker_rate
            + stop * self.effective_taker_rate
        )
        net_reward = (
            abs(target - entry)
            - entry * self.effective_maker_rate
            - target * self.effective_maker_rate
        )
        net_r = net_reward / max(loss_per_unit, 1e-12)
        if net_reward <= 0 or net_r < self.minimum_net_r:
            self.skips["INSUFFICIENT_COSTED_STRUCTURAL_R"] += 1
            return None
        self.sequence += 1
        scenario_id = (
            f"{self.instrument_id}-{scenario}-{observed_ts_ns}-{self.sequence:07d}"
        )
        return MicroPlan(
            scenario_id=scenario_id,
            scenario=scenario,
            direction=direction,
            observed_ts_ns=observed_ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            loss_per_unit=loss_per_unit,
            net_r=net_r,
            expire_ts_ns=observed_ts_ns + expiry_seconds * SECOND_NS,
            details=details,
        )

    def _maybe_absorption_plan(self, bar: FlowBar, atr: float) -> MicroPlan | None:
        event = self.active
        if event is None or event.phase != "ABSORPTION_WAIT_RECLAIM":
            return None
        direction: Literal["LONG", "SHORT"] = (
            "SHORT" if event.breakout_direction == "LONG" else "LONG"
        )
        reclaimed = (
            bar.close < event.pool.price - 0.01 * atr
            if direction == "SHORT"
            else bar.close > event.pool.price + 0.01 * atr
        )
        flow_score = self._local_flow_score(direction)
        impulse = self._confirmation_impulse(direction)
        previous = list(self.bars)[-8:-1]
        structure = (
            bar.close < min(value.low for value in previous)
            if direction == "SHORT"
            else bar.close > max(value.high for value in previous)
        ) if previous else False
        if reclaimed and structure and flow_score is not None and flow_score >= 0.75 and impulse is not None and impulse >= 0.90:
            entry = (bar.open + bar.close) / 2.0
            stop = (
                event.extreme + 0.08 * atr
                if direction == "SHORT"
                else event.extreme - 0.08 * atr
            )
            target_pool = self._next_target(direction, entry, stop, atr)
            vwap = self._micro_vwap()
            targets: list[tuple[float, str]] = []
            if target_pool is not None:
                targets.append((target_pool.price, target_pool.source))
            if vwap is not None:
                distance = entry - vwap if direction == "SHORT" else vwap - entry
                if distance >= max(1.0 * atr, 1.10 * abs(entry - stop)):
                    targets.append((vwap, "PRE_EVENT_5M_VWAP"))
            if targets:
                targets.sort(key=lambda item: abs(item[0] - entry))
                target, target_source = targets[0]
                plan = self._costed_plan(
                    scenario="AR",
                    direction=direction,
                    observed_ts_ns=bar.ts_ns,
                    entry=entry,
                    stop=stop,
                    target=target,
                    expiry_seconds=20,
                    details={
                        "source": "AGGRESSOR_IMPACT_ABSORPTION_REVERSAL",
                        "pool_price": event.pool.price,
                        "pool_source": event.pool.source,
                        "sweep_ts_ns": event.start_ts_ns,
                        "sweep_extreme": event.extreme,
                        "confirmation_flow_score": flow_score,
                        "confirmation_impulse": impulse,
                        "target_source": target_source,
                        "entry_cost_assumption": "MAKER",
                    },
                )
                if plan is not None:
                    self._record("ABSORPTION_REVERSAL_PLAN_EMITTED", bar.ts_ns, scenario_id=plan.scenario_id, net_r=plan.net_r)
                    self.active = None
                    return plan
        if event.bars > 45:
            self._terminate("ABSORPTION_RECLAIM_EXPIRED", bar.ts_ns)
        return None

    def _maybe_acceptance_plan(self, bar: FlowBar, atr: float) -> MicroPlan | None:
        event = self.active
        if event is None or event.phase not in {"ACCEPTANCE_WAIT_RETEST", "ACCEPTANCE_RETEST_HELD"}:
            return None
        direction = event.breakout_direction
        if direction == "LONG":
            event.retest_extreme = min(event.retest_extreme or bar.low, bar.low)
            retest = bar.low <= event.pool.price + 0.20 * atr and bar.close >= event.pool.price + 0.01 * atr
        else:
            event.retest_extreme = max(event.retest_extreme or bar.high, bar.high)
            retest = bar.high >= event.pool.price - 0.20 * atr and bar.close <= event.pool.price - 0.01 * atr
        if event.phase == "ACCEPTANCE_WAIT_RETEST" and retest:
            opposite: Literal["LONG", "SHORT"] = "SHORT" if direction == "LONG" else "LONG"
            opposition = self._local_flow_score(opposite, seconds=5)
            if opposition is not None and opposition <= 0.75:
                event.phase = "ACCEPTANCE_RETEST_HELD"
                self._record("ACCEPTED_POOL_RETEST_HELD", bar.ts_ns, pool_price=event.pool.price, opposition_score=opposition)
        if event.phase == "ACCEPTANCE_RETEST_HELD":
            flow_score = self._local_flow_score(direction)
            impulse = self._confirmation_impulse(direction)
            previous = list(self.bars)[-8:-1]
            structure = (
                bar.close > max(value.high for value in previous)
                if direction == "LONG"
                else bar.close < min(value.low for value in previous)
            ) if previous else False
            if structure and flow_score is not None and flow_score >= 0.75 and impulse is not None and impulse >= 0.90:
                entry = event.pool.price + (0.01 * atr if direction == "LONG" else -0.01 * atr)
                stop = (
                    (event.retest_extreme or event.pool.price) - 0.08 * atr
                    if direction == "LONG"
                    else (event.retest_extreme or event.pool.price) + 0.08 * atr
                )
                target_pool = self._next_target(direction, entry, stop, atr)
                if target_pool is not None:
                    plan = self._costed_plan(
                        scenario="EAC",
                        direction=direction,
                        observed_ts_ns=bar.ts_ns,
                        entry=entry,
                        stop=stop,
                        target=target_pool.price,
                        expiry_seconds=30,
                        details={
                            "source": "AGGRESSOR_IMPACT_EFFICIENT_ACCEPTANCE",
                            "pool_price": event.pool.price,
                            "pool_source": event.pool.source,
                            "breakout_ts_ns": event.start_ts_ns,
                            "breakout_extreme": event.extreme,
                            "retest_extreme": event.retest_extreme,
                            "confirmation_flow_score": flow_score,
                            "confirmation_impulse": impulse,
                            "target_source": target_pool.source,
                            "entry_cost_assumption": "MAKER",
                        },
                    )
                    if plan is not None:
                        self._record("EFFICIENT_ACCEPTANCE_PLAN_EMITTED", bar.ts_ns, scenario_id=plan.scenario_id, net_r=plan.net_r)
                        self.active = None
                        return plan
        if event.bars > 150:
            self._terminate("ACCEPTANCE_RETEST_EXPIRED", bar.ts_ns)
        return None

    def _terminate(self, reason: str, ts_ns: int) -> None:
        if self.active is not None:
            self._record(
                "MICROSTRUCTURE_EVENT_TERMINATED",
                ts_ns,
                reason=reason,
                phase=self.active.phase,
                pool_price=self.active.pool.price,
            )
        self.skips[reason] += 1
        self.active = None

    def on_bar(self, bar: FlowBar) -> MicroPlan | None:
        self._validate(bar)
        if self.bars and bar.ts_ns <= self.bars[-1].ts_ns:
            raise ValueError("microstructure bars must be strictly increasing")
        self.bars.append(bar)
        self._roll_block(bar, 300, "PRIOR_5M_AUCTION", 1)
        self._roll_block(bar, 900, "PRIOR_15M_AUCTION", 2)
        self._roll_block(bar, 3600, "PRIOR_1H_AUCTION", 3)
        atr = self._atr_60()
        return_rms = self._return_rms()
        flow_rms = self._flow_rms()
        if atr is None or return_rms is None or flow_rms is None:
            return None

        if self.active is not None:
            if self.active.phase == "PROBING":
                self._update_probe(bar, atr, return_rms, flow_rms)
            plan = self._maybe_absorption_plan(bar, atr)
            if plan is not None:
                return plan
            return self._maybe_acceptance_plan(bar, atr)

        if bar.ts_ns < self.cooldown_until_ns or self.pending_plan_id is not None or self.position_open:
            return None
        pool = self._touch_pool(bar, atr)
        if pool is not None:
            self._start_event(bar, pool)
            self._update_probe(bar, atr, return_rms, flow_rms)
        return None

    def mark_submitted(self, plan: MicroPlan) -> None:
        self.pending_plan_id = plan.scenario_id
        self._record("MICROSTRUCTURE_PLAN_SUBMITTED", plan.observed_ts_ns, scenario_id=plan.scenario_id)

    def mark_rejected(self, plan: MicroPlan, ts_ns: int, reason: str) -> None:
        self.pending_plan_id = None
        self.position_open = False
        self.cooldown_until_ns = int(ts_ns) + 30 * SECOND_NS
        self.skips[str(reason)] += 1
        self._record("MICROSTRUCTURE_PLAN_REJECTED", ts_ns, scenario_id=plan.scenario_id, reason=str(reason))

    def mark_entry_filled(self, ts_ns: int) -> None:
        if self.pending_plan_id is None:
            return
        self.position_open = True
        self._record("MICROSTRUCTURE_ENTRY_FILLED", ts_ns, scenario_id=self.pending_plan_id)

    def mark_terminal(self, ts_ns: int, reason: str) -> None:
        scenario_id = self.pending_plan_id
        self.pending_plan_id = None
        self.position_open = False
        self.cooldown_until_ns = int(ts_ns) + 60 * SECOND_NS
        self._record("MICROSTRUCTURE_TRADE_TERMINAL", ts_ns, scenario_id=scenario_id, reason=str(reason))
