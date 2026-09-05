"""Same causal opportunities and account execution; corrected probability model."""
from pathlib import Path
import json,pickle,traceback
import run_control_v2 as base
from control_model import fit_barrier,describe_observations

base.OUT=Path('research_results/astra1_control_v3');base.OUT.mkdir(parents=True,exist_ok=True)

def fit(labels,train_end,cal_end):
    train=labels[labels.label_closed<base.ns(train_end)].copy()
    cal=labels[(labels.observed_time_ns>=base.ns(train_end))&(labels.label_closed<base.ns(cal_end))].copy()
    decision,metadata=fit_barrier(train,cal,base.FEATURES,base.ns(cal_end))
    metadata.update(train_end=train_end,calibration_end=cal_end)
    base.write(base.OUT/'model.json',metadata)
    base.write(base.OUT/'logic_observations.json',{'training':describe_observations(train),'calibration':describe_observations(cal)})
    (base.OUT/'decision.pkl').write_bytes(pickle.dumps(decision))
    decision.model.save_model(str(base.OUT/'trees.txt'))
    print('BARRIER_MODEL',json.dumps(metadata),flush=True)
    return decision

base.fit=fit
if __name__=='__main__':
    try:base.main()
    except Exception:
        (base.OUT/'error.txt').write_text(traceback.format_exc());raise
