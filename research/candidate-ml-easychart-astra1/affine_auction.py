"""Causal three-anchor auction range and its first liquidity challenge.

EasyChart Channel pp6-9 motivates the hypothesis: three wick anchors define
context; a later fourth-point challenge needs a lower-frame reversal footprint.
The policy is experimental. Source descriptions are not evidence of profitability.
No asset-specific parameters, outcome inputs, target-R cap or transaction quota.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
import math
import numpy as np
from astra_policy import Observation, Plan, WickMap, MINUTE
from domain import Side

FEATURES = ('parent_minutes', 'width_range', 'slope_range', 'response_activity',
            'response_body', 'wave_progress', 'planned_rr', 'risk_bps',
            'risk_range', 'cost_r', 'rank')


def merge(rows):
    return Observation(rows[-1].ts, rows[0].open, max(x.high for x in rows),
                       min(x.low for x in rows), rows[-1].close,
                       sum(x.volume for x in rows), sum(x.buy for x in rows),
                       sum(x.quote for x in rows), sum(x.trades for x in rows))


@dataclass
class AuctionRange:
    key: str
    tf: int
    first: object
    middle: object
    third: object
    slope: float
    offset: float
    unit: float
    formed: int
    challenge: int = 0
    low: float = float('inf')
    high: float = -float('inf')
    consumed: bool = False
    invalid: bool = False

    def bounds(self, ts):
        a = self.first.price + self.slope * (ts - self.first.event_time_ns)
        b = a + self.offset
        return min(a, b), max(a, b)

    @property
    def side(self):
        return 1 if self.first.side == 'HIGH' else -1


class AuctionMarket:
    def __init__(self, symbol, tick):
        self.symbol, self.tick = symbol, tick
        self.history = []
        self.books = {tf: WickMap(symbol, tf, tick, pivot_spans=(2,)) for tf in (15, 60)}
        self.buckets = {tf: [] for tf in (5, 15, 60)}
        self.frames = {tf: [] for tf in self.buckets}
        self.swings = {tf: [] for tf in self.books}
        self.ranges, self.seen, self.touched = {}, set(), set()
        self.stats, self.explanations = Counter(), []

    def form(self, tf, created):
        for p in sorted(created, key=lambda q: (q.event_time_ns, q.side)):
            swings = self.swings[tf]
            if swings and swings[-1].side == p.side:
                more_extreme = (p.price > swings[-1].price if p.side == 'HIGH'
                                else p.price < swings[-1].price)
                if more_extreme:
                    swings[-1] = p
                else:
                    continue
            else:
                swings.append(p)
            if len(swings) < 3:
                continue
            a, b, c = swings[-3:]
            if c.event_time_ns <= a.event_time_ns:
                continue
            rise = c.price - a.price
            if (a.side == 'LOW' and rise < 0) or (a.side == 'HIGH' and rise > 0):
                continue
            slope = rise / (c.event_time_ns - a.event_time_ns)
            offset = b.price - (a.price + slope * (b.event_time_ns - a.event_time_ns))
            if (a.side == 'LOW' and offset <= 0) or (a.side == 'HIGH' and offset >= 0):
                continue
            bars = self.frames[tf]
            unit = max(float(np.median([x.high-x.low for x in bars[-24:]])), 2*self.tick)
            key = f'{self.symbol}:AFFINE:{tf}:{a.pivot_id}:{b.pivot_id}:{c.pivot_id}'
            if key in self.seen:
                continue
            self.seen.add(key)
            observed = [x for x in bars if a.event_time_ns <= x.ts <= c.observed_time_ns]
            bad = 0
            for x in observed:
                line = a.price + slope * (x.ts - a.event_time_ns)
                lo, hi = min(line, line+offset), max(line, line+offset)
                bad += not (lo-.1*unit <= x.close <= hi+.1*unit)
            if not observed or bad/len(observed) > .1:
                continue
            old = self.ranges.get(tf)
            if old is not None and old.challenge and not old.consumed and not old.invalid:
                continue
            self.ranges[tf] = AuctionRange(key, tf, a, b, c, slope, offset, unit,
                                          c.observed_time_ns)
            self.stats['three_anchor_range'] += 1

    def observe(self, b):
        if self.history and b.ts-self.history[-1].ts != MINUTE:
            raise ValueError('non-contiguous observed minutes')
        self.history.append(b)
        closed = []
        for book in self.books.values():
            for p in book.pivots[-40:]:
                if p.observed_time_ns >= b.ts or p.pivot_id in self.touched:
                    continue
                hit = b.high >= p.price if p.side == 'HIGH' else b.low <= p.price
                if hit:
                    self.touched.add(p.pivot_id)
        # Existing context is challenged before this observation forms new anchors.
        for r in self.ranges.values():
            if r.invalid or r.consumed or b.ts <= r.formed:
                continue
            lo, hi = r.bounds(b.ts)
            if not r.challenge:
                hit = b.low <= lo if r.side > 0 else b.high >= hi
                if not hit:
                    continue
                r.challenge = b.ts
                self.stats['fourth_point_challenge'] += 1
            r.low, r.high = min(r.low, b.low), max(r.high, b.high)
        for tf in sorted(self.buckets, reverse=True):
            self.buckets[tf].append(b)
            if b.ts//MINUTE % tf:
                continue
            rows, self.buckets[tf] = self.buckets[tf], []
            if len(rows) != tf:
                continue
            q = merge(rows)
            self.frames[tf].append(q)
            closed.append(tf)
            if tf in self.books:
                r = self.ranges.get(tf)
                if r is not None:
                    lo, hi = r.bounds(b.ts)
                    if q.close < lo or q.close > hi:
                        r.invalid = True
                        self.stats['parent_close_range_failure'] += 1
                self.form(tf, self.books[tf].observe(q))
        if 5 not in closed or len(self.frames[5]) < 25:
            return []
        p, q = self.frames[5][-2:]
        output = []
        for tf, r in self.ranges.items():
            if r.invalid or r.consumed or not r.challenge or q.ts < r.challenge:
                continue
            s = r.side
            lo, hi = r.bounds(q.ts)
            if not lo < q.close < hi:
                continue
            body, old = s*(q.close-q.open), -s*(p.close-p.open)
            if old <= self.tick or body < 2*old or s*(q.close-p.open) <= 0:
                continue
            # Only the first footprint can authorize this event; no later RR shopping.
            r.consumed = True
            self.stats['first_reversal_footprint'] += 1
            entry = q.close
            local_wave = max(q.high-q.low, p.high-p.low, 2*self.tick)
            stop = (r.low if s > 0 else r.high)-s*local_wave
            risk = s*(entry-stop)
            # Traversal duration is estimated from a completed historical leg.
            # This creates one numerical target, never changed after submission.
            arrival = q.ts+abs(r.middle.event_time_ns-r.first.event_time_ns)
            tlo, thi = r.bounds(arrival)
            objectives = [(thi if s > 0 else tlo, 'AFFINE_OPPOSITE_BOUNDARY')]
            for pv in self.books[tf].pivots[-40:]:
                if pv.observed_time_ns >= r.challenge or pv.pivot_id in self.touched:
                    continue
                if (pv.side == 'HIGH') == (s > 0):
                    objectives.append((pv.price, 'PARENT_UNSPENT_SWING'))
            objectives = [(v, k) for v, k in objectives if s*(v-entry) > self.tick]
            if not objectives or risk <= self.tick:
                continue
            target, kind = min(objectives, key=lambda v: s*(v[0]-entry))
            target -= s*self.tick
            rr = s*(target-entry)/risk
            if rr < 1:
                self.stats['first_response_no_economic_room'] += 1
                self.explanations.append(dict(ts=q.ts, symbol=self.symbol,
                    reason='first_response_no_economic_room', entry=entry,
                    stop=stop, target=target, rr=rr, event=r.key))
                continue
            activity = q.volume/max(float(np.mean([x.volume for x in self.frames[5][-25:-1]])), 1e-12)
            f = dict(parent_minutes=tf, width_range=abs(r.offset)/r.unit,
                slope_range=r.slope*tf*MINUTE/r.unit, response_activity=activity,
                response_body=body/r.unit, wave_progress=s*(entry-r.middle.price)/r.unit,
                planned_rr=rr, risk_bps=1e4*risk/entry, risk_range=risk/r.unit,
                cost_r=.0012*entry/risk, rank=math.log(tf)+math.log1p(activity),
                close_failure_clock=tf, close_failure_origin_time=r.first.event_time_ns,
                close_failure_origin_price=r.first.price, close_failure_slope=r.slope,
                close_failure_offset=r.offset, close_failure_side=s)
            self.stats['plans'] += 1
            output.append(Plan(r.key+f':{q.ts}', r.key, self.symbol,
                Side.LONG if s > 0 else Side.SHORT, q.ts, r.challenge,
                entry, stop, target, rr, lo if s > 0 else hi, tf, r.key, kind,
                min(p.open, p.close), max(p.open, p.close), r.high, r.low, f,
                family='AFFINE_AUCTION_FIRST_CHALLENGE'))
        return output


class AffineAuctionPolicy:
    def __init__(self, ticks):
        self.markets = {s: AuctionMarket(s, t) for s, t in ticks.items()}

    def observe(self, bars):
        if set(bars) != set(self.markets) or len({b.ts for b in bars.values()}) != 1:
            raise ValueError('synchronous market set required')
        return [p for s in sorted(bars) for p in self.markets[s].observe(bars[s])]
