"""Small native experiments and the price paths needed to understand decisions."""
import argparse,importlib,json,subprocess,sys
from pathlib import Path
import numpy as np
import pandas as pd
from market_io import bars,aggregate
WINDOWS=[('2024-08-03','2024-08-10'),('2025-08-10','2025-08-17'),('2025-11-17','2025-11-24')]
ROOT=Path('research_results/candidate_ml_easychart_astra2')


def view_cases(out,start,end):
    prefix=out/f'nautilus_transfer_{start}_1-MINUTE'
    trades=pd.read_csv(str(prefix)+'_trades.csv')
    plans=pd.read_csv(str(prefix)+'_plans.csv')
    if trades.empty: return
    left=pd.Timestamp(start,tz='UTC')-pd.Timedelta(days=7); right=pd.Timestamp(end,tz='UTC')
    frames={s:bars(s,left,right) for s in plans.symbol.unique()}
    chosen=pd.concat([trades[trades.net_r<0].head(2),trades[trades.net_r>0].head(2)]).drop_duplicates(['ts','symbol'])
    records=[]
    for k,row in enumerate(chosen.to_dict('records')):
        d=frames[row['symbol']]; t=pd.Timestamp(row['ts'],tz='UTC'); root=pd.Timestamp(row['root_ts'],tz='UTC')
        stop=pd.Timestamp(row['exit_ts'],tz='UTC')+pd.Timedelta(minutes=30)
        x=aggregate(d,5).loc[root-pd.Timedelta(hours=1):stop,['open','high','low','close','volume','buy_volume']]
        meta=plans[(plans.ts==row['ts'])&(plans.symbol==row['symbol'])].iloc[0].to_dict()
        record={'trade':row,'context':meta,'candles_5m':json.loads(x.reset_index().to_json(orient='records',date_format='iso'))}
        (out/f'case_{start}_{k}.json').write_text(json.dumps(record,indent=2,default=str)+'\n')
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,ax=plt.subplots(figsize=(12,5))
        ax.vlines(x.index,x.low,x.high,linewidth=.7)
        ax.plot(x.index,x.close,linewidth=1,label='close')
        for key in ['entry','stop','target']: ax.axhline(row[key],label=key,linestyle='--')
        ax.axvline(t,linestyle=':'); ax.set_title(f"{row['symbol']} side={row['side']} net R={row['net_r']:.3f}")
        ax.legend(); fig.autofmt_xdate(); fig.tight_layout(); fig.savefig(out/f'case_{start}_{k}.png'); plt.close(fig)
        records.append({'case':k,'symbol':row['symbol'],'ts':str(t),'net_r':row['net_r']})
    (out/f'case_index_{start}.json').write_text(json.dumps(records,indent=2)+'\n')
    # Retrospective analyst-only selection of a strong untraded move. It is
    # never passed into candidates, ranking, sizing or the account executor.
    missed=[]
    for symbol,d in frames.items():
        q=d.loc[(d.index>=pd.Timestamp(start,tz='UTC'))&(d.index<right)]
        future=(q.close.shift(-60)/q.close-1).abs()
        for _,r in trades[trades.symbol==symbol].iterrows():
            a=pd.Timestamp(r.entry_ts,tz='UTC')-pd.Timedelta(hours=1); b=pd.Timestamp(r.exit_ts,tz='UTC')
            future.loc[(future.index>=a)&(future.index<=b)]=np.nan
        if future.notna().sum()==0: continue
        t=future.idxmax(); x=aggregate(d,15).loc[t-pd.Timedelta(hours=3):t+pd.Timedelta(hours=2)]
        missed.append({'symbol':symbol,'analyst_selected_time':str(t),'absolute_next_hour_move':float(future.loc[t]),'candles_15m':json.loads(x.reset_index().to_json(orient='records',date_format='iso'))})
    (out/f'missed_paths_{start}.json').write_text(json.dumps(missed,indent=2)+'\n')


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--policy',default='nested_control'); ap.add_argument('--start'); ap.add_argument('--end'); ap.add_argument('--execution-root'); args=ap.parse_args()
    if not args.policy.isidentifier(): raise ValueError('Expected a local research module name')
    out=ROOT/args.policy; out.mkdir(parents=True,exist_ok=True)
    if args.start:
        import nautilus_account as account
        module=importlib.import_module(args.policy)
        account.candidates=module.candidates; account.OUT=out
        account.run(args.start,args.end,args.execution_root)
        if not args.execution_root: view_cases(out,args.start,args.end)
        return
    results=[]
    for start,end in WINDOWS:
        subprocess.run([sys.executable,__file__,'--policy',args.policy,'--start',start,'--end',end],check=True)
        results.append(json.loads((out/f'nautilus_transfer_{start}_1-MINUTE_summary.json').read_text()))
    (out/'short_results.json').write_text(json.dumps(results,indent=2)+'\n')
if __name__=='__main__': main()
