"""Actual-tape short diagnosis; no new broker/accounting implementation.

Uses the existing archived five-second aggTrade observations. Higher context
is warmed from preceding completed one-minute bars, then updated only after
12 observed five-second intervals. Actual orders run through the existing
Nautilus strategy, protective orders, fees, funding and mark-NAV accounting.
"""
from pathlib import Path
from collections import defaultdict
import hashlib,pickle
import numpy as np
import pandas as pd
from astra_policy import Observation,MINUTE
from execution import ExecutionLiquidity
from nested_control import NestedControlMarket
from micro_response import MicroResponsePolicy,SECOND
from nautilus_trader.model.data import BarType as NativeBarType

class MicroBarTypes:
    @staticmethod
    def from_str(value):
        return NativeBarType.from_str(value.replace('-1-MINUTE-LAST-EXTERNAL','-5-SECOND-LAST-EXTERNAL'))

class MinuteLiquidity(ExecutionLiquidity):
    def __init__(self,stress,seed):
        super().__init__(stress);self.pending=defaultdict(list)
        for symbol,rows in seed.items():
            for b in rows:super().observe(symbol,b)
    def observe(self,symbol,b):
        self.pending[symbol].append(b)
        if b.ts%MINUTE==0:
            rows=self.pending.pop(symbol)
            if len(rows)!=12:raise ValueError('partial execution-liquidity minute')
            quote=sum(max(x.quote,x.volume*x.close) for x in rows)
            self.activity[symbol].append(quote)

def observation(r):
    return Observation(int(r.ts),float(r.open),float(r.high),float(r.low),float(r.close),
                       float(r.volume),float(r.taker_buy_volume),float(r.quote_volume),int(r.count))

def load_micro(root,symbol,start,end):
    frames=[]
    for day in pd.date_range(start,pd.Timestamp(end)-pd.Timedelta(days=1),freq='D'):
        path=root/f'{symbol}-{day:%Y-%m-%d}-5s.parquet'
        if not path.exists():raise FileNotFoundError(path)
        d=pd.read_parquet(path).copy();d['ts']=d.index.to_numpy(dtype=np.int64)*1_000_000
        frames.append(d)
    d=pd.concat(frames).sort_values('ts').drop_duplicates('ts').set_index('ts')
    begin=int(pd.Timestamp(start,tz='UTC').value);finish=int(pd.Timestamp(end,tz='UTC').value)
    d=d.reindex(np.arange(begin+5*SECOND,finish+1,5*SECOND,dtype=np.int64))
    d['close']=d.close.ffill()
    if d.close.isna().any():raise ValueError('missing initial observed trade price')
    for col in ('open','high','low'):d[col]=d[col].fillna(d.close)
    for col in ('volume','quote_volume','buy_volume','buy_quote','trades'):d[col]=d[col].fillna(0.)
    d=d.rename(columns={'buy_volume':'taker_buy_volume','buy_quote':'taker_buy_quote_volume','trades':'count'})
    d.index.name='ts';return d.reset_index()

class MicroTape:
    def __init__(self,base,month,start,end,symbols):
        parent=base.Tape(month)
        self.symbols=tuple(symbols);self.month=month
        self.raw_minute={s:parent.raw[s] for s in symbols}
        self.marks={s:parent.marks[s] for s in symbols};self.mark_arrays={s:parent.mark_arrays[s] for s in symbols}
        self.mark_at=parent.mark_at
        self.instruments={s:parent.instruments[s] for s in symbols};self.ticks={s:parent.ticks[s] for s in symbols}
        self.funding=[x for x in parent.funding if x[1] in symbols]
        root=Path('astra_control_cache/tape-context')
        self.raw={s:load_micro(root,s,start,end) for s in symbols}
        self.begin=base.ns(start)
        self.seed={s:[observation(r) for r in d[(d.ts<=self.begin)&(d.ts>self.begin-15*MINUTE)].itertuples(index=False)] for s,d in self.raw_minute.items()}
        self.base=base
    def plans(self):
        names=('micro_response.py','nested_control.py','local_response.py','liquidity_control.py','reclaimed_liquidity.py')
        digest=hashlib.sha256(b''.join((self.base.HERE/f).read_bytes() for f in names)).hexdigest()[:20]
        key=f'{self.month}-{self.begin}-{len(next(iter(self.raw.values())))}-{digest}'
        path=Path('astra_control_cache')/f'micro-{key}.pkl'
        if path.exists():return pickle.loads(path.read_bytes())
        policy=MicroResponsePolicy(self.ticks)
        for s,m in policy.markets.items():
            d=self.raw_minute[s];d=d[(d.ts>self.begin-3*1440*MINUTE)&(d.ts<=self.begin)]
            for r in d.itertuples(index=False):NestedControlMarket.observe(m,observation(r))
        arrays={s:list(d.itertuples(index=False)) for s,d in self.raw.items()}
        plans=[]
        for j in range(len(next(iter(arrays.values())))):
            plans.extend(policy.observe({s:observation(rows[j]) for s,rows in arrays.items()}))
        stats={s:dict(m.stats) for s,m in policy.markets.items()}
        result=plans,stats;path.write_bytes(pickle.dumps(result))
        return result

def execute(base,request):
    if any(j.get('learned',True) for j in request['experiments']):raise ValueError('micro diagnosis is not a fitted policy')
    base.prepare(request['months']);base.BarType=MicroBarTypes
    results=[];opportunities={}
    for job in request['experiments']:
        cfg=dict(job);month=cfg.pop('month');cfg.pop('learned',None)
        symbols=cfg.pop('symbols',['BTCUSDT'])
        tape=MicroTape(base,month,cfg['start'],cfg['end'],symbols);plans,stats=tape.plans()
        opportunities[cfg['name']]={'symbols':symbols,'plans':len(plans),'stats':stats}
        base.write(base.OUT/'opportunities.json',opportunities)
        base.ExecutionLiquidity=lambda stress:MinuteLiquidity(stress,tape.seed)
        result=base.backtest(tape,plans,None,**cfg)
        result.update(execution_clock_seconds=5,structural_clock_seconds=60,
                      liquidity_cost_clock_seconds=60,symbols=symbols,
                      interpretation='short timing diagnosis, not a four-symbol final-system result')
        results.append(result);base.write(base.OUT/'latest.json',results)
    (base.OUT/'error.txt').unlink(missing_ok=True)
