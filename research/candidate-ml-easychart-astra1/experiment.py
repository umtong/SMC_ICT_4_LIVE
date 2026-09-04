"""Reproducible short research experiment; observations are not promotion scores."""
from __future__ import annotations
from pathlib import Path
import json,sys,traceback,time
import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent
RESEARCH=HERE.parent
for p in [RESEARCH/'candidate-easychart-v3',RESEARCH/'candidate-easychart-v5',RESEARCH/'candidate-easychart_re1',HERE]:sys.path.insert(0,str(p))
from nautilus_trader.adapters.binance.common.types import BinanceBar
from nautilus_trader.model.data import BarType
from decimal import Decimal
from fee_profiles_v5 import make_instrument_with_fee_profile,FEE_PROFILES
from astra_policy import AstraPolicy,MINUTE,SYMBOLS,FEATURES,Observation
from execution import AstraStrategy,ExecutionLiquidity,FundingCashflows,make_engine,EasyChartMTFConfig,VENUE,USDT

OUT=Path('research_results/candidate_ml_easychart_astra1');OUT.mkdir(parents=True,exist_ok=True)
MARKET=Path('bundle/market')
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','count','taker_buy_volume','taker_buy_quote_volume','ignore']

def load_bars(symbol,month,typ='klines'):
    p=MARKET/typ/symbol/f'{symbol}-1m-{month}.zip'
    d=pd.read_csv(p,header=None,compression='zip')
    if not str(d.iloc[0,0]).isdigit():d=d.iloc[1:].copy()
    d.columns=COLS;d=d.apply(pd.to_numeric,errors='raise')
    t=d.open_time.to_numpy(dtype=np.int64)
    if np.median(t)>10**14:t=t//1000
    d['ts']=(t+60000)*1000000
    if not np.all(np.diff(d.ts.to_numpy())==MINUTE):raise ValueError(f'incomplete {p}')
    return d

def load_funding(symbol,month):
    p=MARKET/'fundingRate'/symbol/f'{symbol}-fundingRate-{month}.zip'
    d=pd.read_csv(p,compression='zip')
    return [(int(t)*1000000,symbol,float(rate)) for t,rate in zip(d['calc_time'],d['last_funding_rate'])]

