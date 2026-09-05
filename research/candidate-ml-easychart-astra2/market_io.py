"""Research observations, using the project's existing public archive downloader."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import zipfile
import numpy as np
import pandas as pd
from download_inputs import download, SYMBOLS

COLS=['time','open','high','low','close','volume','close_time','quote_volume','trades','buy_volume','buy_quote_volume','ignore']
ROOT=Path('market_research')

def months_between(start,end):
    return [str(x) for x in pd.period_range(pd.Timestamp(start).tz_localize(None).to_period('M'),pd.Timestamp(end).tz_localize(None).to_period('M'),freq='M')]

def prepare(months, kinds=('klines','markPriceKlines','fundingRate')):
    jobs=[]
    for s in SYMBOLS:
        for m in months:
            for k in kinds:
                name=f'{s}-fundingRate-{m}.zip' if k=='fundingRate' else f'{s}-1m-{m}.zip'
                interval='' if k=='fundingRate' else '/1m'
                jobs.append((f'https://data.binance.vision/data/futures/um/monthly/{k}/{s}{interval}/{name}',ROOT/k/s/name))
    with ThreadPoolExecutor(max_workers=8) as pool: result=list(pool.map(download,jobs))
    errors=[x for x in result if 'error' in x]
    if errors: raise RuntimeError(errors)

def bars(symbol,start,end,kind='klines'):
    pieces=[]
    for m in months_between(start,end):
        path=ROOT/kind/symbol/f'{symbol}-1m-{m}.zip'
        with zipfile.ZipFile(path) as ar:
            with ar.open(ar.namelist()[0]) as f: d=pd.read_csv(f,header=None,names=COLS)
        d=d[pd.to_numeric(d.time,errors='coerce').notna()].copy()
        d=d.apply(pd.to_numeric,errors='raise')
        ts=d.time.to_numpy(dtype=np.int64)
        if np.median(ts)>1e14: ts=ts//1000
        # Exchange archive timestamp is opening time; decision timestamps are closes.
        d.index=pd.to_datetime(ts+60000,unit='ms',utc=True)
        pieces.append(d.drop(columns=['time','close_time','ignore']))
    d=pd.concat(pieces).sort_index()
    d=d.loc[(d.index>pd.Timestamp(start))&(d.index<=pd.Timestamp(end))]
    if d.index.duplicated().any(): raise ValueError('duplicate market bar')
    if len(d)>1 and not np.all(np.diff(d.index.asi8)==60000000000): raise ValueError(f'missing minute {symbol}')
    if not ((d.high>=d[['open','close','low']].max(axis=1))&(d.low<=d[['open','close','high']].min(axis=1))).all(): raise ValueError('invalid OHLC')
    d.index.name='ts'
    return d

def aggregate(d,minutes):
    spec={'open':'first','high':'max','low':'min','close':'last','volume':'sum','quote_volume':'sum','trades':'sum','buy_volume':'sum','buy_quote_volume':'sum'}
    x=d.resample(f'{minutes}min',closed='right',label='right',origin='epoch').agg(spec)
    counts=d.close.resample(f'{minutes}min',closed='right',label='right',origin='epoch').count()
    return x.loc[counts==minutes]

def funding(symbol,start,end):
    out=[]
    for m in months_between(start,end):
        with zipfile.ZipFile(ROOT/'fundingRate'/symbol/f'{symbol}-fundingRate-{m}.zip') as ar:
            with ar.open(ar.namelist()[0]) as f: d=pd.read_csv(f)
        if not {'calc_time','last_funding_rate'}.issubset(d.columns): raise ValueError(list(d.columns))
        d.index=pd.to_datetime(d.calc_time,unit='ms',utc=True)
        out.append(d.last_funding_rate)
    d=pd.concat(out).sort_index()
    return d.loc[(d.index>=pd.Timestamp(start))&(d.index<pd.Timestamp(end))]
