"""Reuses Astra/RE1 data, instrument, execution, protection and NAV components.

Counterfactual candidate labels train the decision model; ONLY the Nautilus
single-account executions below are portfolio results. No performance target
is used as a training label, threshold, risk modifier, or trade-count quota.
"""
from __future__ import annotations
from pathlib import Path
import concurrent.futures,hashlib,json,math,pickle,sys,time,traceback,urllib.request
from collections import Counter
from decimal import Decimal
import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent
for p in (HERE.parent/'candidate-easychart-v3',HERE.parent/'candidate-easychart-v5',HERE.parent/'candidate-easychart_re1',HERE):
    sys.path.insert(0,str(p))
from astra_policy import Observation,MINUTE,SYMBOLS
from control_v2 import ControlPolicy,FEATURES
from execution import AstraStrategy,ExecutionLiquidity,FundingCashflows,make_engine,EasyChartMTFConfig,VENUE,USDT
from experiment import load_bars,load_funding,MARKET
from fee_profiles_v5 import make_instrument_with_fee_profile,FEE_PROFILES
from nautilus_trader.adapters.binance.common.types import BinanceBar
from nautilus_trader.model.data import BarType
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

OUT=Path('research_results/astra1_control_v2');OUT.mkdir(parents=True,exist_ok=True)
CACHE=Path('astra_control_cache');CACHE.mkdir(exist_ok=True)
LOG_GUARDS=[]

def ns(x):return int(pd.Timestamp(x,tz='UTC').value)
def write(path,obj):path.write_text(json.dumps(obj,indent=2,default=str)+'\n')