def one_run(name,month,load_start,start,end,symbols):
    print('START',name,flush=True);t0=time.time()
    a=int(pd.Timestamp(load_start,tz='UTC').value);b=int(pd.Timestamp(end,tz='UTC').value)
    start_ns=int(pd.Timestamp(start,tz='UTC').value)
    raws={s:load_bars(s,month) for s in symbols}
    marks={s:load_bars(s,month,'markPriceKlines') for s in symbols}
    mark_arrays={s:(d.ts.to_numpy(dtype=np.int64),d.close.to_numpy(dtype=float)) for s,d in marks.items()}
    def mark_at(s,ts):
        t,p=mark_arrays[s];i=np.searchsorted(t,ts,side='right')-1
        if i<0:raise ValueError('mark price absent before timestamp')
        return float(p[i])
    funding_records=[r for s in symbols for r in load_funding(s,month) if a<r[0]<=b]
    liquidity=ExecutionLiquidity();funding=FundingCashflows(funding_records,mark_at)
    engine=make_engine(funding,liquidity)
    instruments=[make_instrument_with_fee_profile(s,FEE_PROFILES['usd_m_vip0']) for s in symbols]
    types=[];ticks={}
    for s,inst in zip(symbols,instruments,strict=True):
        engine.add_instrument(inst);ticks[s]=float(inst.price_increment)
        bt=BarType.from_str(f'{inst.id}-1-MINUTE-LAST-EXTERNAL');types.append(bt)
        d=raws[s];d=d[(d.ts>a)&(d.ts<=b)]
        bars=[]
        for r in d.itertuples(index=False):
            bars.append(BinanceBar(bar_type=bt,open=inst.make_price(r.open),high=inst.make_price(r.high),low=inst.make_price(r.low),close=inst.make_price(r.close),volume=inst.make_qty(r.volume),quote_volume=Decimal(str(r.quote_volume)),count=int(r.count),taker_buy_base_volume=Decimal(str(r.taker_buy_volume)),taker_buy_quote_volume=Decimal(str(r.taker_buy_quote_volume)),ts_event=int(r.ts),ts_init=int(r.ts)))
        engine.add_data(bars,sort=False)
    engine.sort_data()
    config=EasyChartMTFConfig(instrument_ids=tuple(x.id for x in instruments),higher_bar_types=tuple(types),decision_bar_types=tuple(types),trigger_bar_types=tuple(types),execution_bar_types=tuple(types),risk_fraction=.03,min_gross_rr=1.,estimated_entry_fee_rate=.0005,estimated_stop_fee_rate=.0005,trading_start_ns=start_ns)
    strategy=AstraStrategy(config,AstraPolicy(ticks),liquidity,mark_at)
    engine.add_strategy(strategy)
    path=OUT/name;path.mkdir(exist_ok=True)
    try:
        engine.run()
        closed=pd.DataFrame(strategy.closed)
        pay=pd.DataFrame(funding.payments)
        if len(closed):
            sums=pay.groupby('position_id').cash.sum() if len(pay) else pd.Series(dtype=float)
            closed['funding']=closed.position_id.map(sums).fillna(0.)
            closed['pnl']=closed.pnl_ex_funding+closed.funding
            closed['net_r']=closed.pnl/(.03*closed.nav_before)
            closed.to_csv(path/'trades.csv',index=False)
        else:closed.to_csv(path/'trades.csv',index=False)
        pd.DataFrame([p.record() for p in strategy.all_plans]).to_csv(path/'plans.csv',index=False)
        pd.DataFrame(strategy.decisions).to_csv(path/'decisions.csv',index=False)
        pd.DataFrame(strategy.nav_path,columns=['ts','nav']).to_csv(path/'nav.csv',index=False)
        pay.to_csv(path/'funding.csv',index=False)
        stats={s:dict(m.stats) for s,m in strategy.policy.markets.items()}
        pd.DataFrame([r for m in strategy.policy.markets.values() for r in m.explanations if r['ts']>=start_ns]).to_csv(path/'no_trade_events.csv',index=False)
        nav=float(engine.portfolio.account(VENUE).balance_total(USDT))
        curve=np.array([100000.]+[x[1] for x in strategy.nav_path]+[nav]);dd=1-curve/np.maximum.accumulate(curve)
        n=len(closed);wins=int((closed.pnl>0).sum()) if n else 0
        summary={'name':name,'symbols':symbols,'start':start,'end_exclusive':end,'days':(b-start_ns)/(1440*MINUTE),
                 'trades':n,'wins':wins,'win_rate':wins/n if n else 0.,'final_nav':nav,'return_pct':(nav/100000.-1)*100,'max_mark_nav_drawdown':float(dd.max()),
                 'mean_net_r':float(closed.net_r.mean()) if n else None,'mean_planned_rr':float(closed.gross_rr.mean()) if n else None,
                 'mean_hold_minutes':float(closed.holding_minutes.mean()) if n else None,
                 'profit_factor':float(closed.loc[closed.pnl>0,'pnl'].sum()/-closed.loc[closed.pnl<0,'pnl'].sum()) if n and (closed.pnl<0).any() else None,
                 'stats':stats,'funding_cash':float(pay.cash.sum()) if len(pay) else 0.,'elapsed_seconds':time.time()-t0,
                 'open_positions_after_stop':len(engine.cache.positions_open())}
        (path/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
        # Market windows for actual trades and missed first-response opportunities.
        selected=[]
        if n:
            selected += [('TRADE',int(r.observed_time_ns),r.symbol,r.plan_id) for r in closed.itertuples()]
        rejected=[r for m in strategy.policy.markets.values() for r in m.explanations if r['ts']>=start_ns and r['reason']=='first_response_geometry_below_one_r'][:8]
        selected += [('MISSED_GEOMETRY',r['ts'],r['symbol'],r['event']) for r in rejected]
        with (path/'market_windows.jsonl').open('w') as f:
            for kind,ts,s,key in selected:
                d=raws[s];d=d[(d.ts>=ts-90*MINUTE)&(d.ts<=ts+180*MINUTE)]
                frame=d[['ts','open','high','low','close','volume','taker_buy_volume']]
                f.write(json.dumps({'kind':kind,'key':key,'symbol':s,'ts':ts,'bars':frame.to_dict('records')})+'\n')
        return summary
    finally:engine.dispose()

if __name__=='__main__':
    try:
        result=one_run('v1_btc_aug2024','2024-08','2024-08-08','2024-08-12','2024-08-20',('BTCUSDT',))
        (OUT/'latest.json').write_text(json.dumps(result,indent=2))
    except Exception:
        (OUT/'error.txt').write_text(traceback.format_exc());raise
