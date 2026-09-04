"""Causal, already-observed executed-flow response from the Astra2 collector.

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
    def raw_at(self,symbol,ts):
        if symbol not in self.tables:return None
        stamps,one,_=self.tables[symbol]
        i=np.searchsorted(stamps,ts,side='right')-1
        return one.iloc[i] if i>=0 and stamps[i]==ts else None
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
