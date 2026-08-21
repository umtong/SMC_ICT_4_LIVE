#!/usr/bin/env python3
"""Cross-period causal case memory combined with the hierarchical model.

Each current decision is compared with complete actions from other periods only.  A
state with several entry/stop variants receives inverse-variant weight so one causal
episode cannot dominate memory by ID multiplication.  The model and memory are
independent views of the same coherent narrative; agreement strengthens evidence and
disagreement lowers expected growth.  No asset or period identity is a feature.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence
import json
import math

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.neighbors import NearestNeighbors
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, RobustScaler

import train_select_v4 as v4

K_NEIGHBORS = 72
RISK_FRACTION = 0.03


def _features(frame: pd.DataFrame) -> list[str]:
    columns = v4.feature_columns(frame, keep_economics=True)
    forbidden = (
        "destination_probability",
        "prior_destination_probability",
        "posterior_destination_probability",
        "action_probability",
        "fill_probability",
        "combined_probability",
        "conservative_probability",
        "robust_expected_r",
        "expected_log_growth",
        "selection_uncertainty",
    )
    columns = [column for column in columns if column not in forbidden]
    # Remove near-duplicate scalar traces while retaining the causal hierarchy and
    # ordered path blocks.  Selection depends on availability/variation only.
    useful=[]
    for column in columns:
        series=frame[column]
        if series.notna().mean()<.35 or series.nunique(dropna=True)<=1:continue
        useful.append(column)
    return sorted(useful)


def _preprocessor(frame: pd.DataFrame, columns: Sequence[str]):
    numeric=[column for column in columns if pd.api.types.is_numeric_dtype(frame[column])]
    categorical=[column for column in columns if column not in numeric]
    transforms=[]
    if numeric:
        transforms.append(('num',Pipeline([('imputer',SimpleImputer(strategy='median',add_indicator=True)),('scale',RobustScaler(quantile_range=(10,90)))]),numeric))
    if categorical:
        transforms.append(('cat',Pipeline([('imputer',SimpleImputer(strategy='most_frequent')),('onehot',OneHotEncoder(handle_unknown='ignore',min_frequency=2,sparse_output=False))]),categorical))
    return ColumnTransformer(transforms,remainder='drop',sparse_threshold=0.0)


def _aligned(frame: pd.DataFrame) -> np.ndarray:
    long_side=frame.side.astype(str).eq('LONG');label=frame.destination_label.astype(str)
    return ((long_side & label.eq('UPPER_FIRST')) | (~long_side & label.eq('LOWER_FIRST'))).astype(float).to_numpy()


def _weighted_probability(values: np.ndarray, weights: np.ndarray, fallback: float) -> float:
    valid=np.isfinite(values)&np.isfinite(weights)&(weights>0)
    if not valid.any():return fallback
    return float(np.sum(values[valid]*weights[valid])/np.sum(weights[valid]))


def _memory_fold(train: pd.DataFrame,test: pd.DataFrame,columns:Sequence[str],seed:int):
    pre=_preprocessor(train,columns);x_train=np.asarray(pre.fit_transform(train[list(columns)]),dtype=float);x_test=np.asarray(pre.transform(test[list(columns)]),dtype=float)
    # Cap a few extreme one-hot/numeric dimensions from dominating distance.
    x_train=np.clip(x_train,-8,8);x_test=np.clip(x_test,-8,8)
    neighbors=NearestNeighbors(n_neighbors=min(K_NEIGHBORS,len(train)),metric='euclidean',algorithm='auto',n_jobs=-1).fit(x_train)
    distance,index=neighbors.kneighbors(x_test,return_distance=True)
    variants=train.groupby(['period','state_id']).action_id.transform('size').to_numpy(float)
    episode_weight=1.0/np.maximum(variants,1.0)
    fill=train.fill_state.astype(str).str.startswith('FILLED').astype(float).to_numpy()
    resolved=train.outcome.astype(str).isin(['TARGET_FIRST','STOP_FIRST','AMBIGUOUS_SAME_MINUTE','AMBIGUOUS_FILL_BARRIER_SAME_MINUTE','TIME_EXIT']).to_numpy()
    win=train.outcome.astype(str).eq('TARGET_FIRST').astype(float).to_numpy();win[~resolved]=np.nan
    direction=_aligned(train);direction[~train.destination_label.astype(str).isin(['UPPER_FIRST','LOWER_FIRST']).to_numpy()]=np.nan
    base_fill=float(np.nanmean(fill));base_win=float(np.nanmean(win)) if np.isfinite(win).any() else .5;base_direction=float(np.nanmean(direction)) if np.isfinite(direction).any() else .5
    rows=[]
    for distances,positions in zip(distance,index):
        local_scale=float(np.median(distances[distances>0])) if np.any(distances>0) else 1.0
        weights=np.exp(-distances/max(local_scale,1e-9))*episode_weight[positions]
        fill_p=_weighted_probability(fill[positions],weights,base_fill)
        win_weights=weights*fill[positions]
        win_p=_weighted_probability(win[positions],win_weights,base_win)
        direction_p=_weighted_probability(direction[positions],weights,base_direction)
        effective=float(np.sum(weights)**2/max(np.sum(weights**2),1e-12))
        winner_dist=float(np.min(distances[np.isfinite(win[positions])&(win[positions]>0.5)])) if np.any(np.isfinite(win[positions])&(win[positions]>0.5)) else float(np.max(distances))
        loser_dist=float(np.min(distances[np.isfinite(win[positions])&(win[positions]<0.5)])) if np.any(np.isfinite(win[positions])&(win[positions]<0.5)) else float(np.max(distances))
        rows.append({'case_fill_probability':fill_p,'case_action_probability':win_p,'case_destination_probability':direction_p,'case_effective_support':effective,'case_median_distance':float(np.median(distances)),'case_nearest_winner_distance':winner_dist,'case_nearest_loss_distance':loser_dist})
    return pd.DataFrame(rows,index=test.index)


def cross_fitted_memory(frame: pd.DataFrame):
    columns=_features(frame);output=pd.DataFrame(index=frame.index)
    dev=frame.period.astype(str).str.startswith('dev-')
    for fold,period in enumerate(sorted(frame.loc[dev,'period'].astype(str).unique())):
        test_mask=dev & frame.period.astype(str).eq(period);train_mask=dev & ~test_mask
        if not train_mask.any() or not test_mask.any():continue
        output.loc[test_mask,_memory_fold(frame.loc[train_mask],frame.loc[test_mask],columns,fold).columns]=_memory_fold(frame.loc[train_mask],frame.loc[test_mask],columns,fold).to_numpy()
    eval_mask=~dev
    if eval_mask.any() and dev.any():
        fold_frame=_memory_fold(frame.loc[dev],frame.loc[eval_mask],columns,1001)
        output.loc[eval_mask,fold_frame.columns]=fold_frame.to_numpy()
    for column,default in (('case_fill_probability',.5),('case_action_probability',.5),('case_destination_probability',.5),('case_effective_support',0.0),('case_median_distance',8.0),('case_nearest_winner_distance',8.0),('case_nearest_loss_distance',8.0)):
        output[column]=pd.to_numeric(output.get(column),errors='coerce').fillna(default)
    return output,columns


def consensus_score(frame: pd.DataFrame):
    output=frame.copy();memory,columns=cross_fitted_memory(output);output=output.join(memory)
    model_direction=pd.to_numeric(output.destination_probability,errors='coerce').clip(.002,.998);model_action=pd.to_numeric(output.action_probability,errors='coerce').clip(.002,.998);model_fill=pd.to_numeric(output.fill_probability,errors='coerce').clip(.002,.998)
    case_direction=output.case_destination_probability.clip(.002,.998);case_action=output.case_action_probability.clip(.002,.998);case_fill=output.case_fill_probability.clip(.002,.998)
    model_win=np.sqrt(model_direction*model_action);case_win=np.sqrt(case_direction*case_action);agreement_gap=(model_win-case_win).abs()
    support_penalty=(1.0-output.case_effective_support.clip(0,K_NEIGHBORS)/K_NEIGHBORS)*.14
    distance_reference=float(output.loc[output.period.astype(str).str.startswith('dev-'),'case_median_distance'].median())
    distance_penalty=(output.case_median_distance/max(distance_reference,1e-9)-1.0).clip(lower=0,upper=2)*.06
    model_uncertainty=pd.to_numeric(output.selection_uncertainty,errors='coerce').fillna(.35)
    consensus=np.sqrt(model_win*case_win);conservative_win=(consensus-.38*agreement_gap-.35*model_uncertainty-support_penalty-distance_penalty).clip(.002,.998)
    fill_gap=(model_fill-case_fill).abs();conservative_fill=(np.sqrt(model_fill*case_fill)-.35*fill_gap-.08*support_penalty).clip(.002,.998)
    target_r=pd.to_numeric(output.planned_account_target_r,errors='coerce');stop_r=pd.to_numeric(output.planned_account_stop_r,errors='coerce').fillna(-1.0)
    output['model_case_probability_gap']=agreement_gap;output['case_memory_feature_count']=len(columns);output['conservative_probability']=conservative_win;output['conservative_fill_probability']=conservative_fill;output['robust_expected_r']=conservative_fill*(conservative_win*target_r+(1-conservative_win)*stop_r);output['expected_log_growth']=conservative_fill*(conservative_win*np.log1p(RISK_FRACTION*target_r.clip(lower=-.999/RISK_FRACTION))+(1-conservative_win)*np.log1p(RISK_FRACTION*stop_r.clip(lower=-.999/RISK_FRACTION)));output['selection_uncertainty']=model_uncertainty+agreement_gap+support_penalty+distance_penalty
    return output,columns


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--model-result',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    actions,states=v4._read(args.root);model=pd.read_csv(args.model_result/'scored_action_universe.csv');destination=states[['period','state_id','destination_label']].drop_duplicates(['period','state_id']);frame=actions.merge(model[['period','action_id','destination_probability','destination_disagreement','fill_probability','fill_disagreement','action_probability','action_disagreement','combined_probability','selection_uncertainty']],on=['period','action_id'],how='inner').merge(destination,on=['period','state_id'],how='left')
    scored,columns=consensus_score(frame);scored.to_csv(args.output/'scored_action_universe.csv',index=False)
    dev_orders,dev_trades,dev_summary=v4.route_account(scored,'dev-');eval_orders,eval_trades,eval_summary=v4.route_account(scored,'eval-');dev_orders.to_csv(args.output/'development_oof_selected_orders.csv',index=False);eval_orders.to_csv(args.output/'evaluation_selected_orders.csv',index=False);dev_trades.to_csv(args.output/'development_oof_account_trades.csv',index=False);eval_trades.to_csv(args.output/'evaluation_account_trades.csv',index=False);v4.nearest_cases(scored,pd.concat([dev_trades,eval_trades],ignore_index=True,sort=False),args.output/'selected_trade_neighbors.csv')
    summary={'memory_feature_count':len(columns),'memory_features':columns,'development_oof_account':dev_summary,'evaluation_account':eval_summary,'action_universe':len(scored),'selection':'hierarchical model and cross-period causal case memory consensus; one global pending order or position','symbol_in_model':False,'period_in_model':False,'actual_fill_in_model':False,'risk_fraction':RISK_FRACTION}
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True))


if __name__=='__main__':main()
