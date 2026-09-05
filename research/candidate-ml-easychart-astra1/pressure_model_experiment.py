"""A different information hypothesis, not another rescue threshold.

Keep the dense original control/first-return opportunities, but learn whether
independent spot demand, derivative dislocation, inventory change, and price-
flow ordering distinguish their outcomes. This explicitly tests information
which the original chart-only classifier did not observe.

Development: 2024/2025 chronological completed labels. Calibration: November
2025 only. All 2026 observations occur after both. Each reported portfolio is
an actual four-market, one-position Nautilus account. The chart-only ablation
is not added to the proposed system and its trades are never pooled with it.
"""
from pathlib import Path
import json,pickle
import numpy as np
import pandas as pd
from control_v2 import ControlPolicy,FEATURES as CHART_FEATURES
from control_model import fit_barrier
from pressure_features import PressureHistory,PRESSURE_COLUMNS

REQUIRED=('basis_bps','spot_flow_15','oi_change_15','basis_spot_bps')

def execute(base,request):
    base.ControlPolicy=ControlPolicy
    base.prepare(request['months'])
    tapes={};proposals={};labels=[];coverage={}
    for month in request['months']:
        tape=base.Tape(month);plans,stats,skips=tape.plans()
        pressure=PressureHistory(tape);pressure.attach(plans)
        plans=[p for p in plans if all(np.isfinite(p.features[c]) for c in REQUIRED)]
        tapes[month]=tape;proposals[month]=plans
        labeled=tape.labels(plans);labels.append(labeled)
        coverage[month]={'candidate_plans':len(plans),'completed_candidate_labels':len(labeled),'structural_counts':stats}
    data=pd.concat(labels,ignore_index=True)
    train_end=base.ns(request['train_end']);cal_end=base.ns(request['calibration_end']);cal_start=base.ns(request['calibration_start'])
    train=data[data.label_closed<train_end].copy()
    cal=data[(data.observed_time_ns>=cal_start)&(data.label_closed<cal_end)].copy()
    if len(train)<500 or len(cal)<100:raise ValueError(f'not enough distinct completed learning examples: {len(train)} / {len(cal)}')
    models={};metadata={}
    for name,columns in (('pressure',CHART_FEATURES+PRESSURE_COLUMNS),('chart_only',CHART_FEATURES)):
        model,details=fit_barrier(train,cal,columns,cal_end)
        models[name]=model;metadata[name]=details
        model.model.save_model(str(base.OUT/f'{name}_trees.txt'))
        (base.OUT/f'{name}_decision.pkl').write_bytes(pickle.dumps(model))
        details.update(train_end=request['train_end'],calibration_start=request['calibration_start'],calibration_end=request['calibration_end'],
                       current_input_required=REQUIRED,positioning_publication_delay_minutes=5,
                       opportunity_policy='unchanged causal control_v2; no category exclusions or target replacement')
    base.write(base.OUT/'models.json',metadata);base.write(base.OUT/'opportunities.json',coverage)
    results=[]
    for job in request['experiments']:
        cfg=dict(job);month=cfg.pop('month');cfg.pop('learned',None);name=cfg.pop('name')
        for model_name in ('pressure','chart_only'):
            result=base.backtest(tapes[month],proposals[month],models[model_name],name=f'{name}_{model_name}',**cfg)
            result['decision_model']=model_name;results.append(result)
            base.write(base.OUT/'latest.json',results)
    # These are candidate forecast diagnostics, NOT additional executed trades.
    forecast=[]
    for month in sorted(set(j['month'] for j in request['experiments'])):
        d=data[(data.observed_time_ns>=base.ns(month+'-01')) & (data.observed_time_ns<base.ns(str(pd.Timestamp(month+'-01')+pd.offsets.MonthBegin(1)).split()[0]))]
        if not len(d):continue
        for name,model in models.items():
            p=model.predict_frame(d);null=1/(1+d.gross_rr)
            forecast.append({'month':month,'model':name,'candidate_labels':len(d),
                'brier':float(np.mean((p-d.label_target)**2)),'null_brier':float(np.mean((null-d.label_target)**2)),
                'mean_probability':float(np.mean(p)),'target_rate':float(d.label_target.mean())})
    base.write(base.OUT/'forecast_observations.json',forecast)
    (base.OUT/'error.txt').unlink(missing_ok=True)
