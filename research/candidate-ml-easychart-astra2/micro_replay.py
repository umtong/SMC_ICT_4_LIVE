"""Second-resolution experiments, with the same native account and cost law."""
import argparse,json
from pathlib import Path
import pandas as pd
BASE=Path('research_results/candidate_ml_easychart_astra2')


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--start',required=True); ap.add_argument('--end',required=True); ap.add_argument('--execution-root',required=True); ap.add_argument('--policy',choices=['adaptive','excursion'],required=True); args=ap.parse_args()
    import nautilus_account as account
    if args.policy=='adaptive':
        from impact_learning import ImpactExecution
        plans=pd.read_csv(BASE/'adaptive_impact_hgb'/f'nautilus_transfer_{args.start}_1-MINUTE_plans.csv')
        # These are previously frozen, causal probability forecasts, not a
        # selected trade list. Arbitration uses the new account's own state.
        fields=['symbol','side','ts','root_ts','source','scale','entry','stop','target','rr','entry_kind','predicted_win']
        plans=plans[fields].copy()
        account.candidates=lambda symbol,d: plans[plans.symbol==symbol].copy()
        account.AuctionExecution=ImpactExecution
        account.OUT=BASE/'adaptive_impact_seconds'
    else:
        from excursion_repair import candidates
        account.candidates=lambda symbol,d:candidates(symbol,d,args.execution_root)
        account.OUT=BASE/'excursion_repair_seconds'
    result=account.run(args.start,args.end,args.execution_root)
    prefix=account.OUT/f'nautilus_transfer_{args.start}_1-SECOND'
    if result['trades']==0: return
    trades=pd.read_csv(str(prefix)+'_trades.csv'); plans=pd.read_csv(str(prefix)+'_plans.csv')
    sample=pd.concat([trades[trades.net_r<0].head(2),trades[trades.net_r>0].head(2)]).drop_duplicates(['symbol','ts'])
    for k,r in enumerate(sample.to_dict('records')):
        paths=sorted((Path(args.execution_root)/'1s'/r['symbol']).glob('*.parquet'))
        d=pd.concat([pd.read_parquet(p) for p in paths]).sort_index()
        left=pd.Timestamp(r['root_ts'],tz='UTC')-pd.Timedelta(minutes=5); right=pd.Timestamp(r['exit_ts'],tz='UTC')+pd.Timedelta(minutes=5)
        d=d.loc[left:right]
        x=d.resample('10s',closed='right',label='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','buy_volume':'sum'}).dropna(subset=['close'])
        context=plans[(plans.ts==r['ts'])&(plans.symbol==r['symbol'])].iloc[0].to_dict()
        record={'trade':r,'context':context,'candles_10s':json.loads(x.reset_index().to_json(orient='records',date_format='iso'))}
        (account.OUT/f'case_{args.start}_{k}.json').write_text(json.dumps(record,indent=2,default=str)+'\n')
if __name__=='__main__': main()
