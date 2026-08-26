#!/usr/bin/env python3
"""Calibrated model/case consensus without arbitrary conservative trade thresholds.

The direct target-first model already sees the liquidity-direction probabilities.  It
therefore owns final win probability instead of being multiplied by direction a second
time.  Ensemble disagreement and case distance shrink estimates toward the causal
training base rate; they are not subtracted as ad-hoc safety margins.  The only trade
selection is positive expected log growth after modeled costs.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import json

import numpy as np
import pandas as pd

import train_select_v4 as v4
import case_memory_consensus as memory

RISK_FRACTION=0.03
EPS=1e-6


def logit(values):
    values=np.clip(np.asarray(values,dtype=float),EPS,1-EPS)
    return np.log(values/(1-values))


def sigmoid(values):
    values=np.asarray(values,dtype=float)
    return 1/(1+np.exp(-np.clip(values,-30,30)))


def causal_base_rates(frame:pd.DataFrame):
    dev=frame.period.astype(str).str.startswith('dev-');filled=frame.fill_state.astype(str).str.startswith('FILLED');resolved=frame.outcome.astype(str).isin(['TARGET_FIRST','STOP_FIRST','AMBIGUOUS_SAME_MINUTE','AMBIGUOUS_FILL_BARRIER_SAME_MINUTE','TIME_EXIT']);win=frame.outcome.astype(str).eq('TARGET_FIRST')
    base_fill=np.full(len(frame),.5);base_win=np.full(len(frame),.5)
    for period in frame.period.astype(str).unique():
        test=frame.period.astype(str).eq(period).to_numpy();train=(dev & ~frame.period.astype(str).eq(period)).to_numpy() if str(period).startswith('dev-') else dev.to_numpy()
        if train.any():
            base_fill[test]=float(filled.to_numpy()[train].mean())
            win_train=train & filled.to_numpy() & resolved.to_numpy()
            if win_train.any():base_win[test]=float(win.to_numpy()[win_train].mean())
    return base_fill,base_win


def score(frame:pd.DataFrame):
    output=frame.copy();case,columns=memory.cross_fitted_memory(output);output=output.join(case);base_fill,base_win=causal_base_rates(output)
    model_win=pd.to_numeric(output.action_probability,errors='coerce').fillna(base_win).clip(EPS,1-EPS).to_numpy(float);model_fill=pd.to_numeric(output.fill_probability,errors='coerce').fillna(base_fill).clip(EPS,1-EPS).to_numpy(float);case_win=output.case_action_probability.clip(EPS,1-EPS).to_numpy(float);case_fill=output.case_fill_probability.clip(EPS,1-EPS).to_numpy(float)
    action_std=pd.to_numeric(output.action_disagreement,errors='coerce').fillna(.35).to_numpy(float);fill_std=pd.to_numeric(output.fill_disagreement,errors='coerce').fillna(.35).to_numpy(float)
    model_win_reliability=1/(1+2.0*np.maximum(action_std,0));model_fill_reliability=1/(1+2.0*np.maximum(fill_std,0))
    support=output.case_effective_support.to_numpy(float);support_reliability=support/(support+12.0);development_distance=float(output.loc[output.period.astype(str).str.startswith('dev-'),'case_median_distance'].median());distance_ratio=np.maximum(output.case_median_distance.to_numpy(float)/max(development_distance,EPS)-1,0);distance_reliability=np.exp(-distance_ratio);case_reliability=support_reliability*distance_reliability
    model_win_shrunk=base_win+model_win_reliability*(model_win-base_win);model_fill_shrunk=base_fill+model_fill_reliability*(model_fill-base_fill);case_win_shrunk=base_win+case_reliability*(case_win-base_win);case_fill_shrunk=base_fill+case_reliability*(case_fill-base_fill)
    final_win=sigmoid((logit(model_win_shrunk)+case_reliability*logit(case_win_shrunk))/(1+case_reliability));final_fill=sigmoid((logit(model_fill_shrunk)+case_reliability*logit(case_fill_shrunk))/(1+case_reliability))
    target_r=pd.to_numeric(output.planned_account_target_r,errors='coerce').to_numpy(float);stop_r=pd.to_numeric(output.planned_account_stop_r,errors='coerce').fillna(-1).to_numpy(float)
    output['causal_base_win_probability']=base_win;output['causal_base_fill_probability']=base_fill;output['case_reliability']=case_reliability;output['model_win_reliability']=model_win_reliability;output['model_fill_reliability']=model_fill_reliability;output['conservative_probability']=final_win;output['conservative_fill_probability']=final_fill;output['model_case_probability_gap']=np.abs(model_win-case_win);output['selection_uncertainty']=(1-model_win_reliability)+(1-case_reliability)+np.abs(model_win-case_win);output['robust_expected_r']=final_fill*(final_win*target_r+(1-final_win)*stop_r);output['expected_log_growth']=final_fill*(final_win*np.log1p(RISK_FRACTION*np.clip(target_r,-.999/RISK_FRACTION,None))+(1-final_win)*np.log1p(RISK_FRACTION*np.clip(stop_r,-.999/RISK_FRACTION,None)))
    return output,columns


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--model-result',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);a=parser.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    actions,states=v4._read(a.root);model=pd.read_csv(a.model_result/'scored_action_universe.csv');destination=states[['period','state_id','destination_label']].drop_duplicates(['period','state_id']);columns=['period','action_id','destination_probability','destination_disagreement','fill_probability','fill_disagreement','action_probability','action_disagreement','prior_destination_probability','posterior_destination_probability','event_probability_update']
    available=[column for column in columns if column in model.columns];frame=actions.merge(model[available],on=['period','action_id'],how='inner').merge(destination,on=['period','state_id'],how='left');scored,features=score(frame);scored.to_csv(a.output/'scored_action_universe.csv',index=False)
    dev_orders,dev_trades,dev_summary=v4.route_account(scored,'dev-');eval_orders,eval_trades,eval_summary=v4.route_account(scored,'eval-');dev_orders.to_csv(a.output/'development_oof_selected_orders.csv',index=False);eval_orders.to_csv(a.output/'evaluation_selected_orders.csv',index=False);dev_trades.to_csv(a.output/'development_oof_account_trades.csv',index=False);eval_trades.to_csv(a.output/'evaluation_account_trades.csv',index=False);v4.nearest_cases(scored,pd.concat([dev_trades,eval_trades],ignore_index=True,sort=False),a.output/'selected_trade_neighbors.csv')
    summary={'memory_feature_count':len(features),'development_oof_account':dev_summary,'evaluation_account':eval_summary,'action_universe':len(scored),'selection':'calibrated direct target-first model plus cross-period case memory, reliability shrinkage only, positive expected log growth, one global pending order or position','arbitrary_probability_threshold':False,'symbol_in_model':False,'period_in_model':False,'actual_fill_in_model':False,'risk_fraction':RISK_FRACTION}
    (a.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True))


if __name__=='__main__':main()
