"""Parallel channels proposed by three alternating, confirmed wick pivots.
Sources: EasyChart channel chapter. Confirmation lag and minimum geometric
resolution below are research choices, not rules claimed by the source.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from market_io import aggregate
MINUTE=60000000000

@dataclass
class Channel:
    born:int
    scale:int
    source:int
    main:int
    anchor:int
    intercept:float
    slope:float
    width:float
    travel:int
    atr:float
    touched:int=-1
    accepted:int=-1
    retest:int=-1
    low:float=float('inf')
    high:float=-float('inf')
    test_low:float=float('inf')
    test_high:float=-float('inf')
    def edges(self,t):
        price=self.intercept+self.slope*(t-self.anchor)/MINUTE
        return (price-self.width,price) if self.main==1 else (price,price+self.width)


def confirmed_pivots(b):
    h=b.high.to_numpy(); l=b.low.to_numpy(); ts=b.index.asi8; events={}
    for j in range(2,len(b)-2):
        kind=0
        if h[j]>max(h[j-2:j]) and h[j]>=max(h[j+1:j+3]): kind=1
        if l[j]<min(l[j-2:j]) and l[j]<=min(l[j+1:j+3]):
            if kind: continue
            kind=-1
        if kind: events[int(ts[j+2])]=(int(ts[j]),float(h[j] if kind==1 else l[j]),kind)
    return events


def geometries(d,scales=(15,60)):
    events={}
    for scale in scales:
        b=aggregate(d,scale); piv=confirmed_pivots(b); hist=[]
        atr=(b.high-b.low).ewm(span=max(8,240//scale),adjust=False).mean().shift()
        for t,p in piv.items():
            if hist and p[2]==hist[-1][2]:
                if p[2]*(p[1]-hist[-1][1])>0: hist[-1]=p
                else: continue
            else: hist.append(p)
            if len(hist)<3: continue
            p1,p2,p3=hist[-3:]
            if p3[0]-p1[0]<4*scale*MINUTE: continue
            slope=(p3[1]-p1[1])/((p3[0]-p1[0])/MINUTE)
            at2=p1[1]+slope*(p2[0]-p1[0])/MINUTE
            width=p1[2]*(at2-p2[1]); a=float(atr.loc[pd.Timestamp(t,tz='UTC')])
            if not np.isfinite(a) or width<2*a: continue
            ch=Channel(t,scale,p3[0],p3[2],p1[0],p1[1],slope,width,int((p3[0]-p2[0])/MINUTE),a)
            events.setdefault(t,[]).append(ch)
    return events
