"""Received-time forced inventory response, not a candlestick proxy for liquidation.

A previously observed swing locates the auction. A received liquidation print
identifies compulsory aggression. The subsequent price/ordinary-flow response
separates absorption from accepted repricing. Entry is a single completed-bar
response; stop and nearest observed objective are frozen before submission.

This is an experimental translation, NOT evidence of a profitable strategy.
The public force-order feed is sampled and does not identify all liquidations.
Missing feed intervals must be excluded by the caller, never interpreted as zero.
There are no symbol-specific rules, fixed-R targets, daily limits or risk overlays.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from math import isfinite, log1p
from typing import Mapping, Sequence
import numpy as np
from astra_policy import Observation, Plan, MINUTE, SYMBOLS
from domain import Side
from policy import Frame


@dataclass(frozen=True, slots=True)
class ForcedPrint:
    received_ns: int
    event_ns: int
    symbol: str
    side: int  # BUY=+1, SELL=-1: order direction, NOT liquidated position direction
    price: float
    quantity: float
    key: str

    def __post_init__(self):
        if self.symbol not in SYMBOLS or self.side not in (-1, 1):
            raise ValueError('invalid force-order symbol or side')
        if self.received_ns < self.event_ns or self.event_ns <= 0:
            raise ValueError('force-order availability precedes its event')
        if not all(isfinite(x) and x > 0 for x in (self.price, self.quantity)):
            raise ValueError('invalid force-order execution')

    @property
    def quote(self) -> float:
        return self.price * self.quantity


@dataclass(slots=True)
class InventoryEpisode:
    key: str
    born: int
    side: int
    boundary: float
    scale: int
    prior_value: float
    prior_opposite: float
    first_extreme: float
    high: float
    low: float
    force_quote: float
    force_quantity: float
    baseline_quote: float
    baseline_range: float
    observations: list = field(default_factory=list)
    crossed_price: float | None = None
    crossing_ns: int = 0
    tested: bool = False
    test_high: float = 0.0
    test_low: float = float('inf')
    last_print_ns: int = 0

    @property
    def force_vwap(self) -> float:
        return self.force_quote / self.force_quantity


class InventoryMarket:
    """One event owns its full causal wave, independent of how many prints arrive."""
    def __init__(self, symbol: str, tick: float):
        self.symbol, self.tick = symbol, float(tick)
        self.history = deque(maxlen=1440)
        self.frames = {n: Frame(n) for n in (5, 15, 60)}
        self.prior_bursts = deque(maxlen=256)
        self.active: InventoryEpisode | None = None
        self.used_sources: set[str] = set()
        self.seen_prints: set[str] = set()
        self.last_ts = 0
        self.stats: dict[str, int] = {}

    def count(self, key):
        self.stats[key] = self.stats.get(key, 0) + 1

    def _targets(self, side, entry, ts, event):
        # A former value and opposite edge are public prices observed BEFORE the
        # event. Confirmed opposing swings are usable only while still unspent.
        candidates = [event.prior_value, event.prior_opposite]
        for frame in self.frames.values():
            candidates.extend(z.price for z in frame.levels
                              if z.kind == side and z.born < ts and not z.consumed)
        return sorted({p for p in candidates if side * (p-entry) > self.tick},
                      key=lambda p: side*(p-entry))

    def _start(self, b, prints):
        if len(self.history) < 240 or not prints:
            return
        net = sum(p.side*p.quote for p in prints)
        if net == 0:
            return
        direction = 1 if net > 0 else -1
        aligned = [p for p in prints if p.side == direction]
        total = sum(p.quote for p in aligned)
        # This is event detection, not a fitted profit/win-rate threshold. The
        # same upper-tail definition is used across assets and both directions.
        prior = list(self.prior_bursts)
        if len(prior) < 32 or total < float(np.quantile(prior, .90)):
            return
        levels = [z for n, f in self.frames.items() if n >= 15 for z in f.levels
                  if z.kind == direction and not z.consumed and z.born < b.ts
                  and z.key not in self.used_sources
                  and (b.high > z.price if direction > 0 else b.low < z.price)]
        if not levels:
            self.count('forced_flow_without_public_sweep')
            return
        z = max(levels, key=lambda x: (x.tf, x.born))
        past = list(self.history)[-60:]
        volume = sum(x.volume for x in past)
        value = sum(x.quote for x in past)/volume if volume > 0 else past[-1].close
        opposite = min(x.low for x in past) if direction > 0 else max(x.high for x in past)
        unit = max(self.tick, float(np.median([x.high-x.low for x in past])))
        key = f'{self.symbol}:FORCED:{z.key}:{b.ts}'
        self.active = InventoryEpisode(key, b.ts, direction, z.price, z.tf,
            value, opposite, b.high if direction > 0 else b.low,
            b.high, b.low, total, sum(p.quantity for p in aligned),
            float(np.median(prior)), unit, last_print_ns=max(p.received_ns for p in aligned))
        self.used_sources.add(z.key)
        self.count('received_forced_sweep')

    def _advance(self, b, prints, peer_move):
        e = self.active
        if e is None or b.ts <= e.born:
            return []
        e.high, e.low = max(e.high, b.high), min(e.low, b.low)
        for p in prints:
            if p.side == e.side:
                e.force_quote += p.quote
                e.force_quantity += p.quantity
                e.last_print_ns = max(e.last_print_ns, p.received_ns)
        e.observations.append(b)
        # The event ceases to describe the market once its originating timeframe
        # has itself completed a later swing. This is structural expiry, not a
        # time-of-day or account-loss filter.
        new = [z for z in self.frames[e.scale].pivots
               if z.born > e.born and z.pivot_time > e.born]
        if new:
            self.active = None
            self.count('new_auction_replaces_forced_episode')
            return []
        side = -e.side
        vwap = e.force_vwap
        reclaimed = side*(b.close-e.boundary) > 0 and side*(b.close-vwap) > 0
        delta = b.delta/max(b.volume, 1e-12)
        progress = side*(b.close-b.open)
        # The central hypothesis is observable absorption: ordinary aggression
        # is STILL adverse, but price makes progress against that aggression.
        # A feed gap or simply 'no more liquidations' is not confirmation.
        absorbed = side*delta < 0 and progress > 0
        if e.crossed_price is None:
            if reclaimed and absorbed:
                e.crossed_price = vwap
                e.crossing_ns = b.ts
                e.test_high, e.test_low = b.high, b.low
                self.count('forced_aggression_absorbed')
            return []
        # Freeze the response reference at its observation, not at a later VWAP.
        reference = e.crossed_price
        if not e.tested:
            touch = b.low <= reference if side > 0 else b.high >= reference
            if not touch:
                return []
            e.tested = True
            e.test_high, e.test_low = b.high, b.low
            return []
        e.test_high, e.test_low = max(e.test_high,b.high), min(e.test_low,b.low)
        previous = e.observations[-2] if len(e.observations) > 1 else b
        renewed = b.close > previous.high if side > 0 else b.close < previous.low
        if not (renewed and reclaimed):
            return []
        stop = e.low-self.tick if side > 0 else e.high+self.tick
        risk = side*(b.close-stop)
        objectives = self._targets(side, b.close, b.ts, e)
        self.active = None  # a completed response is consumed, never retried
        if not objectives:
            self.count('no_public_objective')
            return []
        target = objectives[0]-side*self.tick
        reward = side*(target-b.close)
        if risk <= self.tick or reward < risk:
            self.count('response_geometry_below_one_r')
            return []
        cost_r = .0006*(b.close+stop)/risk
        features = dict(cost_r=cost_r, planned_rr=reward/risk,
            risk_range=risk/e.baseline_range, risk_bps=10000*risk/b.close,
            forced_intensity=e.force_quote/max(e.baseline_quote,1.),
            response_progress=progress/e.baseline_range,
            response_pressure=side*delta,
            peer_response=side*peer_move,
            source_scale=float(e.scale),
            absorption_distance=side*(b.close-reference)/e.baseline_range)
        self.count('held_inventory_response')
        return [Plan(e.key+':RESPONSE',e.key,self.symbol,
            Side.LONG if side > 0 else Side.SHORT,b.ts,e.born,b.close,stop,target,
            reward/risk,e.boundary,e.scale,e.key,'PRE_EVENT_VALUE_OR_OPPOSING_SWING',
            min(reference,e.boundary),max(reference,e.boundary),e.high,e.low,features,
            family='FORCED_INVENTORY_ABSORPTION')]

    def observe(self, b, prints, peer_move=0.):
        if self.last_ts and b.ts-self.last_ts != MINUTE:
            raise ValueError('noncontiguous price clock')
        self.last_ts = b.ts
        for p in prints:
            if p.symbol != self.symbol or not b.ts-MINUTE < p.received_ns <= b.ts:
                raise ValueError('force-order not available in this completed minute')
        unique = [p for p in prints if p.key not in self.seen_prints]
        self.seen_prints.update(p.key for p in unique)
        plans = self._advance(b, unique, peer_move)
        if self.active is None:
            self._start(b, unique)
        if unique:
            self.prior_bursts.append(sum(p.quote for p in unique))
        # Read old levels for this interaction, then consume/update them. No
        # right-side pivot is exposed before its confirmation timestamp.
        for f in self.frames.values():
            for z in f.levels:
                if z.born < b.ts and (b.high >= z.price if z.kind > 0 else b.low <= z.price):
                    z.consumed = True
            f.append(b)
        self.history.append(b)
        return plans


class ForcedInventoryPolicy:
    def __init__(self, ticks: Mapping[str, float]):
        self.markets = {s: InventoryMarket(s,t) for s,t in ticks.items()}
        self.last_ts = 0

    def observe(self, bars: Mapping[str, Observation], prints: Sequence[ForcedPrint]=()):
        if set(bars) != set(self.markets):
            raise ValueError('incomplete synchronized universe')
        timestamps = {b.ts for b in bars.values()}
        if len(timestamps) != 1 or next(iter(timestamps)) <= self.last_ts:
            raise ValueError('invalid synchronized universe time')
        self.last_ts = next(iter(timestamps))
        grouped = {s: [] for s in bars}
        for p in prints:
            if p.symbol not in grouped:
                raise ValueError('force-order outside configured universe')
            grouped[p.symbol].append(p)
        moves = {}
        for s,b in bars.items():
            old = list(self.markets[s].history)
            moves[s] = 10000*(b.close/old[-5].close-1) if len(old) >= 5 else 0.
        plans = []
        for s in sorted(bars):
            peers = [v for k,v in moves.items() if k != s]
            plans.extend(self.markets[s].observe(bars[s],grouped[s],float(np.median(peers))))
        return plans


def priority(plan: Plan) -> float:
    """Deterministic arbitration only; not a claimed win-probability estimate."""
    f = plan.features
    # A positive target after explicit costs is economic feasibility. No trade
    # quotas, symbol preference, score quantiles or selected-period thresholds.
    surplus = plan.gross_rr-f['cost_r']
    if surplus <= 0:
        return -1.
    return surplus*log1p(f['forced_intensity'])
