#!/usr/bin/env python3
"""Trade-by-trade causal audit for coherent auction system v4."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
import json
import math

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import RobustScaler

import train_select as shared


RESOLVED = {"TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE", "TIME_EXIT"}


def aligned(row: pd.Series) -> bool | None:
    label, side = str(row.get("destination_label", "")), str(row.get("side", ""))
    if label not in {"UPPER_FIRST", "LOWER_FIRST"}:
        return None
    return (side == "LONG" and label == "UPPER_FIRST") or (side == "SHORT" and label == "LOWER_FIRST")


def diagnosis(row: pd.Series) -> str:
    outcome = str(row.get("outcome", ""))
    if outcome == "TARGET_FIRST":
        return "COMPLETE_NARRATIVE_WIN"
    direction = aligned(row)
    if direction is False:
        return "DIRECTION_OR_LIQUIDITY_DESTINATION_WRONG"
    if outcome == "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE":
        return "LIMIT_FILL_SEQUENCE_UNRESOLVED_CONSERVATIVE_LOSS"
    if direction is True and outcome in {"STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"}:
        return "DIRECTION_RIGHT_ENTRY_OR_INVALIDATION_WRONG"
    if outcome == "TIME_EXIT":
        return "THESIS_STALE_WITHOUT_BARRIER_RESOLUTION"
    return "DESTINATION_UNRESOLVED_OR_ROUTE_MAP_INCOMPLETE"


def group_table(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    rows=[]
    if frame.empty:return pd.DataFrame()
    for keys, group in frame.groupby(columns, dropna=False):
        if not isinstance(keys,tuple):keys=(keys,)
        record={column:value for column,value in zip(columns,keys)}
        fills=group.fill_state.astype(str).str.startswith('FILLED')
        resolved=group[fills & group.outcome.astype(str).isin(RESOLVED)]
        record.update({"actions":len(group),"filled":int(fills.sum()),"fill_rate":float(fills.mean()),"trades":len(resolved),"wins":int(resolved.outcome.astype(str).eq('TARGET_FIRST').sum()),"win_rate":float(resolved.outcome.astype(str).eq('TARGET_FIRST').mean()) if len(resolved) else np.nan,"mean_net_r":float(pd.to_numeric(resolved.net_r,errors='coerce').mean()) if len(resolved) else np.nan,"periods":int(group.period.nunique())})
        rows.append(record)
    return pd.DataFrame(rows).sort_values(['periods','trades','mean_net_r'],ascending=[False,False,False])


def loss_clusters(frame: pd.DataFrame):
    losses=frame[(frame.fill_state.astype(str).str.startswith('FILLED')) & (~frame.outcome.astype(str).eq('TARGET_FIRST')) & frame.outcome.astype(str).isin(RESOLVED)].copy()
    candidates=["semantic_attraction_normalized","structure_multiscale_trend_vote","structure_multiscale_trend_agreement","dealing_range_position","approach_path_efficiency","approach_delta_share_12m_toward","event_delta_share_signed","confirmation_delta_share_signed","decision_delta_share_signed","response_delta_signed","event_penetration_bps","event_to_confirmation_minutes","return_wait_minutes","response_delay_minutes","risk_bps","gross_rr","planned_account_target_r","destination_probability","fill_probability","action_probability","volume_route_target_node_share","route_obstacle_distance_bps","source_accumulation_distinct_visits"]
    columns=[column for column in candidates if column in losses.columns]
    if len(losses)<8 or len(columns)<3:return losses,pd.DataFrame()
    matrix=losses[columns].apply(pd.to_numeric,errors='coerce');x=RobustScaler(quantile_range=(10,90)).fit_transform(SimpleImputer(strategy='median').fit_transform(matrix));clusters=min(7,max(2,int(round(math.sqrt(len(losses)/2)))));model=KMeans(n_clusters=clusters,random_state=427,n_init=30);losses['loss_cluster']=model.fit_predict(x)
    rows=[]
    for cluster,group in losses.groupby('loss_cluster'):
        record={"loss_cluster":int(cluster),"trades":len(group),"mean_net_r":float(pd.to_numeric(group.net_r,errors='coerce').mean()),"dominant_diagnosis":str(group.diagnosis.value_counts().index[0]),"dominant_branch":str(group.narrative_branch.value_counts().index[0]),"dominant_entry":str(group.entry_geometry.value_counts().index[0]),"dominant_stop":str(group.stop_geometry.value_counts().index[0]),"dominant_target":str(group.objective_kind.value_counts().index[0])}
        for column in columns:record[f'median_{column}']=float(pd.to_numeric(group[column],errors='coerce').median())
        rows.append(record)
    return losses,pd.DataFrame(rows).sort_values('trades',ascending=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--result',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    actions,states=shared._read_universes(args.root);scored=pd.read_csv(args.result/'scored_action_universe.csv');destination=states[['period','state_id','destination_label']].drop_duplicates(['period','state_id']);scored=scored.merge(destination,on=['period','state_id'],how='left');scored['diagnosis']=[diagnosis(row) for _,row in scored.iterrows()]
    scored.to_csv(args.output/'all_action_diagnoses.csv',index=False)
    selected_orders=[];selected_trades=[]
    for filename in ('development_oof_selected_orders.csv','evaluation_selected_orders.csv'):
        path=args.result/filename
        if path.exists():
            frame=pd.read_csv(path)
            if not frame.empty:selected_orders.append(frame)
    for filename in ('development_oof_account_trades.csv','evaluation_account_trades.csv'):
        path=args.result/filename
        if path.exists():
            frame=pd.read_csv(path)
            if not frame.empty:selected_trades.append(frame)
    orders=pd.concat(selected_orders,ignore_index=True,sort=False) if selected_orders else pd.DataFrame();trades=pd.concat(selected_trades,ignore_index=True,sort=False) if selected_trades else pd.DataFrame()
    if not orders.empty:
        orders=orders.drop(columns=['destination_label'],errors='ignore').merge(destination,on=['period','state_id'],how='left');orders['diagnosis']=[diagnosis(row) for _,row in orders.iterrows()];orders.to_csv(args.output/'selected_order_audit.csv',index=False)
    if not trades.empty:
        trades=trades.drop(columns=['destination_label'],errors='ignore').merge(destination,on=['period','state_id'],how='left');trades['diagnosis']=[diagnosis(row) for _,row in trades.iterrows()];trades.to_csv(args.output/'selected_trade_audit.csv',index=False)
    group_table(scored,['narrative_branch','source_pool_kind','setup_kind','location_kind','response_kind']).to_csv(args.output/'market_logic_diagnosis.csv',index=False)
    group_table(scored,['entry_geometry','stop_geometry','objective_kind']).to_csv(args.output/'execution_geometry_diagnosis.csv',index=False)
    group_table(scored,['period','narrative_branch','diagnosis']).to_csv(args.output/'period_diagnosis.csv',index=False)
    group_table(scored,['source_pool_kind','source_pool_accumulated','diagnosis']).to_csv(args.output/'liquidity_source_diagnosis.csv',index=False)
    losses,clusters=loss_clusters(scored);losses.to_csv(args.output/'loss_cluster_members.csv',index=False);clusters.to_csv(args.output/'loss_clusters.csv',index=False)
    filled=scored[scored.fill_state.astype(str).str.startswith('FILLED') & scored.outcome.astype(str).isin(RESOLVED)].copy()
    episode=filled.groupby(['period','episode_id']).agg(actions=('action_id','size'),any_winner=('outcome',lambda values:any(str(value)=='TARGET_FIRST' for value in values)),best_net_r=('net_r','max'),direction_aligned=('destination_label',lambda values:any(str(value) in {'UPPER_FIRST','LOWER_FIRST'} for value in values))).reset_index();episode.to_csv(args.output/'episode_opportunity_ceiling.csv',index=False)
    state_geometry=filled.groupby(['period','state_id']).agg(variants=('action_id','size'),any_winner=('outcome',lambda values:any(str(value)=='TARGET_FIRST' for value in values)),best_net_r=('net_r','max')).reset_index();state_geometry.to_csv(args.output/'state_geometry_ceiling.csv',index=False)
    summary={"actions":len(scored),"filled_resolved_actions":len(filled),"episodes":len(episode),"episode_winner_availability":float(episode.any_winner.mean()) if len(episode) else None,"states":len(state_geometry),"state_winner_availability":float(state_geometry.any_winner.mean()) if len(state_geometry) else None,"selected_orders":len(orders),"selected_filled_trades":len(trades),"selected_diagnoses":trades.diagnosis.value_counts().to_dict() if len(trades) else {},"selected_fill_rate":float(orders.fill_state.astype(str).str.startswith('FILLED').mean()) if len(orders) else None,"loss_clusters":len(clusters)}
    (args.output/'audit_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True))


if __name__=='__main__':main()
