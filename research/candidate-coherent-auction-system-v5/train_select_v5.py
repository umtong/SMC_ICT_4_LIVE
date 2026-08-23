#!/usr/bin/env python3
"""Cross-fitted prior/posterior direction and executable action selection."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence
import json

import numpy as np
import pandas as pd

import train_select as shared
import train_select_v4 as v4


def _state_target(frame: pd.DataFrame) -> np.ndarray:
    long_side=frame.action_side.astype(str).eq('LONG')
    return ((long_side & frame.destination_label.eq('UPPER_FIRST')) | (~long_side & frame.destination_label.eq('LOWER_FIRST'))).astype(int).to_numpy()


def _usable_state_columns(frame: pd.DataFrame, mode: str) -> list[str]:
    base=v4.feature_columns(frame,keep_economics=False)
    if mode=='prior':
        return sorted([column for column in base if column.startswith('prior_') or column.startswith(('source_pool_','source_semantic_','source_scale_','source_strength_','source_defense_','clock_'))])
    if mode=='posterior':
        return sorted([column for column in base if not column.startswith('prior_')])
    raise ValueError(mode)


def _predict_state(frame: pd.DataFrame, mode: str, seed: int):
    modeling=frame[frame.destination_label.astype(str).isin(['UPPER_FIRST','LOWER_FIRST'])].copy().reset_index(drop=True)
    if modeling.empty:return modeling,np.array([]),np.array([]),[],None
    target=_state_target(modeling);development=modeling.period.astype(str).str.startswith('dev-').to_numpy();columns=_usable_state_columns(modeling,mode)
    probability,disagreement=shared._blocked_predictions(modeling,target,columns,development_mask=development,seed=seed)
    base_rate=float(np.mean(target[development])) if development.any() else float(np.mean(target))
    return modeling,probability,disagreement,columns,base_rate


def attach_hierarchical_direction(actions: pd.DataFrame,states: pd.DataFrame):
    prior,p_prior,u_prior,prior_columns,prior_base=_predict_state(states,'prior',31417)
    posterior,p_post,u_post,post_columns,post_base=_predict_state(states,'posterior',81731)
    if prior.empty or posterior.empty:
        output=actions.copy();output['prior_destination_probability']=0.5;output['posterior_destination_probability']=0.5;output['event_probability_update']=0.0;output['destination_probability']=0.5;output['destination_disagreement']=0.5
        return output,{"prior_features":prior_columns,"posterior_features":post_columns,"prior_base_rate":prior_base,"posterior_base_rate":post_base,"resolved_states":0}
    prior['prior_destination_probability']=p_prior;prior['prior_destination_disagreement']=u_prior
    posterior['posterior_destination_probability']=p_post;posterior['posterior_destination_disagreement']=u_post
    mapping=prior[['period','state_id','prior_destination_probability','prior_destination_disagreement']].merge(posterior[['period','state_id','posterior_destination_probability','posterior_destination_disagreement']],on=['period','state_id'],how='inner')
    output=actions.merge(mapping,on=['period','state_id'],how='left')
    output['prior_destination_probability']=pd.to_numeric(output.prior_destination_probability,errors='coerce').fillna(prior_base if prior_base is not None else .5).clip(.002,.998)
    output['posterior_destination_probability']=pd.to_numeric(output.posterior_destination_probability,errors='coerce').fillna(post_base if post_base is not None else .5).clip(.002,.998)
    output['prior_destination_disagreement']=pd.to_numeric(output.prior_destination_disagreement,errors='coerce').fillna(.35)
    output['posterior_destination_disagreement']=pd.to_numeric(output.posterior_destination_disagreement,errors='coerce').fillna(.35)
    output['event_probability_update']=output.posterior_destination_probability-output.prior_destination_probability
    continuation=output.narrative_branch.astype(str).eq('ACCEPTED_AUCTION_CONTINUATION')
    direction=np.empty(len(output),dtype=float);uncertainty=np.empty(len(output),dtype=float)
    prior_values=output.prior_destination_probability.to_numpy(float);post_values=output.posterior_destination_probability.to_numpy(float);prior_u=output.prior_destination_disagreement.to_numpy(float);post_u=output.posterior_destination_disagreement.to_numpy(float)
    direction[continuation.to_numpy()]=np.sqrt(prior_values[continuation.to_numpy()]*post_values[continuation.to_numpy()])
    uncertainty[continuation.to_numpy()]=.45*prior_u[continuation.to_numpy()]+.45*post_u[continuation.to_numpy()]+.20*np.abs(prior_values[continuation.to_numpy()]-post_values[continuation.to_numpy()])
    reversal=~continuation.to_numpy();positive_update=np.clip(post_values[reversal]-prior_values[reversal],0,1)
    direction[reversal]=post_values[reversal]*(.85+.15*positive_update)
    uncertainty[reversal]=.75*post_u[reversal]+.15*prior_u[reversal]+.15*np.clip(prior_values[reversal]-post_values[reversal],0,1)
    output['destination_probability']=np.clip(direction,.002,.998);output['destination_disagreement']=np.clip(uncertainty,0,1)
    return output,{"resolved_states":len(mapping),"prior_feature_count":len(prior_columns),"posterior_feature_count":len(post_columns),"prior_base_rate":prior_base,"posterior_base_rate":post_base,"prior_features":prior_columns,"posterior_features":post_columns}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    actions,states=v4._read(args.root);actions,direction_summary=attach_hierarchical_direction(actions,states);actions,fill_summary=v4.attach_fill(actions);actions,action_summary=v4.attach_action(actions);scored=v4.score(actions);scored.to_csv(args.output/'scored_action_universe.csv',index=False)
    dev_orders,dev_trades,dev_summary=v4.route_account(scored,'dev-');eval_orders,eval_trades,eval_summary=v4.route_account(scored,'eval-')
    dev_orders.to_csv(args.output/'development_oof_selected_orders.csv',index=False);eval_orders.to_csv(args.output/'evaluation_selected_orders.csv',index=False);dev_trades.to_csv(args.output/'development_oof_account_trades.csv',index=False);eval_trades.to_csv(args.output/'evaluation_account_trades.csv',index=False)
    v4.nearest_cases(scored,pd.concat([dev_trades,eval_trades],ignore_index=True,sort=False),args.output/'selected_trade_neighbors.csv')
    summary={"hierarchical_direction_model":direction_summary,"fill_model":fill_summary,"action_model":action_summary,"development_oof_account":dev_summary,"evaluation_account":eval_summary,"action_universe":len(actions),"state_universe":len(states),"selection":"pre-event direction prior updated by causal liquidity event, then fill/action and one global account","symbol_in_model":False,"period_in_model":False,"actual_fill_in_model":False,"risk_fraction":v4.RISK_FRACTION}
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True))


if __name__=='__main__':main()
