#!/usr/bin/env python3
"""Download BTC spot minute bars and causally align them to 5-minute features."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import io
import json
from pathlib import Path
import urllib.request
import zipfile

import numpy as np
import pandas as pd

from v53_nt_core import load_feature_matrix

COLS=["open_time","open","high","low","close","volume","close_time","quote_volume","trade_count","taker_buy_base","taker_buy_quote","ignore"]


def fetch(day: date, root: Path) -> Path:
    name=f"BTCUSDT-1m-{day.isoformat()}.zip"
    url=f"https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/{name}"
    path=root/name; path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): return path
    req=urllib.request.Request(url,headers={"User-Agent":"candidate-02-v108/1.0"})
    tmp=path.with_suffix('.tmp')
    with urllib.request.urlopen(req,timeout=180) as response, tmp.open('wb') as out:
        while chunk:=response.read(1<<20): out.write(chunk)
    with zipfile.ZipFile(tmp) as z:
        assert len([n for n in z.namelist() if not n.endswith('/')])==1
    tmp.replace(path); return path


def read(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        name=[n for n in z.namelist() if not n.endswith('/')][0]
        raw=z.read(name)
    frame=pd.read_csv(io.BytesIO(raw),header=None)
    if str(frame.iloc[0,0]).lower().startswith('open'): frame=frame.iloc[1:]
    frame=frame.iloc[:,:12]; frame.columns=COLS
    for c in ('open_time','open','high','low','close','volume'):
        frame[c]=pd.to_numeric(frame[c],errors='coerce')
    frame.dropna(subset=['open_time','close'],inplace=True)
    unit='us' if float(frame['open_time'].median())>=1e14 else 'ms'
    frame.index=pd.to_datetime(frame['open_time'].astype('int64'),unit=unit,utc=True)+pd.Timedelta(minutes=1)
    return frame[['open','high','low','close','volume']]


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument('--input-root',type=Path,required=True); p.add_argument('--start',required=True); args=p.parse_args()
    npz=next(args.input_root.rglob('v48_features.npz')); columns=npz.with_name('columns.json')
    features=load_feature_matrix(npz,columns)
    start=date.fromisoformat(args.start); days=[start-timedelta(days=2)+timedelta(days=i) for i in range(10)]
    spot_root=args.input_root/'spot_klines'
    with ThreadPoolExecutor(max_workers=8) as pool: paths=list(pool.map(lambda d:fetch(d,spot_root),days))
    spot=pd.concat([read(path) for path in sorted(paths)]).sort_index()
    if spot.index.has_duplicates: raise ValueError('duplicate spot minute closes')
    # A feature row indexed t contains the completed futures bar ending at t+5m.
    # Align only the spot close known at that same t+5m availability timestamp.
    availability=features.index+pd.Timedelta(minutes=5)
    aligned=spot['close'].reindex(availability)
    aligned.index=features.index
    if aligned.isna().any():
        raise ValueError(f'missing aligned spot closes: {int(aligned.isna().sum())}')
    features['spot_close_at_feature_availability']=aligned.astype(float)
    features['spot_log_ret_5m']=np.log(features['spot_close_at_feature_availability']/features['spot_close_at_feature_availability'].shift(1))
    features['perp_spot_log_basis']=np.log(features['close']/features['spot_close_at_feature_availability'])
    features['perp_spot_basis_change_5m']=features['perp_spot_log_basis'].diff()
    if features[['spot_log_ret_5m','perp_spot_log_basis','perp_spot_basis_change_5m']].iloc[1:].isna().any().any():
        raise ValueError('critical v108 cross-market gaps')
    values=features.to_numpy(dtype=np.float64,copy=True)
    # Preserve the original timestamp storage unit used by the audited loader.
    original=np.load(npz)['timestamps_ns']
    tmp=npz.with_suffix('.tmp.npz'); np.savez_compressed(tmp,timestamps_ns=original,values=values); tmp.replace(npz)
    columns.write_text(json.dumps([str(c) for c in features.columns],indent=2),encoding='utf-8')
    print(json.dumps({'rows':len(features),'spot_first':spot.index.min().isoformat(),'spot_last':spot.index.max().isoformat(),'alignment':'feature open t uses spot close at t+5m only'},indent=2))

if __name__=='__main__': main()
