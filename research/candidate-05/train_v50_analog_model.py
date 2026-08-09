#!/usr/bin/env python3
"""Train Candidate 05 v50 fixed high-precision causal analog selector.

The recorder supplies every completed v26 entry proposal from chronologically
pre-evaluation NautilusTrader runs.  This script labels the proposal's own
entry, structural stop and liquidity target against later public one-minute
bars.  Labels guide model research only; all performance claims and fills remain
NautilusTrader results.  Hyperparameters are fixed before validation.
"""
from __future__ import annotations

import argparse
from datetime import date,timedelta
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
import pandas as pd

import features
from timestamp_contract import install as install_timestamp_contract
from v50_candidate_common import FEATURE_NAMES

install_timestamp_contract()

K=41
MIN_NEIGHBOR_WIN_RATE=0.85
MIN_NEIGHBOR_EXPECTANCY_R=0.50
MAX_NEIGHBOR_DISTANCE=4.0
MAX_HOLD_BARS=180


def _candidate_records(root:Path)->list[dict[str,Any]]:
    records:list[dict[str,Any]]=[]
    for path in root.rglob('strategy_diagnostics.json'):
        try:data=json.loads(path.read_text(encoding='utf-8'))
        except Exception:continue
        stack=[data]
        while stack:
            value=stack.pop()
            if isinstance(value,dict):
                candidates=value.get('v50_candidates')
                if isinstance(candidates,list):
                    for item in candidates:
                        if isinstance(item,dict):records.append(item)
                stack.extend(value.values())
            elif isinstance(value,list):stack.extend(value)
    selected:dict[tuple[int,str],dict[str,Any]]={}
    for record in records:
        key=(int(record.get('ts_event',0) or 0),str(record.get('helper','')))
        selected[key]=record
    return sorted(selected.values(),key=lambda row:int(row.get('ts_event',0) or 0))


def _date_from_ns(value:int)->date:
    return pd.Timestamp(value,unit='ns',tz='UTC').date()


def _bars(records:list[dict[str,Any]],cache:Path)->pd.DataFrame:
    if not records:return pd.DataFrame()
    start=_date_from_ns(min(int(row['ts_event']) for row in records))-timedelta(days=1)
    end=_date_from_ns(max(int(row['ts_event']) for row in records))+timedelta(days=2)
    frames=[];day=start
    while day<=end:
        archive,_,_=features.download_checked('klines','BTCUSDT',day,cache)
        frames.append(features.read_kline(archive));day+=timedelta(days=1)
    frame=pd.concat(frames,ignore_index=True).sort_values('close_time_dt').drop_duplicates('close_time_dt')
    frame['ts_ns']=frame['close_time_dt'].astype('int64')
    return frame[['ts_ns','open','high','low','close']].reset_index(drop=True)


def _label(record:dict[str,Any],bars:pd.DataFrame)->dict[str,Any]|None:
    side=int(record['side']);entry=float(record['entry_price']);stop=float(record['stop_price']);target=float(record['target_price']);ts=int(record['ts_event'])
    if side not in (-1,1) or not all(math.isfinite(value) for value in (entry,stop,target)) or side*(entry-stop)<=0.0 or side*(target-entry)<=0.0:return None
    future=bars[bars['ts_ns']>ts].head(MAX_HOLD_BARS+60)
    filled=False;fill_index=None
    for index,row in future.iterrows():
        low=float(row['low']);high=float(row['high'])
        touches_entry=low<=entry<=high
        if not filled:
            if not touches_entry:continue
            # Entry and either exit in the same minute has unknowable ordering.
            if low<=stop<=high or low<=target<=high:return None
            filled=True;fill_index=index;continue
        stop_hit=low<=stop<=high
        target_hit=low<=target<=high
        if stop_hit and target_hit:return None
        if target_hit:return {'label':1,'outcome':'TARGET_FIRST','exit_ts':int(row['ts_ns']),'bars_to_exit':int(index-fill_index),'reward_r':side*(target-entry)/(side*(entry-stop))}
        if stop_hit:return {'label':0,'outcome':'STOP_FIRST','exit_ts':int(row['ts_ns']),'bars_to_exit':int(index-fill_index),'reward_r':side*(target-entry)/(side*(entry-stop))}
        if fill_index is not None and index-fill_index>=MAX_HOLD_BARS:break
    return None


def _dataset(root:Path,cache:Path)->tuple[np.ndarray,np.ndarray,np.ndarray,list[dict[str,Any]]]:
    records=_candidate_records(root);bars=_bars(records,cache)
    x=[];y=[];reward=[];labeled=[]
    for record in records:
        result=_label(record,bars)
        if result is None:continue
        values=np.asarray(record.get('features',[]),dtype=float)
        if values.shape!=(len(FEATURE_NAMES),):continue
        item={**record,**result};labeled.append(item);x.append(values);y.append(float(result['label']));reward.append(float(result['reward_r']))
    return np.asarray(x,dtype=float),np.asarray(y,dtype=float),np.asarray(reward,dtype=float),labeled


