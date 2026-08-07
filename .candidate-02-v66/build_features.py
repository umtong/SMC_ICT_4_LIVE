"""Build causal one-minute v66 features from locked direct microstructure data.

This is a data transformation, not a backtest. Every row is timestamped at the
minute close and uses only aggTrades/bookDepth snapshots from that completed
minute. Performance remains exclusively in NautilusTrader.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import json, zipfile
import numpy as np
import pandas as pd
from v53_nt_core import load_raw_one_minute

ROOT=Path("inputs/v66-first-week")
AGG=ROOT/"direct/aggTrades"
BOOK=ROOT/"direct/bookDepth"
RAW=ROOT/".cache/candidate-02/v66-first-week/binance_1m"
FEATURE_ROOT=ROOT/"candidate-02-v48-first-week"
OUT=ROOT/"artifacts/candidate-02-v66-first-week-data"
AGG_COLUMNS=["agg_trade_id","price","quantity","first_trade_id","last_trade_id","transact_time","is_buyer_maker"]
BOOK_COLUMNS=["timestamp","percentage","depth","notional"]

def _read_zip(path:Path, expected:list[str]) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members=[n for n in archive.namelist() if not n.endswith("/")]
        if len(members)!=1: raise ValueError(f"unexpected archive {path}")
        frame=pd.read_csv(archive.open(members[0]))
    if not set(expected).issubset(frame.columns):
        with zipfile.ZipFile(path) as archive:
            frame=pd.read_csv(archive.open(members[0]),header=None,names=expected)
    return frame

def _unit(values:pd.Series) -> str:
    median=float(pd.to_numeric(values,errors="coerce").dropna().median())
    return "us" if median>=1e14 else "ms"

def aggregate_trades(path:Path) -> pd.DataFrame:
    frame=_read_zip(path,AGG_COLUMNS)
    for c in ("price","quantity","transact_time"):
        frame[c]=pd.to_numeric(frame[c],errors="coerce")
    frame.dropna(subset=["price","quantity","transact_time"],inplace=True)
    maker=frame["is_buyer_maker"]
    if maker.dtype!=bool:
        maker=maker.astype(str).str.lower().map({"true":True,"false":False,"1":True,"0":False})
    if maker.isna().any(): raise ValueError(f"invalid maker flags in {path}")
    quote=frame["price"]*frame["quantity"]
    signed=np.where(maker.to_numpy(),-quote.to_numpy(),quote.to_numpy())
    timestamp=pd.to_datetime(frame["transact_time"],unit=_unit(frame["transact_time"]),utc=True)
    minute=timestamp.dt.floor("min")+pd.Timedelta(minutes=1)
    temp=pd.DataFrame({"minute":minute,"quote":quote.to_numpy(),"signed":signed,
                       "buy":np.where(signed>0,quote,0.0),"sell":np.where(signed<0,quote,0.0),
                       "price":frame["price"].to_numpy()})
    grouped=temp.groupby("minute",sort=True)
    result=pd.DataFrame(index=grouped.size().index)
    result["aggressive_total_quote_1m"]=grouped["quote"].sum()
    result["aggressive_signed_quote_1m"]=grouped["signed"].sum()
    result["aggressive_buy_quote_1m"]=grouped["buy"].sum()
    result["aggressive_sell_quote_1m"]=grouped["sell"].sum()
    result["trade_count_1m"]=grouped.size().astype(float)
    result["agg_price_open"]=grouped["price"].first()
    result["agg_price_high"]=grouped["price"].max()
    result["agg_price_low"]=grouped["price"].min()
    result["agg_price_close"]=grouped["price"].last()
    return result

def aggregate_book(path:Path) -> pd.DataFrame:
    frame=_read_zip(path,BOOK_COLUMNS)
    frame["timestamp"]=pd.to_datetime(frame["timestamp"],utc=True,errors="coerce")
    frame["percentage"]=pd.to_numeric(frame["percentage"],errors="coerce")
    frame["notional"]=pd.to_numeric(frame["notional"],errors="coerce")
    frame.dropna(subset=["timestamp","percentage","notional"],inplace=True)
    frame=frame.loc[frame["percentage"].isin([-1,1])]
    pivot=frame.pivot_table(index="timestamp",columns="percentage",values="notional",aggfunc="last").sort_index()
    if -1 not in pivot or 1 not in pivot: raise ValueError(f"missing one-percent depth in {path}")
    pivot.rename(columns={-1:"bid",1:"ask"},inplace=True)
    pivot["minute"]=pivot.index.floor("min")+pd.Timedelta(minutes=1)
    grouped=pivot.groupby("minute",sort=True)
    result=pd.DataFrame(index=grouped.size().index)
    for side in ("bid","ask"):
        result[f"{side}_depth_1pct_first"]=grouped[side].first()
        result[f"{side}_depth_1pct_end"]=grouped[side].last()
        result[f"{side}_depth_1pct_mean"]=grouped[side].mean()
    result["book_snapshot_count"]=grouped.size().astype(float)
    result["bid_depth_change_1m"]=result["bid_depth_1pct_end"]/result["bid_depth_1pct_first"]-1.0
    result["ask_depth_change_1m"]=result["ask_depth_1pct_end"]/result["ask_depth_1pct_first"]-1.0
    total=result["bid_depth_1pct_end"]+result["ask_depth_1pct_end"]
    result["book_imbalance_end"]=(result["bid_depth_1pct_end"]-result["ask_depth_1pct_end"])/total.replace(0.0,np.nan)
    return result

def main() -> None:
    agg_frames=[aggregate_trades(path) for path in sorted(AGG.glob("BTCUSDT-aggTrades-*.zip"))]
    book_frames=[aggregate_book(path) for path in sorted(BOOK.glob("BTCUSDT-bookDepth-*.zip"))]
    if len(agg_frames)!=10 or len(book_frames)!=10: raise ValueError("expected ten daily direct-data archives per source")
    trades=pd.concat(agg_frames).sort_index()
    book=pd.concat(book_frames).sort_index()
    if trades.index.has_duplicates or book.index.has_duplicates: raise ValueError("duplicate minute features")
    raw=load_raw_one_minute(RAW)
    data=raw[["open","high","low","close","volume"]].join(trades,how="left").join(book,how="left")
    flow_columns=["aggressive_total_quote_1m","aggressive_signed_quote_1m","aggressive_buy_quote_1m",
                  "aggressive_sell_quote_1m","trade_count_1m"]
    data[flow_columns]=data[flow_columns].fillna(0.0)
    depth_columns=[c for c in data.columns if "depth_1pct" in c or c in {"book_snapshot_count","book_imbalance_end","bid_depth_change_1m","ask_depth_change_1m"}]
    for c in [c for c in depth_columns if c not in {"bid_depth_change_1m","ask_depth_change_1m"}]:
        data[c]=data[c].ffill()
    data["bid_depth_change_1m"]=data["bid_depth_change_1m"].fillna(0.0)
    data["ask_depth_change_1m"]=data["ask_depth_change_1m"].fillna(0.0)
    data["signed_flow_ratio_1m"]=data["aggressive_signed_quote_1m"]/data["aggressive_total_quote_1m"].replace(0.0,np.nan)
    data["signed_flow_ratio_1m"]=data["signed_flow_ratio_1m"].fillna(0.0)
    data["depth_imbalance_1pct"]=data["book_imbalance_end"]
    log1=np.log(data["close"]/data["close"].shift(1))
    data["log_ret_5m"]=np.log(data["close"]/data["close"].shift(5))
    data["realized_vol_30m"]=log1.rolling(30,min_periods=10).std(ddof=0)
    data["vol_5m"]=data["aggressive_total_quote_1m"].rolling(5,min_periods=1).sum()
    buy5=data["aggressive_buy_quote_1m"].rolling(5,min_periods=1).sum()
    data["taker_buy_ratio_5m"]=buy5/data["vol_5m"].replace(0.0,np.nan)
    signed50=data["aggressive_signed_quote_1m"].rolling(50,min_periods=10).sum().abs()
    total50=data["aggressive_total_quote_1m"].rolling(50,min_periods=10).sum()
    data["vpin_50"]=signed50/total50.replace(0.0,np.nan)
    data["hawkes_net"]=data["signed_flow_ratio_1m"]
    data["oi_change_1h"]=0.0
    required=["close","log_ret_5m","realized_vol_30m","vol_5m","taker_buy_ratio_5m",
              "depth_imbalance_1pct","vpin_50","hawkes_net","oi_change_1h",
              "aggressive_signed_quote_1m","aggressive_total_quote_1m","signed_flow_ratio_1m",
              "ask_depth_1pct_end","bid_depth_1pct_end","ask_depth_change_1m","bid_depth_change_1m",
              "book_snapshot_count"]
    matrix=data[required].replace([np.inf,-np.inf],np.nan)
    if matrix[["close","aggressive_total_quote_1m"]].isna().any().any(): raise ValueError("critical feature gaps")
    FEATURE_ROOT.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
    timestamps=matrix.index.astype("int64").to_numpy(dtype=np.int64)
    values=matrix.to_numpy(dtype=np.float64,copy=True)
    npz=FEATURE_ROOT/"v48_features.npz"
    np.savez_compressed(npz,timestamps_ns=timestamps,values=values)
    (FEATURE_ROOT/"columns.json").write_text(json.dumps(required,indent=2),encoding="utf-8")
    coverage={"rows":len(matrix),"first_minute_close_utc":matrix.index[0].isoformat(),
              "last_minute_close_utc":matrix.index[-1].isoformat(),
              "columns":required,"feature_npz_sha256":sha256(npz.read_bytes()).hexdigest(),
              "feature_npz_size":npz.stat().st_size,"causality":"each row uses only trades and depth snapshots from its completed minute"}
    (OUT/"feature_manifest.json").write_text(json.dumps(coverage,indent=2),encoding="utf-8")
    (OUT/"STATUS_CAUSAL_FEATURES_READY").write_text(str(len(matrix))+"\n",encoding="utf-8")
    print(json.dumps(coverage,indent=2))
if __name__=="__main__": main()
