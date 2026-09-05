"""Native-account replay. Each run is one continuous four-symbol account.
One-minute execution is a coarse diagnostic; --execution-root uses acquired 1s bars.
"""
import argparse,importlib.util,json,sys
from decimal import Decimal
from pathlib import Path
import numpy as np
import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig,LoggingConfig,RiskEngineConfig
from nautilus_trader.backtest.models import FillModel,LatencyModel
from nautilus_trader.model.data import Bar,BarType
from nautilus_trader.model.enums import OmsType,AccountType
from nautilus_trader.model.identifiers import Venue,TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USDT
from market_io import prepare,bars,funding,months_between,SYMBOLS
from control_transfer import candidates,market_context
from first_passage import attach_episodes
from nautilus_costs import ExecutionCosts,HistoricalFunding
from nautilus_policy import AuctionExecution
OUT=Path('research_results/candidate_ml_easychart_astra2')

def run(start,end,execution_root=None):
    start=pd.Timestamp(start,tz='UTC'); end=pd.Timestamp(end,tz='UTC'); warm=start-pd.Timedelta(days=7)
    prepare(months_between(warm,end))
    frames={s:bars(s,warm,end) for s in SYMBOLS}
    marks={s:bars(s,warm,end,'markPriceKlines') for s in SYMBOLS}
    fs={s:funding(s,start,end) for s in SYMBOLS}
    plans=pd.concat([candidates(s,frames[s]) for s in SYMBOLS],ignore_index=True)
    if plans.empty: raise ValueError('No control-transfer plans; inspect the market logic')
    plans=plans[(plans.ts>=start.value)&(plans.ts<end.value)].copy()
    plans=attach_episodes(market_context(plans,frames),frames)
    print('CAUSAL PLANS',len(plans),plans.groupby(['symbol','side']).size().to_dict(),flush=True)
    module_path=Path(__file__).resolve().parents[1]/'candidate-easychart-v3'/'instruments.py'
    spec=importlib.util.spec_from_file_location('astra_reused_instruments',module_path)
    source=importlib.util.module_from_spec(spec); sys.modules[spec.name]=source; spec.loader.exec_module(source)
    instruments={s:source.make_instrument(s) for s in SYMBOLS}
    step='1-SECOND' if execution_root else '1-MINUTE'
    types={s:BarType.from_str(f'{instruments[s].id}-{step}-LAST-EXTERNAL') for s in SYMBOLS}
    prefix=OUT/f'nautilus_transfer_{start.date()}_{step}'; OUT.mkdir(parents=True,exist_ok=True)
    plans.to_csv(str(prefix)+'_plans.csv',index=False)
    costs=ExecutionCosts(frames); payments=HistoricalFunding(fs,marks)
    engine=BacktestEngine(BacktestEngineConfig(trader_id=TraderId('ASTRA2-001'),logging=LoggingConfig(log_level='ERROR'),risk_engine=RiskEngineConfig(bypass=False)))
    engine.add_venue(venue=Venue('BINANCE'),oms_type=OmsType.NETTING,account_type=AccountType.MARGIN,base_currency=USDT,starting_balances=[Money(10000,USDT)],default_leverage=Decimal('100'),fill_model=FillModel(prob_fill_on_limit=0.,prob_slippage=1.,random_seed=42),fee_model=costs,latency_model=LatencyModel(base_latency_nanos=100000000),modules=[payments],bar_execution=True,bar_adaptive_high_low_ordering=False)
    for symbol,inst in instruments.items():
        engine.add_instrument(inst)
        if execution_root:
            paths=sorted((Path(execution_root)/'1s'/symbol).glob('*.parquet'))
            if not paths: raise FileNotFoundError(f'No 1s executions for {symbol}')
            d=pd.concat([pd.read_parquet(p) for p in paths]).sort_index()
        else: d=frames[symbol]
        d=d[(d.index>start)&(d.index<=end)]
        if d.index.duplicated().any(): raise ValueError('Duplicate execution bars')
        data=[Bar(bar_type=types[symbol],open=inst.make_price(r.open),high=inst.make_price(r.high),low=inst.make_price(r.low),close=inst.make_price(r.close),volume=inst.make_qty(r.volume),ts_event=int(t.value),ts_init=int(t.value)) for t,r in d.iterrows()]
        engine.add_data(data); print('EXECUTION BARS',symbol,len(data),flush=True)
    strategy=AuctionExecution(plans,instruments,types,costs,marks,end.value)
    engine.add_strategy(strategy); engine.run()
    trades=pd.DataFrame(strategy.trades); nav=pd.DataFrame(strategy.nav_path); ending=strategy.balance()
    for p in strategy.cache.positions_open():
        symbol=str(p.instrument_id).split('-PERP')[0]
        ending+=float(p.unrealized_pnl(instruments[symbol].make_price(float(marks[symbol].close.iloc[-1]))))
    nav=pd.concat([nav,pd.DataFrame([{'ts':end.value,'nav':ending}])],ignore_index=True)
    days=(end-start).total_seconds()/86400
    summary={'start':str(start),'end':str(end),'execution_resolution':step,'candidate_plans':len(plans),'trades':len(trades),'open_positions':len(strategy.cache.positions_open()),'nav':ending,'return_pct':(ending/10000-1)*100,'mark_nav_drawdown_pct':float((nav.nav/nav.nav.cummax()-1).min()*100),'trades_per_day':len(trades)/days,'rejections':len(strategy.rejections),'funding_cashflow':sum(x['cashflow'] for x in payments.records),'exchange_fee':sum(x['commission'] for x in costs.records),'model_execution_cost':sum(x['execution_cost'] for x in costs.records)}
    if len(trades): summary.update(win_rate=float((trades.net_r>0).mean()),mean_net_r=float(trades.net_r.mean()),mean_planned_rr=float(trades.rr.mean()),mean_hold_minutes=float(trades.hold_minutes.mean()),median_hold_minutes=float(trades.hold_minutes.median()),min_planned_risk_pct=float(trades.planned_risk_pct.min()),max_planned_risk_pct=float(trades.planned_risk_pct.max()))
    trades.to_csv(str(prefix)+'_trades.csv',index=False); nav.to_csv(str(prefix)+'_nav.csv',index=False)
    pd.DataFrame(strategy.fills).to_csv(str(prefix)+'_fills.csv',index=False)
    Path(str(prefix)+'_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    Path(str(prefix)+'_rejections.json').write_text(json.dumps(strategy.rejections,indent=2)+'\n')
    print('NAUTILUS ACCOUNT',json.dumps(summary),flush=True); engine.dispose()
    return summary
if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--start',required=True); ap.add_argument('--end',required=True); ap.add_argument('--execution-root'); args=ap.parse_args(); run(args.start,args.end,args.execution_root)
