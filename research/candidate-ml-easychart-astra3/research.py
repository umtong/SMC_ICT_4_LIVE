"""Short alpha experiments. Final trade statistics come from one Nautilus account.

Candidate labels are research-only, never summed into portfolio performance.
Training requires the whole label to have completed before the training cutoff.
The learner has no symbol ID, absolute price, date, future frame or outcome input.
"""
from __future__ import annotations
from pathlib import Path
import hashlib,json,math,pickle,sys,time,traceback
from decimal import Decimal
import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
for p in (ROOT/'candidate-easychart-v3',ROOT/'candidate-easychart-v5',ROOT/'candidate-easychart_re1',ROOT/'candidate-ml-easychart-astra1',HERE):
    sys.path.insert(0,str(p))
from experiment import load_bars,load_funding
from astra_policy import Observation,MINUTE,SYMBOLS
from hierarchy_policy import LiquidityPolicy,FEATURES as AUCTION_FEATURES
from extended_inputs import ExtraObservations,EXTRA_FEATURES
from executed_flow import ExecutedFlow,MICRO_FEATURES
FEATURES=AUCTION_FEATURES+EXTRA_FEATURES+MICRO_FEATURES
from execution import AstraStrategy,ExecutionLiquidity,FundingCashflows,make_engine,EasyChartMTFConfig,VENUE,USDT
from fee_profiles_v5 import make_instrument_with_fee_profile,FEE_PROFILES
from nautilus_trader.adapters.binance.common.types import BinanceBar
from nautilus_trader.model.data import BarType
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

OUT=Path('research_results/candidate_ml_easychart_astra3')
CACHE=Path('astra3_cache');CACHE.mkdir(exist_ok=True)
OUT.mkdir(parents=True,exist_ok=True)
LOG_GUARDS=[]  # Keep Nautilus' process-global logger alive across engine disposal.

def ns(x):return int(pd.Timestamp(x,tz='UTC').value)

class Tape:
    def __init__(self,month,symbols=SYMBOLS):
        self.month=month;self.symbols=tuple(symbols)
        self.raw={s:load_bars(s,month) for s in symbols}
        self.marks={s:load_bars(s,month,'markPriceKlines') for s in symbols}
        self.funding=[r for s in symbols for r in load_funding(s,month)]
        self.extra=ExtraObservations(month,self.raw)
        self.micro=ExecutedFlow(month,symbols)
        self.instruments={s:make_instrument_with_fee_profile(s,FEE_PROFILES['usd_m_vip0']) for s in symbols}
        self.ticks={s:float(i.price_increment) for s,i in self.instruments.items()}
        self.mark_arrays={s:(d.ts.to_numpy(dtype=np.int64),d.close.to_numpy(dtype=float)) for s,d in self.marks.items()}
        times=[d.ts.to_numpy(dtype=np.int64) for d in self.raw.values()]
        if not all(np.array_equal(times[0],t) for t in times):raise ValueError('unequal four-market clocks')
        if not np.all(np.diff(times[0])==MINUTE):raise ValueError('missing exchange minutes')
    def mark_at(self,s,t):
        stamps,prices=self.mark_arrays[s];i=np.searchsorted(stamps,t,side='right')-1
        if i<0 or t-stamps[i]>MINUTE:raise ValueError(f'missing observed mark: {s} {t}')
        return float(prices[i])
    def feature_mark_at(self,s,t):
        # Missing explanatory data stays missing. The learner supports NaN.
        # NAV/funding retain mark_at's strict actual-observation requirement.
        stamps,prices=self.mark_arrays[s]
        i=np.searchsorted(stamps,t,side='right')-1
        return float(prices[i]) if i>=0 and t-stamps[i]<=MINUTE else float('nan')
    def with_participation(self,value):
        from dataclasses import replace
        plans,stats=value
        attached=[]
        for p in plans:
            unit_bps=p.features['risk_bps']/p.features['risk_range']
            micro=self.micro.at(p.symbol,p.observed_time_ns,int(p.side.value),unit_bps)
            if micro is None:micro={k:float("nan") for k in MICRO_FEATURES}
            f=dict(p.features);f.update(micro)
            f.update(self.extra.at(p.symbol,p.observed_time_ns,int(p.side.value),unit_bps))
            attached.append(replace(p,features=f))
        return attached,stats
    def plans(self):
        source=b''.join((HERE/f).read_bytes() for f in ('policy.py','hierarchy_policy.py'))
        key=hashlib.sha256(source).hexdigest()[:16]
        path=CACHE/f'{self.month}-{key}-plans.pkl'
        if path.exists():return self.with_participation(pickle.loads(path.read_bytes()))
        policy=LiquidityPolicy(self.ticks,self.feature_mark_at,self.micro)
        arrays={s:d[['ts','open','high','low','close','volume','taker_buy_volume','quote_volume','count']].to_numpy() for s,d in self.raw.items()}
        plans=[]
        for i in range(len(next(iter(arrays.values())))):
            bars={}
            for s,a in arrays.items():
                t,o,h,l,c,v,b,q,n=a[i]
                bars[s]=Observation(int(t),float(o),float(h),float(l),float(c),float(v),float(b),float(q),int(n))
            plans.extend(policy.observe(bars))
        value=(plans,{s:dict(m.stats) for s,m in policy.markets.items()})
        path.write_bytes(pickle.dumps(value));print('CANDIDATES',self.month,len(plans),flush=True)
        return self.with_participation(value)
    def outcomes(self,plans):
        arrays={s:d[['ts','open','high','low','close']].to_numpy() for s,d in self.raw.items()}
        output=[]
        for p in plans:
            a=arrays[p.symbol];side=int(p.side.value)
            j=np.searchsorted(a[:,0],p.observed_time_ns,side='right')
            # Entry happens after the signal's closed bar, never at its historical
            # low/high. One basis point is the small-account label assumption.
            entry=p.entry*(1+side*.0001)
            risk=abs(p.entry-p.stop)+.0006*(p.entry+p.stop)+p.entry*.0001
            exit_price=None;stop=False;ambiguous=False;closed=None
            for k in range(j,len(a)):
                t,o,h,l,c=a[k]
                sl=(l<=p.stop if side>0 else h>=p.stop)
                tp=(h>p.target+self.ticks[p.symbol]*.5 if side>0 else l<p.target-self.ticks[p.symbol]*.5)
                if not sl and not tp:continue
                ambiguous=bool(sl and tp)
                if sl:
                    stop=True;exit_price=(min(p.stop,o) if side>0 else max(p.stop,o))*(1-side*.0001)
                else:exit_price=p.target
                closed=int(t);break
            if closed is None:continue
            fee=.0005*entry+(.0005 if stop else .0002)*exit_price
            funding=sum(-side*self.mark_at(s,t)*r for t,s,r in self.funding if s==p.symbol and p.observed_time_ns<t<=closed)
            net_r=(side*(exit_price-entry)-fee+funding)/risk
            row=p.record();row.update(label_closed=closed,label_net_r=float(net_r),label_win=int(net_r>0),label_target=int(not stop),
                                     label_hold=(closed-p.observed_time_ns)/MINUTE,label_ambiguous=ambiguous)
            output.append(row)
        return pd.DataFrame(output)