def prepare(months):
    jobs=[]
    for month in months:
        for s in SYMBOLS:
            for typ in ('klines','markPriceKlines','fundingRate'):
                name=f'{s}-fundingRate-{month}.zip' if typ=='fundingRate' else f'{s}-1m-{month}.zip'
                dest=MARKET/typ/s/name
                extra='' if typ=='fundingRate' else '/1m'
                url=f'https://data.binance.vision/data/futures/um/monthly/{typ}/{s}{extra}/{name}'
                jobs.append((url,dest))
    def get(job):
        url,dest=job;dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(url,timeout=90) as r:data=r.read()
                    dest.write_bytes(data);break
                except Exception:
                    if attempt==2:raise
                    time.sleep(1+attempt)
        return {'url':url,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:records=list(ex.map(get,jobs))
    write(OUT/'inputs.json',records)

class Tape:
    def __init__(self,month):
        self.month=month;self.symbols=SYMBOLS
        self.raw={s:load_bars(s,month) for s in SYMBOLS}
        self.marks={s:load_bars(s,month,'markPriceKlines') for s in SYMBOLS}
        self.funding=[r for s in SYMBOLS for r in load_funding(s,month)]
        self.instruments={s:make_instrument_with_fee_profile(s,FEE_PROFILES['usd_m_vip0']) for s in SYMBOLS}
        self.ticks={s:float(x.price_increment) for s,x in self.instruments.items()}
        times=[d.ts.to_numpy(dtype=np.int64) for d in self.raw.values()]
        if not all(np.array_equal(times[0],t) for t in times) or not np.all(np.diff(times[0])==MINUTE):
            raise ValueError('four-market clock is not complete and synchronous')
        self.mark_arrays={s:(d.ts.to_numpy(dtype=np.int64),d.close.to_numpy(dtype=float)) for s,d in self.marks.items()}
    def mark_at(self,s,t):
        stamps,prices=self.mark_arrays[s];j=np.searchsorted(stamps,t,side='right')-1
        if j<0 or t-stamps[j]>MINUTE:raise ValueError(f'missing mark: {s} {t}')
        return float(prices[j])
    def plans(self):
        code=(HERE/'control_v2.py').read_bytes()+(HERE/'astra_policy.py').read_bytes()
        key=hashlib.sha256(code).hexdigest()[:20];path=CACHE/f'{self.month}-{key}.pkl'
        if path.exists():return pickle.loads(path.read_bytes())
        policy=ControlPolicy(self.ticks)
        arrays={s:d[['ts','open','high','low','close','volume','taker_buy_volume','quote_volume','count']].to_numpy() for s,d in self.raw.items()}
        plans=[]
        for j in range(len(next(iter(arrays.values())))):
            bars={s:Observation(int(a[j,0]),*map(float,a[j,1:8]),int(a[j,8])) for s,a in arrays.items()}
            plans.extend(policy.observe(bars))
        stats={s:dict(m.stats) for s,m in policy.markets.items()}
        no_trades=[x for m in policy.markets.values() for x in m.explanations]
        value=(plans,stats,no_trades);path.write_bytes(pickle.dumps(value))
        print('OPPORTUNITIES',self.month,len(plans),json.dumps(stats),flush=True)
        return value
    def labels(self,plans):
        arrays={s:d[['ts','open','high','low','close']].to_numpy() for s,d in self.raw.items()}
        output=[];unresolved=0
        for p in plans:
            a=arrays[p.symbol];sgn=int(p.side.value)
            j=np.searchsorted(a[:,0],p.observed_time_ns,side='right')
            future=a[j:]
            if not len(future):continue
            sl=future[:,3]<=p.stop if sgn>0 else future[:,2]>=p.stop
            tp=future[:,2]>p.target+self.ticks[p.symbol]*.5 if sgn>0 else future[:,3]<p.target-self.ticks[p.symbol]*.5
            hit=np.flatnonzero(sl|tp)
            if not len(hit):unresolved+=1;continue
            k=hit[0];t,o,h,l,c=future[k]
            # Both boundaries in one minute => stop first for research labels.
            stopped=bool(sl[k]);ambiguous=bool(sl[k] and tp[k])
            entry=p.entry*(1+sgn*.00012)
            exit_px=(min(p.stop,o) if sgn>0 else max(p.stop,o))*(1-sgn*.00012) if stopped else p.target
            fees=.0005*entry+(.0005 if stopped else .0002)*exit_px
            funding=sum(-sgn*self.mark_at(s,t0)*rate for t0,s,rate in self.funding if s==p.symbol and p.observed_time_ns<t0<=t)
            per_risk=abs(p.entry-p.stop)+(p.entry+p.stop)*.00062+p.entry*.0001
            r=(sgn*(exit_px-entry)-fees+funding)/per_risk
            row=p.record();row.update(label_closed=int(t),label_target=int(not stopped),label_net_r=float(r),
                                     label_hold=(t-p.observed_time_ns)/MINUTE,label_ambiguous=ambiguous)
            output.append(row)
        print('LABELS',self.month,len(output),'unresolved',unresolved,flush=True)
        return pd.DataFrame(output)

class Decision:
    def __init__(self,model,calibrator,trained_through):
        self.model=model;self.calibrator=calibrator;self.trained_through=trained_through
    def probabilities(self,plans):
        if not plans:return {}
        x=pd.DataFrame([p.features for p in plans]).reindex(columns=FEATURES).replace([np.inf,-np.inf],np.nan)
        raw=np.clip(self.model.predict_proba(x)[:,1],1e-5,1-1e-5)
        pred=self.calibrator.predict_proba(np.log(raw/(1-raw)).reshape(-1,1))[:,1]
        return {p.plan_id:float(q) for p,q in zip(plans,pred)}

def fit(labels,train_end,cal_end):
    train=labels[labels.label_closed<ns(train_end)].copy()
    cal=labels[(labels.observed_time_ns>=ns(train_end))&(labels.label_closed<ns(cal_end))].copy()
    if len(train)<200 or len(cal)<50:raise ValueError(f'not enough completed learning observations: {len(train)} {len(cal)}')
    model=HistGradientBoostingClassifier(max_iter=160,max_leaf_nodes=7,min_samples_leaf=50,
                                         l2_regularization=10.,learning_rate=.05,early_stopping=False,random_state=20260905)
    x=train.reindex(columns=FEATURES).replace([np.inf,-np.inf],np.nan)
    # Simultaneous four-symbol responses are not four independent training votes.
    group=train.observed_time_ns//(5*MINUTE)
    weight=1./group.map(group.value_counts()).to_numpy()
    model.fit(x,train.label_target,sample_weight=weight)
    raw=np.clip(model.predict_proba(cal.reindex(columns=FEATURES))[:,1],1e-5,1-1e-5)
    calibrator=LogisticRegression(C=1.,random_state=20260905).fit(np.log(raw/(1-raw)).reshape(-1,1),cal.label_target)
    decision=Decision(model,calibrator,ns(cal_end))
    pred=calibrator.predict_proba(np.log(raw/(1-raw)).reshape(-1,1))[:,1]
    metadata={'train_end':train_end,'calibration_end':cal_end,'train_candidates':len(train),'calibration_candidates':len(cal),
              'train_target_rate':float(train.label_target.mean()),'calibration_target_rate':float(cal.label_target.mean()),
              'calibration_brier':float(np.mean((pred-cal.label_target)**2)),
              'calibration_slope':float(calibrator.coef_[0,0]),'features':FEATURES,
              'decision':'positive expected log NAV increment using current NAV and execution cost; no win-rate target threshold'}
    write(OUT/'model.json',metadata);(OUT/'decision.pkl').write_bytes(pickle.dumps(decision))
    print('MODEL',json.dumps(metadata),flush=True)
    return decision

class ReplayPolicy:
    def __init__(self,tape,plans):
        self.markets={s:type('ObservedMarket',(),{'history':[],'stats':Counter(),'explanations':[]})() for s in tape.symbols}
        self.by_time={}
        for p in plans:self.by_time.setdefault(p.observed_time_ns,[]).append(p)
    def observe(self,bars):
        for s,b in bars.items():self.markets[s].history.append(b)
        return self.by_time.get(next(iter(bars.values())).ts,[])

class AccountStrategy(AstraStrategy):
    def __init__(self,*a,probabilities=None,**k):
        super().__init__(*a,**k);self.probabilities=probabilities;self.last_close=0;self.used_events=set()
        self.router=self.score
    def score(self,p):
        if self.probabilities is None:return 1.
        if p.plan_id not in self.probabilities:return -1.
        nav=float(self._current_nav());inst=next(i for i in self.instruments.values() if i.raw_symbol.value==p.symbol)
        q=self._quantity(inst,p,Decimal(str(nav)))
        if q is None:return -1.
        qty=float(q);side=int(p.side.value);slip=self.liquidity.fraction(p.symbol,qty,p.entry)
        entry=p.entry*(1+side*slip);stop=p.stop*(1-side*slip)
        win=qty*(side*(p.target-entry)-.0005*entry-.0002*p.target)/nav
        loss=qty*(side*(stop-entry)-.0005*entry-.0005*stop)/nav
        if min(win,loss)<=-1:return -1.
        prob=self.probabilities[p.plan_id]
        return prob*math.log1p(win)+(1-prob)*math.log1p(loss)
    def _submit_plan(self,iid,p):
        if p.causal_event_id in self.used_events or p.interaction_time_ns<=self.last_close:return False
        answer=super()._submit_plan(iid,p)
        if answer:self.used_events.add(p.causal_event_id)
        return answer
    def on_position_closed(self,event):
        self.last_close=max(self.last_close,int(event.ts_closed))
        super().on_position_closed(event)

def backtest(tape,plans,decision,name,start,end,starting_nav=10000.,stress=1.):
    begin=ns(start);finish=ns(end);start_clock=max(int(next(iter(tape.raw.values())).ts.iloc[0])-MINUTE,begin-3*1440*MINUTE)
    if decision is not None and begin<decision.trained_through:raise ValueError('evaluation overlaps model training/calibration')
    probs=None if decision is None else decision.probabilities(plans)
    liquidity=ExecutionLiquidity(stress);funding=FundingCashflows([x for x in tape.funding if start_clock<x[0]<=finish],tape.mark_at)
    engine=make_engine(funding,liquidity,starting_nav)
    if engine.kernel._log_guard is not None:LOG_GUARDS.append(engine.kernel._log_guard)
    types=[];instruments=[]
    for s,inst in tape.instruments.items():
        engine.add_instrument(inst);instruments.append(inst)
        bt=BarType.from_str(f'{inst.id}-1-MINUTE-LAST-EXTERNAL');types.append(bt)
        d=tape.raw[s];d=d[(d.ts>start_clock)&(d.ts<=finish)]
        bars=[BinanceBar(bar_type=bt,open=inst.make_price(r.open),high=inst.make_price(r.high),low=inst.make_price(r.low),close=inst.make_price(r.close),volume=inst.make_qty(r.volume),quote_volume=Decimal(str(r.quote_volume)),count=int(r.count),taker_buy_base_volume=Decimal(str(r.taker_buy_volume)),taker_buy_quote_volume=Decimal(str(r.taker_buy_quote_volume)),ts_event=int(r.ts),ts_init=int(r.ts)) for r in d.itertuples(index=False)]
        engine.add_data(bars,sort=False)
    engine.sort_data()
    config=EasyChartMTFConfig(instrument_ids=tuple(i.id for i in instruments),higher_bar_types=tuple(types),decision_bar_types=tuple(types),trigger_bar_types=tuple(types),execution_bar_types=tuple(types),risk_fraction=.03,min_gross_rr=1.,estimated_entry_fee_rate=.0005,estimated_stop_fee_rate=.0005,trading_start_ns=begin)
    strategy=AccountStrategy(config,ReplayPolicy(tape,plans),liquidity,tape.mark_at,probabilities=probs)
    engine.add_strategy(strategy);path=OUT/name;path.mkdir(exist_ok=True)
    try:
        engine.run()
        p=strategy.active_plan
        if p is not None and p.plan_id not in {r['plan_id'] for r in strategy.closed}:
            actual=[x for x in engine.cache.positions_closed() if x.opening_order_id==strategy.active_entry_id]
            if len(actual)!=1:raise RuntimeError('terminal close has no unique actual fill')
            pos=actual[0];row=p.record();row.update(strategy.open_context[p.plan_id]);row.update(
                position_id=str(pos.id),opened=int(pos.ts_opened),closed=int(pos.ts_closed),entry_fill=float(pos.avg_px_open),
                exit_fill=float(pos.avg_px_close),quantity=float(pos.peak_qty),pnl_ex_funding=pos.realized_pnl.as_double(),
                holding_minutes=(pos.ts_closed-pos.ts_opened)/MINUTE,evaluation_censored=True)
            strategy.closed.append(row)
        trades=pd.DataFrame(strategy.closed);pay=pd.DataFrame(funding.payments)
        if len(trades):
            trades['funding']=[float(pay.loc[(pay.symbol==r.symbol)&(pay.ts>r.opened)&(pay.ts<=r.closed),'cash'].sum()) if len(pay) else 0. for r in trades.itertuples()]
            trades['pnl']=trades.pnl_ex_funding+trades.funding;trades['net_r']=trades.pnl/(.03*trades.nav_before)
            trades['probability']=[probs.get(p,np.nan) if probs else np.nan for p in trades.plan_id]
        nav=engine.portfolio.account(VENUE).balance_total(USDT).as_double()
        curve=np.array([starting_nav]+[v for _,v in strategy.nav_path]+[nav]);dd=1-curve/np.maximum.accumulate(curve)
        natural=trades[~trades.evaluation_censored] if len(trades) else trades;n=len(natural)
        summary={'name':name,'start':start,'end_exclusive':end,'days':(finish-begin)/(1440*MINUTE),
                 'completed_trades':n,'wins':int((natural.pnl>0).sum()) if n else 0,'win_rate':float((natural.pnl>0).mean()) if n else None,
                 'initial_nav':starting_nav,'final_nav':nav,'return_pct':100*(nav/starting_nav-1),'max_minute_mark_nav_drawdown':float(dd.max()),
                 'mean_net_r':float(natural.net_r.mean()) if n else None,'mean_planned_rr':float(natural.gross_rr.mean()) if n else None,
                 'mean_hold_minutes':float(natural.holding_minutes.mean()) if n else None,'censored_trades':len(trades)-n,
                 'funding_cash':float(pay.cash.sum()) if len(pay) else 0.,'slippage_stress':stress,
                 'episode_constraint':'one execution per control event; initiating interaction must follow previous global close',
                 'by_symbol':{str(k):{'trades':len(g),'win_rate':float((g.pnl>0).mean()),'net_r':float(g.net_r.mean())} for k,g in natural.groupby('symbol')} if n else {}}
        trades.to_csv(path/'trades.csv',index=False);pd.DataFrame(strategy.nav_path,columns=['ts','nav']).to_csv(path/'nav.csv',index=False)
        pd.DataFrame(strategy.decisions).to_csv(path/'decisions.csv',index=False)
        write(path/'summary.json',summary)
        if engine.cache.positions_open():raise AssertionError('evaluation terminated with open exposure')
        if len(trades) and abs(nav-starting_nav-trades.pnl.sum())>.03:raise AssertionError('account and trade cash flows differ')
        # Numerical chart windows include only what was visible at entry; future
        # bars are explicitly separated for post-trade diagnosis.
        cases=[]
        for r in list(trades.itertuples())[:12]:
            d=tape.raw[r.symbol];left=d[(d.ts>r.opened-90*MINUTE)&(d.ts<=r.opened)]
            right=d[(d.ts>r.opened)&(d.ts<=min(r.closed,r.opened+180*MINUTE))]
            cases.append({'plan_id':r.plan_id,'symbol':r.symbol,'entry':r.entry,'stop':r.stop,'target':r.target,
                          'net_r':r.net_r,'before_entry_5m':compress(left),'after_entry_5m':compress(right)})
        write(path/'chart_windows.json',cases)
        print('ACCOUNT',json.dumps(summary),flush=True)
        return summary
    finally:engine.dispose()

def compress(d):
    if d.empty:return []
    x=d[['ts','open','high','low','close','volume']].copy()
    x['period']=((x.ts-1)//(5*MINUTE)+1)*(5*MINUTE)
    x=x.groupby('period').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'))
    x['ts']=pd.to_datetime(x.index,utc=True).astype(str)
    return x.reset_index(drop=True).round(8).to_dict('records')

def main():
    request=json.loads((HERE/'control_request.json').read_text())
    prepare(request['months'])
    tapes={};all_labels=[];proposals={};opportunities={}
    for month in request['months']:
        tape=Tape(month);plans,stats,no_trades=tape.plans()
        tapes[month]=tape;proposals[month]=plans
        labels=tape.labels(plans);all_labels.append(labels)
        opportunities[month]={'plans':len(plans),'stats':stats,'labeled':len(labels),
                             'target_rate':float(labels.label_target.mean()) if len(labels) else None,
                             'mean_rr':float(labels.gross_rr.mean()) if len(labels) else None,
                             'mean_net_r':float(labels.label_net_r.mean()) if len(labels) else None}
        write(OUT/f'{month}_notrade_examples.json',no_trades[::max(1,len(no_trades)//20)][:20])
    write(OUT/'opportunities.json',opportunities)
    labels=pd.concat(all_labels,ignore_index=True);labels.to_pickle(CACHE/'labels.pkl')
    decision=fit(labels,request['train_end'],request['calibration_end'])
    results=[]
    for job in request['experiments']:
        cfg=dict(job);month=cfg.pop('month');learned=cfg.pop('learned',True)
        results.append(backtest(tapes[month],proposals[month],decision if learned else None,**cfg))
        write(OUT/'latest.json',results)
    (OUT/'error.txt').unlink(missing_ok=True)

if __name__=='__main__':
    try:main()
    except Exception:
        (OUT/'error.txt').write_text(traceback.format_exc());raise
