"""Liquidity-owned displacement -> first return -> completed price response.

Source-derived relations: EasyChart OB pp3-6, FVG pp5-7, Fakeout pp6-7,11.
A footprint is an entry location, not a standalone reason for direction. Its
parent must have challenged a previously observed public swing. A rejected
challenge and an accepted break share one control/return state machine.

Research translations: 5m/15m decision scales, two-right-bar public pivots,
first completed directional minute at the returned footprint. These are
hypotheses, not claims about the source's exact discretionary rules.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from collections import Counter, deque
import math
import numpy as np
from astra_policy import Observation, Plan, MINUTE
from domain import Side
from policy import Frame

FEATURES = ('rejection', 'scale', 'higher_direction', 'context_location',
            'impulse_progress', 'impulse_efficiency', 'impulse_pressure',
            'return_depth', 'return_pressure', 'return_effort',
            'response_progress', 'response_pressure', 'peer_progress',
            'source_penetration', 'source_distance', 'risk_range',
            'cost_r', 'planned_rr')

@dataclass
class Challenge:
    key: str
    kind: int
    level: float
    scale: int
    started: int
    high: float
    low: float
    index: int

@dataclass
class ReturnWave:
    key: str
    source: Challenge
    side: int
    scale: int
    born: int
    first: int
    low: float
    high: float
    stop: float
    peak: float
    unit: float
    impulse_volume: float
    impulse_count: int
    impulse_delta: float
    impulse_progress: float
    impulse_efficiency: float
    rejection: bool
    returned: bool = False
    return_first: int = 0
    return_volume: float = 0.
    return_delta: float = 0.
    return_count: int = 0
    target: float | None = None

class Market:
    def __init__(self, symbol: str, tick: float):
        self.symbol, self.tick = symbol, tick
        self.frames = {n: Frame(n) for n in (5, 15, 60)}
        self.history = deque(maxlen=1441)
        self.stats = Counter()
        self.explanations = []
        self.last_ts = 0
        self.challenges = {}
        self.waves = {}
        self.claimed_sources = set()

    def _unit(self, tf):
        rows = self.frames[tf].bars[-48:]
        return max(self.tick, float(np.median([x.high-x.low for x in rows]))) if rows else self.tick

    def _public_challenges(self, b):
        # The current bar cannot use a pivot published at its own timestamp.
        hits = []
        for tf in (15, 60):
            for z in self.frames[tf].levels:
                if z.consumed or z.born >= b.ts:
                    continue
                touched = b.high >= z.price if z.kind > 0 else b.low <= z.price
                if touched:
                    z.consumed = True
                    hits.append(z)
        for kind in (-1, 1):
            same = [z for z in hits if z.kind == kind]
            if same:
                z = max(same, key=lambda x: (x.tf, x.born))
                self.challenges[kind] = Challenge(z.key, kind, z.price, z.tf,
                                                b.ts, b.high, b.low, len(self.frames[5].bars))
                self.stats['public_challenge'] += 1
        for c in self.challenges.values():
            c.high = max(c.high, b.high)
            c.low = min(c.low, b.low)

    def _footprints(self, tf, x):
        bars = self.frames[tf].bars
        if len(bars) < 48:
            return
        a, p, b = bars[-3:]
        typical = max(self.tick, float(np.median([abs(v.close-v.open) for v in bars[-49:-1]])))
        for side in (-1, 1):
            ob = (side*(p.close-p.open) < 0 and side*(b.close-p.open) > 0
                  and side*(b.close-b.open) >= 2*abs(p.close-p.open)
                  and abs(p.close-p.open) >= .1*typical)
            gap = (b.low > a.high if side > 0 else b.high < a.low)
            gap = gap and side*(p.close-p.open) >= 2*max(abs(a.close-a.open), abs(b.close-b.open), self.tick)
            if not ob and not gap:
                continue
            formation_first = p.ts-tf*MINUTE if ob else a.ts-tf*MINUTE
            candidates = []
            for c in self.challenges.values():
                # One causal formation/return, not a remotely inherited old sweep.
                if c.started < formation_first or c.started > b.ts:
                    continue
                rejection = c.kind == -side
                controlled = side*(b.close-c.level) > 0
                if controlled:
                    candidates.append((rejection, c))
            if not candidates:
                continue
            rejection, c = max(candidates, key=lambda item: (item[1].scale, item[1].started, item[0]))
            if c.key in self.claimed_sources:
                continue
            zones = []
            if ob:
                zones.append((*sorted((p.open, p.close)), (p, b), 'OB'))
            if gap:
                lo, hi = (a.high, b.low) if side > 0 else (b.high, a.low)
                zones.append((lo, hi, (a, p, b), 'FVG'))
            # Choose the source-nearest actual footprint, not a fitted R target.
            lo, hi, formation, kind = min(zones, key=lambda z: abs((z[0]+z[1])/2-c.level))
            if side*(b.close-(hi if side > 0 else lo)) <= 0:
                continue
            stop = min(v.low for v in formation)-self.tick if side > 0 else max(v.high for v in formation)+self.tick
            if rejection:
                stop = min(stop, c.low-self.tick) if side > 0 else max(stop, c.high+self.tick)
            unit = self._unit(tf)
            volume = sum(v.volume for v in formation)
            delta = sum(v.delta for v in formation)
            progress = side*(b.close-formation[0].open)
            variation = sum(abs(v.close-v.open) for v in formation)
            key = f'{self.symbol}:CONTROL:{c.key}'
            self.waves[side] = ReturnWave(key, replace(c), side, tf, b.ts, formation_first,
                lo, hi, stop, b.high if side > 0 else b.low, unit,
                volume, len(formation)*tf, delta, progress/unit,
                progress/max(variation, self.tick), rejection)
            self.claimed_sources.add(c.key)
            self.stats['rejected_control' if rejection else 'accepted_control'] += 1

    def _objective(self, wave, b):
        side = wave.side
        prices = [wave.peak]
        for tf in (15, 60):
            prices += [z.price for z in self.frames[tf].levels
                       if z.born < b.ts and not z.consumed and z.kind == side]
        prices = [p for p in prices if side*(p-b.close) > self.tick]
        return min(prices, key=lambda p: side*(p-b.close))-side*self.tick if prices else None

    def _return(self, b, peer):
        plans = []
        for side, wave in list(self.waves.items()):
            if b.ts <= wave.born:
                continue
            if b.low <= wave.stop if side > 0 else b.high >= wave.stop:
                self.stats['control_invalidated'] += 1
                del self.waves[side]
                continue
            if not wave.returned:
                touch = b.low <= wave.high if side > 0 else b.high >= wave.low
                if not touch:
                    wave.peak = max(wave.peak, b.high) if side > 0 else min(wave.peak, b.low)
                    continue
                wave.returned = True
                wave.return_first = b.ts
                wave.target = self._objective(wave, b)
                self.stats['first_return'] += 1
            elif b.high >= wave.peak if side > 0 else b.low <= wave.peak:
                self.stats['return_wave_finished'] += 1
                del self.waves[side]
                continue
            wave.return_volume += b.volume
            wave.return_delta += b.delta
            wave.return_count += 1
            if wave.target is None:
                del self.waves[side]
                continue
            if b.high >= wave.target if side > 0 else b.low <= wave.target:
                self.stats['objective_spent_before_entry'] += 1
                del self.waves[side]
                continue
            # A completed directional response at the intended area, not a
            # delayed breakout entry after the available reward has been spent.
            if side*(b.close-b.open) <= 0:
                continue
            located = b.low <= wave.high and b.high >= wave.low
            if not located:
                continue
            risk = side*(b.close-wave.stop)
            reward = side*(wave.target-b.close)
            if risk <= self.tick or reward < risk:
                self.stats['first_response_geometry_unavailable'] += 1
                del self.waves[side]
                continue
            hist = list(self.history)
            recent = hist[-60:]
            low, high = min(v.low for v in recent), max(v.high for v in recent)
            volume_rate = wave.impulse_volume/max(wave.impulse_count, 1)
            f = dict(rejection=float(wave.rejection), scale=math.log2(wave.scale/5),
                higher_direction=side*self.frames[60].direction(),
                context_location=side*(2*(b.close-low)/max(high-low, self.tick)-1),
                impulse_progress=wave.impulse_progress, impulse_efficiency=wave.impulse_efficiency,
                impulse_pressure=side*wave.impulse_delta/max(wave.impulse_volume, 1e-12),
                return_depth=side*(wave.peak-b.close)/max(side*(wave.peak-wave.stop), self.tick),
                return_pressure=side*wave.return_delta/max(wave.return_volume, 1e-12),
                return_effort=(wave.return_volume/wave.return_count)/max(volume_rate, 1e-12),
                response_progress=side*(b.close-b.open)/wave.unit,
                response_pressure=side*b.delta/max(b.volume, 1e-12), peer_progress=side*peer,
                source_penetration=(wave.source.high-wave.source.level)/wave.unit if wave.source.kind>0 else (wave.source.level-wave.source.low)/wave.unit,
                source_distance=side*(b.close-wave.source.level)/wave.unit,
                risk_range=risk/wave.unit, cost_r=.0006*(b.close+wave.stop)/risk,
                planned_rr=reward/risk)
            f = {k:float(v) for k,v in f.items()}
            plans.append(Plan(f'{wave.key}:{b.ts}', wave.key, self.symbol,
                Side.LONG if side>0 else Side.SHORT, b.ts, wave.source.started,
                b.close, wave.stop, wave.target, reward/risk, wave.source.level,
                wave.source.scale, wave.source.key, 'FIRST_RETURN_WAVE_OR_OPPOSING_SWING',
                wave.low, wave.high, max(wave.peak, b.high), min(wave.peak,b.low), f,
                family='REJECTED_CONTROL_RETURN' if wave.rejection else 'ACCEPTED_CONTROL_RETURN'))
            self.stats['plan'] += 1
            del self.waves[side]
        return plans

    def observe(self, b, peer):
        if self.last_ts and b.ts-self.last_ts != MINUTE:
            raise ValueError('non-contiguous completed observation clock')
        self.last_ts = b.ts
        self.history.append(b)
        plans = self._return(b, peer)
        self._public_challenges(b)
        closed = {tf:f.append(b) for tf,f in self.frames.items()}
        # A broader formation owns a simultaneous nested footprint.
        for tf in (15,5):
            if closed[tf] is not None:
                self._footprints(tf, closed[tf])
        return plans

class ControlWavePolicy:
    def __init__(self, ticks):
        self.markets = {s:Market(s,t) for s,t in ticks.items()}
        self.last_ts = 0
    def observe(self, bars):
        if set(bars) != set(self.markets) or len({b.ts for b in bars.values()}) != 1:
            raise ValueError('incomplete synchronous universe')
        ts = next(iter(bars.values())).ts
        if ts <= self.last_ts:
            raise ValueError('non-increasing policy clock')
        self.last_ts = ts
        moves = {s:(b.close-list(self.markets[s].history)[-15].close)/self.markets[s]._unit(5)
                 for s,b in bars.items() if len(self.markets[s].history)>=15}
        output=[]
        for s in sorted(bars):
            values=[v for key,v in moves.items() if key!=s]
            peer=float(np.median(values)) if values else 0.
            output.extend(self.markets[s].observe(bars[s],peer))
        return output