def fit_decision(labels,train_end,calibration_end):
    from models import fit_offset
    train=labels[labels.label_closed<ns(train_end)].copy()
    calibration=labels[(labels.observed_time_ns>=ns(train_end))&(labels.label_closed<ns(calibration_end))].copy()
    decision,details=fit_offset(train,calibration,FEATURES)
    details.update(train_end=train_end,calibration_end=calibration_end)
    print('FIT_OFFSET',json.dumps(details),flush=True)
    return decision,details

class AccountStrategy(AstraStrategy):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.used_events=set();self.last_episode_close=0
    def _submit_plan(self,iid,plan):
        if plan.causal_event_id in self.used_events or plan.interaction_time_ns<=self.last_episode_close:
            return False
        answer=super()._submit_plan(iid,plan)
        if answer:self.used_events.add(plan.causal_event_id)
        return answer
    def on_position_closed(self,event):
        self.last_episode_close=max(self.last_episode_close,int(event.ts_closed))
        super().on_position_closed(event)

class ReplayPolicy:
    """Replay already-causal plans, retaining observed history for execution sizing.

    Training is outside the strategy. The same LiquidityPolicy generates live
    plans; this replay only avoids recomputing its geometry in each experiment.
    """
    def __init__(self,tape,plans):
        self.markets={s:type('ObservedMarket',(),{'history':[],'stats':Counter(),'explanations':[]})() for s in tape.symbols}
        self.by_time={}
        for p in plans:self.by_time.setdefault(p.observed_time_ns,[]).append(p)
    def observe(self,bars):
        for s,b in bars.items():self.markets[s].history.append(b)
        return self.by_time.get(next(iter(bars.values())).ts,[])

from collections import Counter

