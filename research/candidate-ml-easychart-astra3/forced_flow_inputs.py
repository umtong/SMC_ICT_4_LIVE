"""Observed forcing and aggregate passive depth, with no invented liquidation feed.

Anonymous CryptoHFTData access is documented at 60 requests/minute/IP. Missing
hour objects represent no archived broadcast, not proof of zero liquidations.
A strategy may use reported positive forcing, never treat this sampled feed as
complete. Last-filled quantity is a deliberately partial observed-flow measure;
we do not sum cumulative order quantity on successive updates.
"""
from __future__ import annotations
import datetime as dt
import json,time,urllib.error
from pathlib import Path
import numpy as np
import pandas as pd
from observed_flow_inputs import liquidation_hour,book_day,CACHE,HERE,OUT

SYMBOLS=('BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT')


def dates(start,end):
    a=dt.date.fromisoformat(start);b=dt.date.fromisoformat(end)
    while a<b:
        yield a.isoformat();a+=dt.timedelta(days=1)


def read_hour(symbol,date,hour):
    absent=CACHE/'absent'/date/f'{hour:02d}-{symbol}.json'
    if absent.exists():return None
    for attempt in range(3):
        try:return liquidation_hour(symbol,date,hour)
        except urllib.error.HTTPError as error:
            body=error.read(500).decode(errors='replace')
            if error.code==404:
                absent.parent.mkdir(parents=True,exist_ok=True)
                absent.write_text(json.dumps({'status':404,'body':body}))
                return None
            if error.code not in (429,500,502,503,504) or attempt==2:raise
            time.sleep(max(float(error.headers.get('Retry-After',5)),5*(attempt+1)))
        except (TimeoutError,urllib.error.URLError):
            if attempt==2:raise
            time.sleep(5*(attempt+1))
    raise RuntimeError('unreachable download state')


def prepare(symbol,start,end):
    destination=CACHE/f'{symbol}-{start}-{end}-observed.parquet'
    depth_destination=CACHE/f'{symbol}-{start}-{end}-depth.parquet'
    if destination.exists() and depth_destination.exists():
        return destination,depth_destination
    parts=[];books=[];absent=0
    for date in dates(start,end):
        for hour in range(24):
            frame=read_hour(symbol,date,hour)
            if frame is None:absent+=1
            else:parts.append(frame)
        depth=book_day(symbol,date)
        depth['ts']=pd.to_datetime(depth['timestamp'],utc=True).astype('int64')+30_000_000_000
        # Aggregate +/-1% depth is NOT individual limit-order arrival OFI.
        depth=depth[depth.percentage.isin([-1,1])]
        wide=depth.pivot(index='ts',columns='percentage',values='notional')
        wide=wide.rename(columns={-1:'bid_notional',1:'ask_notional'})
        books.append(wide.reset_index())
        print('FORCED_INPUT_DAY',symbol,date,'received_rows',sum(len(p) for p in parts),flush=True)
    if not parts:raise RuntimeError(f'no positive archived broadcasts {symbol} {start} {end}')
    raw=pd.concat(parts,ignore_index=True)
    if not raw.symbol.eq(symbol).all():raise ValueError('wrong liquidation symbol')
    if not raw.side.isin(['BUY','SELL']).all():raise ValueError('unknown forced-order side')
    # Repeated delivery is identified by the complete exchange message, with
    # earliest receipt retained. Different executions are never merged by price.
    payload=[c for c in raw.columns if c!='received_time']
    raw=raw.sort_values('received_time').drop_duplicates(payload,keep='first')
    observed=raw.received_time.to_numpy(dtype=np.int64)
    event_ns=raw.event_time.to_numpy(dtype=np.int64)*1_000_000
    if np.any(observed<event_ns):raise ValueError('receiver predates exchange event')
    price=pd.to_numeric(raw.average_price,errors='raise').to_numpy(dtype=float)
    quantity=pd.to_numeric(raw.last_filled_quantity,errors='raise').to_numpy(dtype=float)
    if np.any(price<0) or np.any(quantity<0):raise ValueError('invalid observed fills')
    result=pd.DataFrame({'received_time':observed,'exchange_time':event_ns,
                         'side':np.where(raw.side.eq('BUY'),1,-1),
                         'reported_notional':price*quantity})
    result=result[result.reported_notional>0].sort_values('received_time')
    result.to_parquet(destination,index=False,compression='zstd')
    book=pd.concat(books,ignore_index=True).sort_values('ts').drop_duplicates('ts')
    if book[['bid_notional','ask_notional']].isna().any().any():raise ValueError('incomplete depth snapshot')
    book.to_parquet(depth_destination,index=False,compression='zstd')
    print('FORCED_INPUT_READY',symbol,start,end,len(result),'unpublished_hours',absent,flush=True)
    return destination,depth_destination


def run():
    request=json.loads((HERE/'request.json').read_text())
    output=[]
    for start,end in request['ranges']:
        for symbol in request.get('symbols',SYMBOLS):
            forcing,depth=prepare(symbol,start,end)
            output.append({'symbol':symbol,'start':start,'end':end,
                           'received_broadcast_fills':len(pd.read_parquet(forcing)),
                           'depth_snapshots':len(pd.read_parquet(depth))})
    (OUT/'forced_input_windows.json').write_text(json.dumps(output,indent=2))
    print('FORCED_WINDOWS',json.dumps(output),flush=True)
