from pathlib import Path
import json
here=Path(__file__).resolve().parent
(here/'executed_flow.py').write_text('''"""Causal, already-observed executed-flow response from the Astra2 collector.

The 5-second markout excludes trades whose response horizon has not elapsed
inside a completed observation bar. It is NOT a future label, L2 book feature,
identified liquidation or identification of an institutional participant.
"""
from pathlib import Path
import numpy as np
import pandas as pd
from astra_policy import MINUTE

NAMES=('opponent_markout','own_markout','trapped_opponent','trapped_self',
       'extreme_flow','extreme_share','late_flow','late_progress','flow_price_alignment')
MICRO_FEATURES=tuple(f'm_{name}_{n}' for n in (1,5) for name in NAMES)

class ExecutedFlow:
    def __init__(self,month,symbols):
        self.tables={}
        for s in symbols:
            paths=sorted((Path('micro_market')/'60s'/s).glob(f'{month}-*.parquet'))
            if not paths:continue
            d=pd.concat([pd.read_parquet(p) for p in paths]).sort_index()
            if d.index.duplicated().any():raise ValueError('duplicate microstructure observation')
            times=d.index.as_unit('ns').asi8
            self.tables[s]=(times,d,d.rolling(5,min_periods=5).mean())
    def at(self,symbol,ts,side,unit_bps):
        if symbol not in self.tables:return None
        stamps,one,five=self.tables[symbol]
        i=np.searchsorted(stamps,ts,side='right')-1
        if i<4 or stamps[i]!=ts or stamps[i]-stamps[i-4]!=4*MINUTE:return None
        long=side>0;out={}
        for n,table in ((1,one),(5,five)):
            b=table.iloc[i]
            values=(-b['sell_markout_5s_bps' if long else 'buy_markout_5s_bps']/unit_bps,
                    b['buy_markout_5s_bps' if long else 'sell_markout_5s_bps']/unit_bps,
                    b['trapped_sell_share' if long else 'trapped_buy_share'],
                    b['trapped_buy_share' if long else 'trapped_sell_share'],
                    side*b['low_delta' if long else 'high_delta'],
                    b['low_volume_share' if long else 'high_volume_share'],
                    side*b.late_delta,side*b.late_return_bps/unit_bps,b.price_delta_correlation)
            out.update({f'm_{name}_{n}':float(v) for name,v in zip(NAMES,values,strict=True)})
        return out
''')
p=here/'research.py';s=p.read_text()
old='FEATURES=AUCTION_FEATURES+EXTRA_FEATURES'
new='from executed_flow import ExecutedFlow,MICRO_FEATURES\nFEATURES=AUCTION_FEATURES+EXTRA_FEATURES+MICRO_FEATURES'
assert s.count(old)==1;s=s.replace(old,new)
old='        self.extra=ExtraObservations(month,self.raw)'
assert s.count(old)==1;s=s.replace(old,old+'\n        self.micro=ExecutedFlow(month,symbols)')
old="            f=dict(p.features)\n            f.update(self.extra.at(p.symbol,p.observed_time_ns,int(p.side.value),unit_bps))"
new="""            micro=self.micro.at(p.symbol,p.observed_time_ns,int(p.side.value),unit_bps)
            if micro is None:continue
            f=dict(p.features);f.update(micro)
            f.update(self.extra.at(p.symbol,p.observed_time_ns,int(p.side.value),unit_bps))"""
assert s.count(old)==1;s=s.replace(old,new)
old='    def __init__(self,model,calibration=None):self.model=model;self.calibration=calibration'
new=old+';self.columns=FEATURES'
assert s.count(old)==1;s=s.replace(old,new)
old='x=np.array([[p.features[k] for k in FEATURES] for p in plans],dtype=float)'
assert s.count(old)==1;s=s.replace(old,'x=np.array([[p.features[k] for k in self.columns] for p in plans],dtype=float)')
s=s.replace('max_leaf_nodes=15,max_depth=4,min_samples_leaf=80','max_leaf_nodes=7,max_depth=3,min_samples_leaf=40')
s=s.replace('if len(calibration)>=150 and','if len(calibration)>=50 and')
old='def main():\n'
assert s.count(old)==1;s=s.replace(old,old+'    global FEATURES\n')
old="    request=json.loads((HERE/'request.json').read_text())"
assert s.count(old)==1;s=s.replace(old,old+"\n    FEATURES=tuple(request.get('features',FEATURES))")
p.write_text(s)
features=['acceptance','source_scale','source_strength','context_15','context_60','risk_bps','cost_r','planned_rr',
          'attack_progress','response_progress','retracement_efficiency','x_spot_flow_15','x_relative_move_15','x_oi_change_15','x_premium',
          'm_opponent_markout_1','m_own_markout_1','m_trapped_opponent_1','m_trapped_self_1','m_extreme_flow_1','m_extreme_share_1',
          'm_late_flow_1','m_late_progress_1','m_flow_price_alignment_1',
          'm_opponent_markout_5','m_own_markout_5','m_trapped_opponent_5','m_trapped_self_5','m_extreme_flow_5','m_extreme_share_5',
          'm_late_flow_5','m_late_progress_5','m_flow_price_alignment_5']
r={'months':['2024-08','2025-08','2025-11'],'train_end':'2025-08-13','calibration_end':'2025-08-14','features':features,
   'experiments':[{'name':'v7_flow_raw_aug14_17','month':'2025-08','start':'2025-08-14','end':'2025-08-17','raw':True},
                  {'name':'v7_flow_learned_aug14_17','month':'2025-08','start':'2025-08-14','end':'2025-08-17'},
                  {'name':'v7_flow_learned_nov17_24','month':'2025-11','start':'2025-11-17','end':'2025-11-24'}]}
(here/'request.json').write_text(json.dumps(r,indent=2)+'\n')
Path(__file__).unlink()
