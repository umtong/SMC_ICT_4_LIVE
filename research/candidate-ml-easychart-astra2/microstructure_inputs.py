"""Public aggregate trades -> causal execution bars and price/volume footprints.

These are executed-trade observations, NOT L2 order-book OFI, identified
liquidations or verified institutional positions. A five-second markout is
measured only for trades whose observation horizon ends inside a completed
bar. The still-unobserved tail is excluded, not filled with future prices.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
from pathlib import Path
import zipfile
import numpy as np
import pandas as pd
from numba import njit
from download_inputs import download, SYMBOLS

COLUMNS = ['open','high','low','close','volume','buy_volume','quote_volume','buy_quote_volume','trades',
           'low_volume_share','high_volume_share','low_delta','high_delta','trapped_buy_share','trapped_sell_share',
           'buy_markout_5s_bps','sell_markout_5s_bps','largest_buy_run_share','largest_sell_run_share',
           'early_delta','late_delta','late_return_bps','early_return_bps','flat_volume_share',
           'price_delta_correlation','poc_location','poc_volume_share','largest_trade_share']

@njit(cache=True,nogil=True)
def aggregate(time,price,quantity,maker,interval):
    starts=[0]
    for i in range(1,len(time)):
        if time[i]//interval!=time[i-1]//interval:starts.append(i)
    starts.append(len(time))
    n=len(starts)-1;times=np.empty(n,np.int64);out=np.zeros((n,28))
    for g in range(n):
        a=starts[g];b=starts[g+1];end=(time[a]//interval+1)*interval;times[g]=end
        op=price[a];cl=price[b-1];hi=op;lo=op;vol=0.;buy=0.;quote=0.;bquote=0.;largest=0.
        for i in range(a,b):
            hi=max(hi,price[i]);lo=min(lo,price[i]);vol+=quantity[i];quote+=quantity[i]*price[i];largest=max(largest,quantity[i])
            if not maker[i]:buy+=quantity[i];bquote+=quantity[i]*price[i]
        out[g,0]=op;out[g,1]=hi;out[g,2]=lo;out[g,3]=cl;out[g,4]=vol;out[g,5]=buy;out[g,6]=quote;out[g,7]=bquote;out[g,8]=b-a
        if interval<=1000 or vol<=0:continue
        lowv=0.;highv=0.;lowd=0.;highd=0.;trappedbuy=0.;trappedsell=0.;buyret=0.;sellret=0.;buyw=0.;sellw=0.
        earlyv=0.;earlyd=0.;latev=0.;lated=0.;lateprice=op;haslate=False;flat=0.;run=0.;lastside=0;maxbuy=0.;maxsell=0.;cvd=0.
        sx=0.;sy=0.;sxx=0.;syy=0.;sxy=0.;hist=np.zeros(16);k=a
        for i in range(a,b):
            q=quantity[i];p=price[i];side=-1 if maker[i] else 1
            if p<=lo+(hi-lo)*.25:lowv+=q;lowd+=side*q
            if p>=hi-(hi-lo)*.25:highv+=q;highd+=side*q
            if side==1 and p>cl:trappedbuy+=q
            if side==-1 and p<cl:trappedsell+=q
            if i>a and p==price[i-1]:flat+=q
            if side==lastside:run+=q
            else:run=q;lastside=side
            if side==1:maxbuy=max(maxbuy,run)
            else:maxsell=max(maxsell,run)
            if time[i]<end-interval//6:earlyv+=q;earlyd+=side*q
            else:
                latev+=q;lated+=side*q
                if not haslate:lateprice=p;haslate=True
            cvd+=side*q;x=(p-op)/op*1e4;y=cvd/vol
            sx+=x;sy+=y;sxx+=x*x;syy+=y*y;sxy+=x*y
            binno=0 if hi==lo else min(15,int((p-lo)/(hi-lo)*16));hist[binno]+=q
            while k<b and time[k]<time[i]+5000:k+=1
            if k<b:
                mark=(price[k]/p-1)*1e4
                if side==1:buyret+=q*mark;buyw+=q
                else:sellret-=q*mark;sellw+=q
        count=b-a;den=np.sqrt(max(sxx-sx*sx/count,0)*max(syy-sy*sy/count,0));poc=int(np.argmax(hist))
        out[g,9]=lowv/vol;out[g,10]=highv/vol;out[g,11]=lowd/max(lowv,1e-12);out[g,12]=highd/max(highv,1e-12)
        out[g,13]=trappedbuy/vol;out[g,14]=trappedsell/vol
        out[g,15]=buyret/buyw if buyw>0 else np.nan;out[g,16]=sellret/sellw if sellw>0 else np.nan
        out[g,17]=maxbuy/vol;out[g,18]=maxsell/vol;out[g,19]=earlyd/max(earlyv,1e-12);out[g,20]=lated/max(latev,1e-12)
        out[g,21]=(cl/lateprice-1)*1e4;out[g,22]=(lateprice/op-1)*1e4;out[g,23]=flat/vol
        out[g,24]=(sxy-sx*sy/count)/den if den>0 else 0.;out[g,25]=(poc+.5)/16*2-1 if hi>lo else 0.;out[g,26]=hist[poc]/vol;out[g,27]=largest/vol
    return times,out

def process(job):
    symbol,date,root=job;name=f'{symbol}-aggTrades-{date}.zip';path=root/'raw'/name
    record=download((f'https://data.binance.vision/data/futures/um/daily/aggTrades/{symbol}/{name}',path))
    if 'error' in record:raise RuntimeError(record)
    chunks={1000:[],60000:[],300000:[]};carry=None;rows=0;last=-1
    with zipfile.ZipFile(path) as archive:
        with archive.open(archive.namelist()[0]) as stream:
            reader=pd.read_csv(stream,usecols=['transact_time','price','quantity','is_buyer_maker'],chunksize=1000000,dtype={'is_buyer_maker':'bool'},true_values=['true','True'],false_values=['false','False'])
            for chunk in reader:
                if carry is not None:chunk=pd.concat([carry,chunk],ignore_index=True)
                stamp=chunk.transact_time.to_numpy(dtype=np.int64)
                if stamp[0]<last or np.any(np.diff(stamp)<0):raise ValueError('out-of-order aggregate trades')
                cut=np.searchsorted(stamp,stamp[-1]//300000*300000)
                carry=chunk.iloc[cut:].copy();part=chunk.iloc[:cut]
                if not len(part):continue
                t=part.transact_time.to_numpy(dtype=np.int64);p=part.price.to_numpy(dtype=np.float64);q=part.quantity.to_numpy(dtype=np.float64);m=part.is_buyer_maker.to_numpy(dtype=np.bool_)
                for interval in chunks:chunks[interval].append(aggregate(t,p,q,m,interval))
                last=t[-1];rows+=len(part)
            if carry is not None and len(carry):
                t=carry.transact_time.to_numpy(dtype=np.int64);p=carry.price.to_numpy(dtype=np.float64);q=carry.quantity.to_numpy(dtype=np.float64);m=carry.is_buyer_maker.to_numpy(dtype=np.bool_)
                for interval in chunks:chunks[interval].append(aggregate(t,p,q,m,interval))
                rows+=len(carry)
    for interval,parts in chunks.items():
        times=np.concatenate([x[0] for x in parts]);values=np.concatenate([x[1] for x in parts]);columns=COLUMNS if interval>1000 else COLUMNS[:9]
        frame=pd.DataFrame(values[:,:len(columns)],columns=columns,index=pd.to_datetime(times,unit='ms',utc=True));frame.index.name='ts'
        if frame.index.duplicated().any():raise ValueError('duplicate output bar')
        dest=root/f'{interval//1000}s'/symbol/f'{date}.parquet';dest.parent.mkdir(parents=True,exist_ok=True);frame.to_parquet(dest,index=True,compression='zstd')
    path.unlink();record['aggregate_rows']=rows;print(symbol,date,rows,flush=True);return record

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--start',required=True);parser.add_argument('--days',type=int,default=7);parser.add_argument('--output',type=Path,default=Path('micro_market'));parser.add_argument('--workers',type=int,default=2);args=parser.parse_args()
    start=dt.date.fromisoformat(args.start);jobs=[(s,str(start+dt.timedelta(days=d)),args.output) for d in range(args.days) for s in SYMBOLS]
    aggregate(np.array([1,2],np.int64),np.array([1.,2.]),np.array([1.,1.]),np.array([False,True]),60000)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:records=list(pool.map(process,jobs))
    args.output.mkdir(parents=True,exist_ok=True);(args.output/'archives.json').write_text(json.dumps(records,indent=2)+'\n')
if __name__=='__main__':main()