def _scaling(x:np.ndarray)->tuple[np.ndarray,np.ndarray]:
    center=np.nanmedian(x,axis=0);center=np.where(np.isfinite(center),center,0.0)
    filled=np.where(np.isfinite(x),x,center)
    scale=np.nanmedian(np.abs(filled-center),axis=0)*1.4826
    scale=np.where(np.isfinite(scale)&(scale>1e-9),scale,1.0)
    return center,scale


def _neighbors(query:np.ndarray,reference:np.ndarray,labels:np.ndarray,rewards:np.ndarray,center:np.ndarray,scale:np.ndarray)->dict[str,float]:
    q=np.where(np.isfinite(query),query,center);ref=np.where(np.isfinite(reference),reference,center);dist=np.sqrt(np.mean(((ref-q)/scale)**2,axis=1));order=np.argsort(dist)[:min(K,len(dist))]
    if len(order)<min(K,20):return {'selected':0.0,'win_rate':0.0,'expectancy_r':-1.0,'max_distance':math.inf,'mean_distance':math.inf}
    neighbor_labels=labels[order];neighbor_rewards=rewards[order];win_rate=float(neighbor_labels.mean());expectancy=float(np.mean(np.where(neighbor_labels>0.5,neighbor_rewards,-1.0)));maximum=float(dist[order].max());mean=float(dist[order].mean());selected=float(win_rate>=MIN_NEIGHBOR_WIN_RATE and expectancy>=MIN_NEIGHBOR_EXPECTANCY_R and maximum<=MAX_NEIGHBOR_DISTANCE)
    return {'selected':selected,'win_rate':win_rate,'expectancy_r':expectancy,'max_distance':maximum,'mean_distance':mean}


def _validate(x_train:np.ndarray,y_train:np.ndarray,r_train:np.ndarray,x_valid:np.ndarray,y_valid:np.ndarray,r_valid:np.ndarray,center:np.ndarray,scale:np.ndarray)->dict[str,Any]:
    rows=[]
    for x,y,reward in zip(x_valid,y_valid,r_valid,strict=True):
        state=_neighbors(x,x_train,y_train,r_train,center,scale)
        if state['selected']>0.5:rows.append({'label':int(y),'reward_r':float(reward),**state})
    trades=len(rows);wins=sum(row['label'] for row in rows);pnl_r=sum(row['reward_r'] if row['label'] else -1.0 for row in rows);gross=sum(row['reward_r'] for row in rows if row['label']);losses=trades-wins;largest=max((row['reward_r'] for row in rows if row['label']),default=0.0);largest_share=largest/gross if gross>0.0 else 1.0
    return {'trades':trades,'wins':wins,'win_rate':wins/trades if trades else 0.0,'net_r':pnl_r,'profit_factor':gross/losses if losses else math.inf if gross>0.0 else 0.0,'largest_winner_share':largest_share,'mean_neighbor_win_rate':sum(row['win_rate'] for row in rows)/trades if trades else 0.0,'mean_neighbor_expectancy_r':sum(row['expectancy_r'] for row in rows)/trades if trades else 0.0}


def main()->None:
    parser=argparse.ArgumentParser();parser.add_argument('--train-root',type=Path,required=True);parser.add_argument('--validation-root',type=Path,required=True);parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    x_train,y_train,r_train,train_records=_dataset(args.train_root,args.cache/'train');x_valid,y_valid,r_valid,valid_records=_dataset(args.validation_root,args.cache/'validation')
    if len(y_train)<120 or len(y_valid)<40:raise RuntimeError(f'insufficient labeled candidates train={len(y_train)} validation={len(y_valid)}')
    center,scale=_scaling(x_train);validation=_validate(x_train,y_train,r_train,x_valid,y_valid,r_valid,center,scale)
    passed=validation['trades']>=20 and validation['wins']>=16 and validation['win_rate']>=0.80 and validation['net_r']>0.0 and validation['profit_factor']>=2.0 and validation['largest_winner_share']<=0.35
    combined_x=np.concatenate((x_train,x_valid));combined_y=np.concatenate((y_train,y_valid));combined_r=np.concatenate((r_train,r_valid));final_center,final_scale=_scaling(combined_x)
    payload={'schema':'candidate-05-v50-analog-model-v1','feature_names':list(FEATURE_NAMES),'hyperparameters':{'k':K,'minimum_neighbor_win_rate':MIN_NEIGHBOR_WIN_RATE,'minimum_neighbor_expectancy_r':MIN_NEIGHBOR_EXPECTANCY_R,'maximum_neighbor_distance':MAX_NEIGHBOR_DISTANCE,'max_hold_bars':MAX_HOLD_BARS,'searched':False},'training_labeled_candidates':int(len(y_train)),'validation_labeled_candidates':int(len(y_valid)),'validation':validation,'validation_pass':bool(passed),'model':{'center':final_center.tolist(),'scale':final_scale.tolist(),'features':combined_x.tolist(),'labels':combined_y.astype(int).tolist(),'reward_r':combined_r.tolist()}}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(payload,indent=2,sort_keys=True),encoding='utf-8');print(json.dumps({key:value for key,value in payload.items() if key!='model'},indent=2,sort_keys=True))
    if not passed:raise SystemExit(3)


if __name__=='__main__':main()
