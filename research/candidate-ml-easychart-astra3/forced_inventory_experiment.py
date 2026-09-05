"""Short real-data experiment for the forced-inventory hypothesis.

Reuses the existing Nautilus account, actual fees/funding and quantity contract.
No candidate-label aggregation is reported as portfolio performance. This driver
requires explicit acquisition coverage; isolated probe files are insufficient.

Run from repository root:
 python research/candidate-ml-easychart-astra3/forced_inventory_experiment.py \
   --manifest PATH --start YYYY-MM-DD --end YYYY-MM-DD --warmup-start YYYY-MM-DD

Manifest (all intervals end-exclusive; paths relative to repository root):
 {"complete_hours": {"BTCUSDT": ["2025-08-01T00:00:00Z", ...], ...},
  "files": ["astra3_cache/received/BTCUSDT/2025-08-01/00.parquet", ...]}
Coverage must come from successful acquisition, including empty archives. Never
infer complete coverage from the first/last observed liquidation timestamps.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
import json
import math
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import research as r
import pandas as pd
import numpy as np
from astra_policy import Observation, Plan, MINUTE, SYMBOLS
from domain import Side
from forced_inventory_response import ForcedPrint, ForcedInventoryPolicy, InventoryMarket, priority


def timestamp(value):
    t = pd.Timestamp(value)
    return int((t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')).value)


def read_prints(manifest, start_ns, end_ns):
    info = json.loads(Path(manifest).read_text())
    required = set(range(start_ns,end_ns,60*MINUTE))
    if start_ns%(60*MINUTE) or end_ns%(60*MINUTE):
        raise ValueError('coverage boundaries must be full UTC hours')
    for s in SYMBOLS:
        covered = {timestamp(t) for t in info['complete_hours'].get(s,[])}
        if not required <= covered:
            missing = sorted(required-covered)
            raise ValueError(f'{s}: {len(missing)} unacquired hours; absence is not zero forced flow')
    required_columns = {'received_time','event_time','symbol','side','average_price','last_filled_quantity'}
    output = []
    duplicates = set()
    for file in info['files']:
        path = Path(file)
        if path.suffix == '.parquet':
            frame = pd.read_parquet(path)
        elif ''.join(path.suffixes) in ('.csv','.csv.gz'):
            frame = pd.read_csv(path)
        else:
            raise ValueError(f'unsupported acquired format: {path}')
        if not required_columns <= set(frame.columns):
            raise ValueError(f'missing actual force-order columns: {path}')
        for row in frame.to_dict('records'):
            received = int(row['received_time'])
            event = int(row['event_time'])*1_000_000
            if not start_ns <= received < end_ns or row['symbol'] not in SYMBOLS:
                continue
            status = str(row.get('order_status',''))
            qty = float(row['last_filled_quantity'])
            if status not in ('FILLED','PARTIALLY_FILLED') or qty <= 0:
                continue
            direction = {'BUY':1,'SELL':-1}.get(str(row['side']))
            if direction is None:
                raise ValueError('unknown forced-order side')
            price = float(row['average_price'])
            trade = int(row.get('trade_time',row['event_time']))
            # No exchange order identifier is available in this archive schema.
            # Exact repeated broadcasts are deduplicated conservatively; they
            # are not claimed to be independent liquidated accounts.
            key = f'{row["symbol"]}:{trade}:{event}:{direction}:{price}:{qty}'
            if key in duplicates:
                continue
            duplicates.add(key)
            output.append(ForcedPrint(received,event,row['symbol'],direction,price,qty,key))
    return sorted(output,key=lambda p:(p.received_ns,p.symbol,p.key))


class FirstAbsorptionMarket(InventoryMarket):
    """Same economic event, earliest observable absorption rather than a retest.

