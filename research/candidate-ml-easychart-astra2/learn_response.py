"""Learn from earlier completed auctions and apply one frozen model to later weeks."""
import json
import pandas as pd
from experiment import observe,utc,OUT,WINDOWS,cases
from first_passage import route
from response_value import ResponseValue

def main():
    training=[]
    for start,end in [('2024-06-01','2024-07-01'),('2024-07-01','2024-08-01')]:
        print('EARLIER OBSERVATIONS',start,end,flush=True)
        d,_=observe(start,end)
        d['window']=start
        training.append(d[(d.censored==0)&(d.exit_ts<pd.Timestamp(utc('2024-08-01')).value)])
    model=ResponseValue().fit(pd.concat(training,ignore_index=True))
    model.save(OUT/'response_model.json')
    print('MODEL',model.training_rows,dict(zip(['intercept']+model.features,model.coef.tolist())),flush=True)
    results=[]
    for start,end in WINDOWS:
        rows,frames=observe(start,end)
        if rows.empty: continue
        rows=model.apply(rows)
        trades,summary=route(rows,utc(start),utc(end),threshold=0.)
        summary.update(window=start,policy='frozen_response_value',eligible=int((rows.predicted_value>0).sum()),candidates=len(rows))
        results.append(summary)
        print(json.dumps(summary),flush=True)
        rows.to_csv(OUT/f'value_actions_{start}.csv',index=False)
        trades.to_csv(OUT/f'trades_value_{start}.csv',index=False)
        (OUT/f'value_cases_{start}.json').write_text(json.dumps(cases(trades,frames),indent=2,allow_nan=False))
        bins=pd.cut(rows.predicted_win,[0,.3,.4,.5,.6,.7,.8,1.],include_lowest=True)
        grouped=rows.assign(probability_bin=bins,won=rows.net_r>0).groupby('probability_bin',observed=True)
        print(grouped.agg(n=('net_r','size'),wins=('won','mean'),mean_net_r=('net_r','mean')).to_string(),flush=True)
    (OUT/'response_value_results.json').write_text(json.dumps(results,indent=2)+'\n')
if __name__=='__main__': main()