def backtest(tape,plans,scores,name,start,end,starting_nav=100000.,stress=1.):
    t0=time.time();start_ns=ns(start);end_ns=ns(end)
    a=max(int(next(iter(tape.raw.values())).ts.iloc[0])-MINUTE,start_ns-3*1440*MINUTE)
    liquidity=ExecutionLiquidity(stress)
    funding=FundingCashflows([r for r in tape.funding if a<r[0]<=end_ns],tape.mark_at)
    engine=make_engine(funding,liquidity,starting_nav)
    if engine.kernel._log_guard is not None:LOG_GUARDS.append(engine.kernel._log_guard)
    types=[];instruments=[]
    for s,inst in tape.instruments.items():
        instruments.append(inst);engine.add_instrument(inst)
        bt=BarType.from_str(f'{inst.id}-1-MINUTE-LAST-EXTERNAL');types.append(bt)
        d=tape.raw[s];d=d[(d.ts>a)&(d.ts<=end_ns)]
        bars=[BinanceBar(bar_type=bt,open=inst.make_price(r.open),high=inst.make_price(r.high),low=inst.make_price(r.low),close=inst.make_price(r.close),volume=inst.make_qty(r.volume),quote_volume=Decimal(str(r.quote_volume)),count=int(r.count),taker_buy_base_volume=Decimal(str(r.taker_buy_volume)),taker_buy_quote_volume=Decimal(str(r.taker_buy_quote_volume)),ts_event=int(r.ts),ts_init=int(r.ts)) for r in d.itertuples(index=False)]
        engine.add_data(bars,sort=False)
    engine.sort_data()
    config=EasyChartMTFConfig(instrument_ids=tuple(i.id for i in instruments),higher_bar_types=tuple(types),decision_bar_types=tuple(types),trigger_bar_types=tuple(types),execution_bar_types=tuple(types),risk_fraction=.03,min_gross_rr=1.,estimated_entry_fee_rate=.0005,estimated_stop_fee_rate=.0005,trading_start_ns=start_ns)
    router=lambda p:scores.get(p.plan_id,(-1.,0.))[0]
    strategy=AccountStrategy(config,ReplayPolicy(tape,plans),liquidity,tape.mark_at,router=router)
    engine.add_strategy(strategy)
    try:
        engine.run()
        # Nautilus does not dispatch strategy callbacks after on_stop. A terminal
        # market close still executes; use that actual cached fill, not a made-up
        # mark-to-market trade, and keep it out of natural completed-trade counts.
        pending=strategy.active_plan
        if pending is not None and pending.plan_id not in {x['plan_id'] for x in strategy.closed}:
            actual=[x for x in engine.cache.positions_closed() if x.opening_order_id==strategy.active_entry_id]
            if len(actual)!=1:raise RuntimeError('terminal position has no unique actual completed execution')
            pos=actual[0];row=pending.record();row.update(strategy.open_context[pending.plan_id])
            row.update(position_id=str(pos.id),opened=int(pos.ts_opened),closed=int(pos.ts_closed),
                       entry_fill=float(pos.avg_px_open),exit_fill=float(pos.avg_px_close),quantity=float(pos.peak_qty),
                       pnl_ex_funding=pos.realized_pnl.as_double(),holding_minutes=(pos.ts_closed-pos.ts_opened)/MINUTE,
                       evaluation_censored=True)
            strategy.closed.append(row)
        trades=pd.DataFrame(strategy.closed);payments=pd.DataFrame(funding.payments)
        if len(trades):
            trades['funding']=[float(payments.loc[(payments.symbol==r.symbol)&(payments.ts>r.opened)&(payments.ts<=r.closed),'cash'].sum()) if len(payments) else 0. for r in trades.itertuples()]
            trades['pnl']=trades.pnl_ex_funding+trades.funding;trades['net_r']=trades.pnl/(.03*trades.nav_before)
            trades['probability']=[scores.get(p,(0.,0.))[1] for p in trades.plan_id]
        nav=engine.portfolio.account(VENUE).balance_total(USDT).as_double()
        curve=np.array([starting_nav]+[n for _,n in strategy.nav_path]+[nav]);dd=1-curve/np.maximum.accumulate(curve)
        natural=trades[~trades.evaluation_censored] if len(trades) else trades
        n=len(natural)
        summary={'name':name,'start':start,'end_exclusive':end,'days':(end_ns-start_ns)/(1440*MINUTE),'completed_trades':n,
                 'wins':int((natural.pnl>0).sum()) if n else 0,'win_rate':float((natural.pnl>0).mean()) if n else None,
                 'initial_nav':starting_nav,'final_nav':nav,'return_pct':100*(nav/starting_nav-1),'max_mark_nav_drawdown':float(dd.max()),
                 'mean_net_r':float(natural.net_r.mean()) if n else None,'mean_planned_rr':float(natural.gross_rr.mean()) if n else None,
                 'mean_hold_minutes':float(natural.holding_minutes.mean()) if n else None,'censored_trades':len(trades)-n,
                 'funding_cash':float(payments.cash.sum()) if len(payments) else 0.,'elapsed_seconds':time.time()-t0,
                 'by_family':{},'by_symbol':{}}
        for key,group in [('family','by_family'),('symbol','by_symbol')]:
            if n:
                summary[group]={str(k):{'trades':len(g),'win_rate':float((g.pnl>0).mean()),'net_r':float(g.net_r.mean())} for k,g in natural.groupby(key)}
        path=OUT/name;path.mkdir(exist_ok=True)
        trades.to_csv(path/'trades.csv',index=False)
        pd.DataFrame(strategy.nav_path,columns=['ts','nav']).to_csv(path/'nav.csv',index=False)
        (path/'summary.json').write_text(json.dumps(summary,indent=2))
        if len(trades) and abs((nav-starting_nav)-trades.pnl.sum())>.03:
            engine.trader.generate_order_fills_report().tail(20).to_json(path/'last_fills.json',orient='records',date_format='iso',default_handler=str)
            engine.trader.generate_positions_report().tail(8).to_json(path/'last_positions.json',orient='records',date_format='iso',default_handler=str)
            engine.trader.generate_account_report(VENUE).tail(12).to_json(path/'last_account.json',orient='records',date_format='iso',default_handler=str)
            (path/'active_execution.json').write_text(json.dumps({'open_positions':len(engine.cache.positions_open()),'closed_rows':len(trades),'active_plan':str(strategy.active_plan),'active_entry':str(strategy.active_entry_id)},indent=2))
        if len(trades) and not engine.cache.positions_open() and abs((nav-starting_nav)-trades.pnl.sum())>.03:
            raise AssertionError(f'account residual={nav-starting_nav-trades.pnl.sum():.12f}; wallet={nav}; attributed={trades.pnl.sum()}; funding={summary["funding_cash"]}')
        print('ACCOUNT',json.dumps(summary),flush=True)
        return summary
    finally:engine.dispose()