This ablation addresses one market-logic question: does awaiting another test
remove the forced-flow edge? It does not adjust thresholds or target R.
"""
    def _advance(self,b,prints,peer_move):
        old = self.active
        prior_cross = None if old is None else old.crossed_price
        result = super()._advance(b,prints,peer_move)
        e = self.active
        if result or e is None or prior_cross is not None or e.crossed_price is None:
            return result
        side = -e.side
        stop = e.low-self.tick if side > 0 else e.high+self.tick
        risk = side*(b.close-stop)
        objectives = self._targets(side,b.close,b.ts,e)
        self.active = None
        if not objectives or risk <= self.tick:
            return []
        target = objectives[0]-side*self.tick
        reward = side*(target-b.close)
        if reward < risk:
            self.count('first_absorption_geometry_below_one_r')
            return []
        f = dict(cost_r=.0006*(b.close+stop)/risk,planned_rr=reward/risk,
            risk_range=risk/e.baseline_range,risk_bps=10000*risk/b.close,
            forced_intensity=e.force_quote/max(e.baseline_quote,1.),
            response_progress=side*(b.close-b.open)/e.baseline_range,
            response_pressure=side*b.delta/max(b.volume,1e-12),
            peer_response=side*peer_move,source_scale=float(e.scale),
            absorption_distance=side*(b.close-e.crossed_price)/e.baseline_range)
        return [Plan(e.key+':FIRST',e.key,self.symbol,
            Side.LONG if side > 0 else Side.SHORT,b.ts,e.born,b.close,stop,target,
            reward/risk,e.boundary,e.scale,e.key,'PRE_EVENT_VALUE_OR_OPPOSING_SWING',
            min(e.crossed_price,e.boundary),max(e.crossed_price,e.boundary),e.high,e.low,f,
            family='FIRST_FORCED_INVENTORY_ABSORPTION')]


class JoinedTape:
    """Concatenate actual adjacent price months, not completed account returns."""
    def __init__(self, months):
        tapes = [r.Tape(m) for m in months]
        self.symbols = tuple(SYMBOLS)
        self.instruments = tapes[0].instruments
        self.ticks = tapes[0].ticks
        self.raw = {s:pd.concat([t.raw[s] for t in tapes],ignore_index=True).sort_values('ts') for s in SYMBOLS}
        self.marks = {s:pd.concat([t.marks[s] for t in tapes],ignore_index=True).sort_values('ts') for s in SYMBOLS}
        self.mark_arrays = {s:(d.ts.to_numpy(dtype=np.int64),d.close.to_numpy(dtype=float)) for s,d in self.marks.items()}
        self.funding = sorted({row for t in tapes for row in t.funding})
        stamps = [d.ts.to_numpy(dtype=np.int64) for d in self.raw.values()]
        if not all(np.array_equal(stamps[0],x) for x in stamps) or np.any(np.diff(stamps[0]) != MINUTE):
            raise ValueError('price months are not an actual continuous four-market tape')

    def mark_at(self,s,t):
        stamps,values = self.mark_arrays[s]
        i = np.searchsorted(stamps,t,side='right')-1
        if i < 0 or t-stamps[i] > MINUTE:
            raise ValueError('no causally available mark')
        return float(values[i])


def proposals(tape,prints,start_ns,end_ns,first):
    policy = ForcedInventoryPolicy(tape.ticks)
    if first:
        policy.markets = {s:FirstAbsorptionMarket(s,t) for s,t in tape.ticks.items()}
    # Availability at a closed minute is conservative: a print received at the
    # exact boundary may be used then; later received prints are never backdated.
    buckets = defaultdict(list)
    for p in prints:
        close_ns = ((p.received_ns+MINUTE-1)//MINUTE)*MINUTE
        buckets[close_ns].append(p)
    frames = {s:d[(d.ts>start_ns)&(d.ts<=end_ns)].reset_index(drop=True) for s,d in tape.raw.items()}
    expected = (end_ns-start_ns)//MINUTE
    if not all(len(d)==expected for d in frames.values()):
        raise ValueError('incomplete actual minute prices in experiment')
    arrays = {s:d[['ts','open','high','low','close','volume','taker_buy_volume','quote_volume','count']].to_numpy() for s,d in frames.items()}
    output = []
    for i in range(expected):
        bars = {}
        for s,a in arrays.items():
            ts,o,h,l,c,v,b,q,n = a[i]
            bars[s] = Observation(int(ts),float(o),float(h),float(l),float(c),float(v),float(b),float(q),int(n))
        ts = next(iter(bars.values())).ts
        output.extend(policy.observe(bars,buckets.get(ts,())))
    return output,{s:m.stats for s,m in policy.markets.items()}


def run(manifest,start,end,warmup_start,output):
    a,b,z = map(timestamp,(warmup_start,start,end))
    if not a < b < z:
        raise ValueError('warmup_start < start < end required')
    # The research boundary is NOT allowed to censor entry signals using future
    # holding time; remaining exposure is closed and separately labelled by the
    # established Nautilus runner only at the final evaluation boundary.
    months = pd.period_range(pd.Timestamp(a,unit='ns',tz='UTC').strftime('%Y-%m'),
                             pd.Timestamp(z-1,unit='ns',tz='UTC').strftime('%Y-%m'),freq='M')
    tape = JoinedTape([str(m) for m in months])
    prints = read_prints(manifest,a,z)
    if not prints:
        raise ValueError('acquired interval contains no forced executions')
    r.OUT = Path(output)
    r.OUT.mkdir(parents=True,exist_ok=True)
    results = []
    for first in (True,False):
        name = 'first_absorption' if first else 'held_absorption'
        plans,counts = proposals(tape,prints,a,z,first)
        # Small prefix replay is a direct regression test for proposal causality.
        cut = b+min(2*1440*MINUTE,z-b)
        prefix,_ = proposals(tape,[p for p in prints if p.received_ns<cut],a,cut,first)
        left = [p.record() for p in plans if p.observed_time_ns<=cut]
        right = [p.record() for p in prefix]
        if left != right:
            raise AssertionError('future suffix changed an already emitted plan')
        scores = {p.plan_id:(priority(p),None) for p in plans}
        summary = r.backtest(tape,plans,scores,name,start,end)
        summary['hypothesis'] = name
        summary['received_time_liquidations'] = True
        summary['sampled_feed_not_total_liquidations'] = True
        summary['generation'] = counts
        summary['source_manifest'] = str(manifest)
        results.append(summary)
    (r.OUT/'results.json').write_text(json.dumps(results,indent=2,allow_nan=False))
    print(json.dumps(results,indent=2,allow_nan=False),flush=True)
    return results


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--manifest',required=True,type=Path)
    p.add_argument('--start',required=True)
    p.add_argument('--end',required=True)
    p.add_argument('--warmup-start',required=True)
    p.add_argument('--output',default='research_results/candidate_ml_easychart_astra3/forced_inventory_v24')
    run(**vars(p.parse_args()))
