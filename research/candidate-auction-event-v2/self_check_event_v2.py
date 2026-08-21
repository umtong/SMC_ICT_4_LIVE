from __future__ import annotations
import numpy as np,pandas as pd
from event_episode_harvest import _detect_at
idx=pd.date_range('2025-01-01',periods=5,freq='min',tz='UTC')
base={
 'open':[99,100,101,102,101], 'high':[100,101,103,104,102], 'low':[98,99,100,100,99], 'close':[99,100,102,101.5,100],
 'prior_high_60':[101]*5,'prior_low_60':[95]*5,'prior_high_240':[110]*5,'prior_low_240':[90]*5,
 'p5_pivot_high':[101]*5,'p5_pivot_low':[95]*5,'p15_pivot_high':[110]*5,'p15_pivot_low':[90]*5,'p60_pivot_high':[120]*5,'p60_pivot_low':[80]*5,
}
f=pd.DataFrame(base,index=idx)
# Bar 2 closes above 101; bar 3 holds -> accepted long. Bar 4 sweeps above 101 and closes back inside -> reclaim short.
a=_detect_at(f,3,.1);b=_detect_at(f,4,.1)
assert any(x['style']=='ACCEPTED_BREAK' and x['side']=='LONG' for x in a),a
assert any(x['style']=='RECLAIM' and x['side']=='SHORT' for x in b),b
print({'accepted':len(a),'reclaim':len(b),'status':'ok'})