def label_summary(labels):
    return {str(k):{'candidates':len(g),'win_rate':float(g.label_win.mean()),'mean_net_r':float(g.label_net_r.mean()),
                   'mean_rr':float(g.gross_rr.mean()),'median_hold':float(g.label_hold.median()),'ambiguous':int(g.label_ambiguous.sum())} for k,g in labels.groupby('family')}

def main():
    global FEATURES
    (OUT/'error.txt').unlink(missing_ok=True)
    request=json.loads((HERE/'request.json').read_text())
    FEATURES=tuple(request.get('features',FEATURES))
    tapes={m:Tape(m) for m in request['months']};allplans={};labels=[];generation={}
    for month,tape in tapes.items():
        plans,stats=tape.plans();allplans[month]=plans
        data=tape.outcomes(plans)
        if len(data):data['month']=month;labels.append(data)
        generation[month]={'mechanisms':stats,'labels':label_summary(data) if len(data) else {}}
    labels=pd.concat(labels,ignore_index=True)
    decision,fit=fit_decision(labels,request['train_end'],request['calibration_end'])
    (OUT/'generation.json').write_text(json.dumps(generation,indent=2));(OUT/'fit.json').write_text(json.dumps(fit,indent=2))
    (OUT/'decision.pkl').write_bytes(pickle.dumps(decision))
    observations=[]
    for job in request['experiments']:
        month=job['month'];plans=allplans[month]
        scores=decision.scores(plans)
        if job.get('raw',False):scores={p.plan_id:(1.-p.features['cost_r'],.5) for p in plans}
        observations.append(backtest(tapes[month],plans,scores,job['name'],job['start'],job['end'],stress=job.get('stress',1.)))
        subset=labels[(labels.month==month)&(labels.observed_time_ns>=ns(job['start']))&(labels.observed_time_ns<ns(job['end']))].copy()
        if len(subset):
            subset['forecast']=[scores.get(i,(-1,0))[1] for i in subset.plan_id]
            subset['utility']=[scores.get(i,(-1,0))[0] for i in subset.plan_id]
            subset.to_csv(OUT/job['name']/'candidate_outcomes.csv',index=False)
            print('FORECAST',job['name'],subset.groupby(pd.cut(subset.forecast,[0,.4,.5,.6,.7,.8,1.],include_lowest=True),observed=True).agg(n=('label_win','size'),win=('label_win','mean'),net_r=('label_net_r','mean'),forecast=('forecast','mean')).to_json(),flush=True)
    (OUT/'latest.json').write_text(json.dumps(observations,indent=2));(OUT/'error.txt').unlink(missing_ok=True)

if __name__=='__main__':
    try:main()
    except Exception:
        (OUT/'error.txt').write_text(traceback.format_exc());raise
