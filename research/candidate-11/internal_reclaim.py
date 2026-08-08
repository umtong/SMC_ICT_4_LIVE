"""Causal internal-liquidity reclaim and external-draw reacceleration.

This module adds an independent continuation family without weakening the
existing FAR/AAC detector or its price-discovery gate.  A plan can be emitted
only after all of the following are visible on completed one-minute bars:

1. a still-live prior hourly, four-hour, or UTC-day auction boundary exists in
   the proposed direction;
2. a causally confirmed internal pivot is swept in the opposite direction;
3. price reclaims that pivot before the pullback becomes a new external break;
4. a local structure break, directionally aligned aggressor flow, and a
   volatility-normalized confirmation impulse demonstrate reacceleration;
5. the limit retracement entry, structural stop, and live external target retain
   at least the configured post-cost structural R.

The detector never sizes positions, submits orders, models fills, or calculates
account PnL.  Those responsibilities remain with the existing NautilusTrader
portfolio runner and exact 3% NAV RiskSizer.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import MISSING, dataclass, fields, is_dataclass
from math import isfinite, log, sqrt
from statistics import median
from typing import Any

from logic import Direction, Scenario, TradePlan

MINUTE_NS = 60_000_000_000
SOURCE = "INTERNAL_RECLAIM_EXTERNAL_DRAW"


@dataclass(slots=True)
class _Bar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float


@dataclass(slots=True)
class _Pivot:
    side: str
    price: float
    pivot_ts_ns: int
    confirmed_ts_ns: int
    consumed: bool = False


@dataclass(slots=True)
class _Target:
    side: str
    price: float
    created_ts_ns: int
    source: str
    consumed: bool = False


@dataclass(slots=True)
class _Block:
    block_id: int
    high: float
    low: float


@dataclass(slots=True)
class _Sweep:
    direction: str
    pivot: _Pivot
    target: _Target
    sweep_ts_ns: int
    sweep_extreme: float
    phase: str
    reclaim_ts_ns: int | None = None
    reclaim_anchor: float | None = None
    age_bars: int = 0


def _float_attr(value: Any, name: str) -> float:
    result = float(getattr(value, name))
    if not isfinite(result):
        raise ValueError(f"non-finite bar field {name}")
    return result


def _enum_far() -> Any:
    return Scenario.FAR


def _construct_trade_plan(values: dict[str, Any]) -> TradePlan:
    """Construct the project TradePlan while tolerating stable field aliases.

    Candidate 11 has evolved through several source migrations.  The economic
    fields are stable, while two historical revisions used entry/expiry aliases.
    Supporting those aliases keeps this independent scenario compatible with the
    committed candidate without duplicating its plan type.
    """
    aliases: dict[str, tuple[str, ...]] = {
        "scenario_id": ("scenario_id", "plan_id"),
        "scenario": ("scenario", "scenario_type"),
        "direction": ("direction", "side"),
        "observed_ts_ns": ("observed_ts_ns", "confirmation_ts_ns", "created_ts_ns"),
        "expected_entry": ("expected_entry", "entry_price", "entry"),
        "stop_price": ("stop_price", "stop"),
        "target_price": ("target_price", "target"),
        "loss_per_unit": ("loss_per_unit", "expected_loss_per_unit", "risk_per_unit"),
        "net_r": ("net_r", "structural_r", "r_multiple"),
        "expire_ts_ns": ("expire_ts_ns", "expiry_ts_ns", "entry_expiry_ts_ns"),
        "details": ("details", "metadata"),
    }
    canonical: dict[str, Any] = {}
    for canonical_name, names in aliases.items():
        for name in names:
            if name in values:
                canonical[canonical_name] = values[name]
                break

    if not is_dataclass(TradePlan):
        return TradePlan(**canonical)

    kwargs: dict[str, Any] = {}
    for field in fields(TradePlan):
        if field.name in values:
            kwargs[field.name] = values[field.name]
            continue
        matched = False
        for canonical_name, names in aliases.items():
            if field.name in names and canonical_name in canonical:
                kwargs[field.name] = canonical[canonical_name]
                matched = True
                break
        if matched:
            continue
        if field.default is not MISSING or field.default_factory is not MISSING:
            continue
        raise TypeError(f"unsupported required TradePlan field: {field.name}")
    return TradePlan(**kwargs)


def is_internal_reclaim_plan(plan: Any) -> bool:
    details = getattr(plan, "details", None)
    return isinstance(details, dict) and details.get("source") == SOURCE


class InternalReclaimEngine:
    """Single-symbol causal detector; portfolio approval remains external."""

    def __init__(self, config: Any, instrument_id: str) -> None:
        self.instrument_id = str(instrument_id)
        self.effective_maker_rate = float(getattr(config, "effective_maker_rate", 0.0004))
        self.effective_taker_rate = float(getattr(config, "effective_taker_rate", 0.0008))
        self.minimum_net_r = max(1.25, float(getattr(config, "min_net_r", 1.25)))
        self.bars: deque[_Bar] = deque(maxlen=3200)
        self.pivots: list[_Pivot] = []
        self.targets: list[_Target] = []
        self.blocks: dict[int, _Block] = {}
        self.active: _Sweep | None = None
        self.pending_plan_id: str | None = None
        self.position_open = False
        self.cooldown_until_ns = -1
        self.sequence = 0
        self.skips: Counter[str] = Counter()
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def _bar(value: Any) -> _Bar:
        ts_ns = int(getattr(value, "ts_ns"))
        result = _Bar(
            ts_ns=ts_ns,
            open=_float_attr(value, "open"),
            high=_float_attr(value, "high"),
            low=_float_attr(value, "low"),
            close=_float_attr(value, "close"),
            volume=max(0.0, _float_attr(value, "volume")),
            taker_buy_volume=max(0.0, _float_attr(value, "taker_buy_volume")),
        )
        if result.high < max(result.open, result.close) or result.low > min(result.open, result.close):
            raise ValueError("invalid OHLC ordering")
        return result

    def _atr(self, period: int = 30) -> float | None:
        if len(self.bars) < period + 1:
            return None
        sample = list(self.bars)[-(period + 1):]
        values: list[float] = []
        for previous, current in zip(sample, sample[1:]):
            values.append(max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            ))
        atr = sum(values) / len(values)
        return atr if atr > 0 and isfinite(atr) else None

    def _confirm_internal_pivot(self, wing: int = 2) -> None:
        sample = list(self.bars)
        if len(sample) < 2 * wing + 1:
            return
        index = len(sample) - wing - 1
        candidate = sample[index]
        window = sample[index - wing:index + wing + 1]
        lows = [bar.low for bar in window]
        highs = [bar.high for bar in window]
        if candidate.low == min(lows) and lows.count(candidate.low) == 1:
            self.pivots.append(_Pivot(
                side="LOW",
                price=candidate.low,
                pivot_ts_ns=candidate.ts_ns,
                confirmed_ts_ns=sample[-1].ts_ns,
            ))
        if candidate.high == max(highs) and highs.count(candidate.high) == 1:
            self.pivots.append(_Pivot(
                side="HIGH",
                price=candidate.high,
                pivot_ts_ns=candidate.ts_ns,
                confirmed_ts_ns=sample[-1].ts_ns,
            ))
        if len(self.pivots) > 240:
            self.pivots = self.pivots[-240:]

    def _roll_block(self, bar: _Bar, minutes: int, source: str) -> None:
        block_id = bar.ts_ns // (minutes * MINUTE_NS)
        current = self.blocks.get(minutes)
        if current is None:
            self.blocks[minutes] = _Block(block_id, bar.high, bar.low)
            return
        if current.block_id == block_id:
            current.high = max(current.high, bar.high)
            current.low = min(current.low, bar.low)
            return
        created = bar.ts_ns
        self._add_target("HIGH", current.high, created, source)
        self._add_target("LOW", current.low, created, source)
        self.blocks[minutes] = _Block(block_id, bar.high, bar.low)

    def _add_target(self, side: str, price: float, created_ts_ns: int, source: str) -> None:
        if not isfinite(price) or price <= 0:
            return
        for target in reversed(self.targets[-80:]):
            if target.side == side and abs(target.price / price - 1.0) <= 1e-6:
                return
        self.targets.append(_Target(side, price, created_ts_ns, source))
        if len(self.targets) > 300:
            self.targets = self.targets[-300:]

    def _consume_touched_targets(self, bar: _Bar) -> None:
        for target in self.targets:
            if target.consumed or target.created_ts_ns >= bar.ts_ns:
                continue
            if target.side == "HIGH" and bar.high >= target.price:
                target.consumed = True
            elif target.side == "LOW" and bar.low <= target.price:
                target.consumed = True

    def _live_target(self, direction: str, price: float, atr: float) -> _Target | None:
        side = "HIGH" if direction == "LONG" else "LOW"
        candidates: list[tuple[float, int, _Target]] = []
        weight = {"PRIOR_UTC_DAY": 3, "PRIOR_4H_AUCTION": 2, "PRIOR_1H_AUCTION": 1}
        for target in self.targets:
            if target.consumed or target.side != side:
                continue
            distance = target.price - price if direction == "LONG" else price - target.price
            if not 0.75 * atr <= distance <= 12.0 * atr:
                continue
            candidates.append((distance, -weight.get(target.source, 0), target))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2].created_ts_ns))
        return candidates[0][2]

    def _trend_state(self) -> tuple[float, float, float] | None:
        if len(self.bars) < 121:
            return None
        bars = list(self.bars)
        close = bars[-1].close
        r30 = log(close / bars[-31].close)
        r120 = log(close / bars[-121].close)
        sixty = bars[-61:]
        one_minute = [log(curr.close / prev.close) for prev, curr in zip(sixty, sixty[1:])]
        path = sum(abs(value) for value in one_minute)
        efficiency = log(sixty[-1].close / sixty[0].close) / max(path, 1e-12)
        volume_sum = sum(bar.volume for bar in sixty)
        vwap = (
            sum(bar.close * bar.volume for bar in sixty) / volume_sum
            if volume_sum > 0
            else median(bar.close for bar in sixty)
        )
        location = log(close / vwap)
        return r30, r120, efficiency + location

    def _direction(self, atr: float) -> tuple[str, _Target] | None:
        state = self._trend_state()
        if state is None:
            return None
        r30, r120, composite = state
        price = self.bars[-1].close
        long_votes = int(r30 > 0) + int(r120 > 0) + int(composite > 0)
        short_votes = int(r30 < 0) + int(r120 < 0) + int(composite < 0)
        choices: list[tuple[float, str, _Target]] = []
        if long_votes >= 2:
            target = self._live_target("LONG", price, atr)
            if target is not None:
                choices.append((r30 + 0.5 * r120 + composite, "LONG", target))
        if short_votes >= 2:
            target = self._live_target("SHORT", price, atr)
            if target is not None:
                choices.append((-(r30 + 0.5 * r120 + composite), "SHORT", target))
        if not choices:
            return None
        choices.sort(key=lambda item: (-item[0], item[1]))
        return choices[0][1], choices[0][2]

    def _eligible_pivot(self, direction: str, now_ns: int) -> _Pivot | None:
        desired = "LOW" if direction == "LONG" else "HIGH"
        for pivot in reversed(self.pivots):
            if pivot.side != desired or pivot.consumed:
                continue
            age = (now_ns - pivot.confirmed_ts_ns) // MINUTE_NS
            if 3 <= age <= 240:
                return pivot
        return None

    def _confirmation_impulse(self, direction: str, lookback: int = 60) -> float | None:
        if len(self.bars) < lookback + 2:
            return None
        sample = list(self.bars)[-(lookback + 2):]
        baseline = [log(curr.close / prev.close) for prev, curr in zip(sample[:-2], sample[1:-1])]
        rms = sqrt(sum(value * value for value in baseline) / len(baseline))
        sign = 1.0 if direction == "LONG" else -1.0
        current = sign * log(sample[-1].close / sample[-2].close)
        return current / max(rms, 1e-12)

    def _flow_aligned(self, bar: _Bar, direction: str) -> bool:
        if bar.volume <= 0:
            return False
        fraction = bar.taker_buy_volume / bar.volume
        return fraction >= 0.52 if direction == "LONG" else fraction <= 0.48

    def _volume_active(self, bar: _Bar) -> bool:
        if len(self.bars) < 61:
            return False
        prior = [value.volume for value in list(self.bars)[-61:-1]]
        return bar.volume >= 0.80 * median(prior)

    def _record(self, event_type: str, ts_ns: int, **details: Any) -> None:
        self.events.append({
            "type": event_type,
            "instrument_id": self.instrument_id,
            "observed_ts_ns": int(ts_ns),
            **details,
        })
        if len(self.events) > 5000:
            self.events = self.events[-5000:]

    def _reset_active(self, reason: str, ts_ns: int) -> None:
        if self.active is not None:
            self._record(
                "INTERNAL_RECLAIM_TERMINATED",
                ts_ns,
                reason=reason,
                direction=self.active.direction,
                sweep_ts_ns=self.active.sweep_ts_ns,
                pivot_price=self.active.pivot.price,
            )
        self.active = None
        self.skips[reason] += 1

    def _begin_or_update_sweep(self, bar: _Bar, atr: float) -> None:
        if self.active is not None:
            state = self.active
            state.age_bars += 1
            if state.direction == "LONG":
                state.sweep_extreme = min(state.sweep_extreme, bar.low)
                if state.phase == "SWEPT" and bar.close > state.pivot.price + 0.02 * atr:
                    state.phase = "RECLAIMED"
                    state.reclaim_ts_ns = bar.ts_ns
                    state.reclaim_anchor = bar.high
                elif bar.low < state.pivot.price - 1.50 * atr:
                    self._reset_active("INTERNAL_PULLBACK_BECAME_EXTERNAL_BREAK", bar.ts_ns)
                    return
            else:
                state.sweep_extreme = max(state.sweep_extreme, bar.high)
                if state.phase == "SWEPT" and bar.close < state.pivot.price - 0.02 * atr:
                    state.phase = "RECLAIMED"
                    state.reclaim_ts_ns = bar.ts_ns
                    state.reclaim_anchor = bar.low
                elif bar.high > state.pivot.price + 1.50 * atr:
                    self._reset_active("INTERNAL_PULLBACK_BECAME_EXTERNAL_BREAK", bar.ts_ns)
                    return
            limit = 5 if state.phase == "SWEPT" else 8
            if state.age_bars > limit:
                self._reset_active("INTERNAL_RECLAIM_CONFIRMATION_EXPIRED", bar.ts_ns)
            return

        if bar.ts_ns < self.cooldown_until_ns or self.pending_plan_id is not None or self.position_open:
            return
        directional = self._direction(atr)
        if directional is None:
            return
        direction, target = directional
        pivot = self._eligible_pivot(direction, bar.ts_ns)
        if pivot is None:
            return
        minimum = 0.03 * atr
        maximum = 1.50 * atr
        if direction == "LONG":
            penetration = pivot.price - bar.low
            swept = minimum <= penetration <= maximum
            reclaimed = swept and bar.close > pivot.price + 0.02 * atr
            extreme = bar.low
            anchor = bar.high if reclaimed else None
        else:
            penetration = bar.high - pivot.price
            swept = minimum <= penetration <= maximum
            reclaimed = swept and bar.close < pivot.price - 0.02 * atr
            extreme = bar.high
            anchor = bar.low if reclaimed else None
        if not swept:
            return
        pivot.consumed = True
        self.active = _Sweep(
            direction=direction,
            pivot=pivot,
            target=target,
            sweep_ts_ns=bar.ts_ns,
            sweep_extreme=extreme,
            phase="RECLAIMED" if reclaimed else "SWEPT",
            reclaim_ts_ns=bar.ts_ns if reclaimed else None,
            reclaim_anchor=anchor,
        )
        self._record(
            "INTERNAL_LIQUIDITY_SWEPT",
            bar.ts_ns,
            direction=direction,
            pivot_price=pivot.price,
            target_price=target.price,
            target_source=target.source,
            penetration_atr=penetration / atr,
            reclaimed_same_bar=reclaimed,
        )

    def _maybe_plan(self, bar: _Bar, atr: float) -> TradePlan | None:
        state = self.active
        if state is None or state.phase != "RECLAIMED" or state.reclaim_anchor is None:
            return None
        if state.target.consumed:
            self._reset_active("EXTERNAL_DRAW_CONSUMED_BEFORE_CONFIRMATION", bar.ts_ns)
            return None
        previous = list(self.bars)[-4:-1]
        if len(previous) < 3:
            return None
        body = abs(bar.close - bar.open)
        location = (
            (bar.close - bar.low) / max(bar.high - bar.low, 1e-12)
            if state.direction == "LONG"
            else (bar.high - bar.close) / max(bar.high - bar.low, 1e-12)
        )
        structure = (
            bar.close > max(value.high for value in previous)
            if state.direction == "LONG"
            else bar.close < min(value.low for value in previous)
        )
        impulse = self._confirmation_impulse(state.direction)
        confirmed = (
            structure
            and body >= 0.20 * atr
            and location >= 0.65
            and impulse is not None
            and impulse >= 0.80
            and self._flow_aligned(bar, state.direction)
            and self._volume_active(bar)
        )
        if not confirmed:
            return None

        if state.direction == "LONG":
            entry = max(state.pivot.price, (bar.open + bar.close) / 2.0)
            stop = state.sweep_extreme - 0.08 * atr
            target = state.target.price
            direction_enum = Direction.LONG
            valid_ordering = stop < entry < target
        else:
            entry = min(state.pivot.price, (bar.open + bar.close) / 2.0)
            stop = state.sweep_extreme + 0.08 * atr
            target = state.target.price
            direction_enum = Direction.SHORT
            valid_ordering = target < entry < stop
        if not valid_ordering:
            self._reset_active("INTERNAL_RECLAIM_NON_CAUSAL_PRICE_ORDER", bar.ts_ns)
            return None

        stop_distance = abs(entry - stop)
        if not 0.12 * atr <= stop_distance <= 1.75 * atr:
            self._reset_active("INTERNAL_RECLAIM_STOP_GEOMETRY", bar.ts_ns)
            return None
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
            self._reset_active("INTERNAL_RECLAIM_INSUFFICIENT_COSTED_R", bar.ts_ns)
            return None

        self.sequence += 1
        scenario_id = (
            f"{self.instrument_id}-IRX-{bar.ts_ns}-{self.sequence:06d}"
        )
        details = {
            "source": SOURCE,
            "entry_cost_assumption": "MAKER",
            "entry_expiry_bars": 8,
            "sweep_ts_ns": state.sweep_ts_ns,
            "internal_pivot_ts_ns": state.pivot.pivot_ts_ns,
            "internal_pivot_price": state.pivot.price,
            "sweep_extreme": state.sweep_extreme,
            "reclaim_ts_ns": state.reclaim_ts_ns,
            "confirmation_impulse": impulse,
            "confirmation_body_atr": body / atr,
            "confirmation_close_location": location,
            "external_target_source": state.target.source,
            "external_target_created_ts_ns": state.target.created_ts_ns,
            "external_target_price": state.target.price,
        }
        plan = _construct_trade_plan({
            "scenario_id": scenario_id,
            "scenario": _enum_far(),
            "direction": direction_enum,
            "observed_ts_ns": bar.ts_ns,
            "expected_entry": entry,
            "stop_price": stop,
            "target_price": target,
            "loss_per_unit": loss_per_unit,
            "net_r": net_r,
            "expire_ts_ns": bar.ts_ns + 8 * MINUTE_NS,
            "details": details,
        })
        self._record(
            "INTERNAL_RECLAIM_PLAN_EMITTED",
            bar.ts_ns,
            scenario_id=scenario_id,
            direction=state.direction,
            entry=entry,
            stop=stop,
            target=target,
            net_r=net_r,
        )
        self.active = None
        return plan

    def on_bar(self, value: Any) -> TradePlan | None:
        bar = self._bar(value)
        if self.bars and bar.ts_ns <= self.bars[-1].ts_ns:
            raise ValueError("internal reclaim bars must be strictly increasing")
        self.bars.append(bar)
        self._confirm_internal_pivot()
        self._roll_block(bar, 60, "PRIOR_1H_AUCTION")
        self._roll_block(bar, 240, "PRIOR_4H_AUCTION")
        self._roll_block(bar, 1440, "PRIOR_UTC_DAY")
        self._consume_touched_targets(bar)
        atr = self._atr()
        if atr is None:
            return None
        self._begin_or_update_sweep(bar, atr)
        return self._maybe_plan(bar, atr)

    def mark_submitted(self, plan: Any, *_: Any, **__: Any) -> None:
        if not is_internal_reclaim_plan(plan):
            raise ValueError("attempted to submit a non-internal plan")
        self.pending_plan_id = str(getattr(plan, "scenario_id"))
        self._record(
            "INTERNAL_RECLAIM_SUBMITTED",
            int(getattr(plan, "observed_ts_ns")),
            scenario_id=self.pending_plan_id,
        )

    def mark_rejected(
        self,
        plan: Any,
        ts_ns: int,
        reason: str,
        *_: Any,
        **__: Any,
    ) -> None:
        scenario_id = str(getattr(plan, "scenario_id", "UNKNOWN"))
        self.skips[str(reason)] += 1
        self.pending_plan_id = None
        self.position_open = False
        self.cooldown_until_ns = int(ts_ns) + 5 * MINUTE_NS
        self._record(
            "INTERNAL_RECLAIM_REJECTED",
            int(ts_ns),
            scenario_id=scenario_id,
            reason=str(reason),
        )

    def mark_entry_filled(self, ts_ns: int, *_: Any, **__: Any) -> None:
        if self.pending_plan_id is None:
            return
        self.position_open = True
        self._record(
            "INTERNAL_RECLAIM_ENTRY_FILLED",
            int(ts_ns),
            scenario_id=self.pending_plan_id,
        )

    def mark_trade_terminal(self, ts_ns: int, reason: str, *_: Any, **__: Any) -> None:
        scenario_id = self.pending_plan_id
        self.pending_plan_id = None
        self.position_open = False
        self.cooldown_until_ns = int(ts_ns) + 10 * MINUTE_NS
        self._record(
            "INTERNAL_RECLAIM_TRADE_TERMINAL",
            int(ts_ns),
            scenario_id=scenario_id,
            reason=str(reason),
        )
