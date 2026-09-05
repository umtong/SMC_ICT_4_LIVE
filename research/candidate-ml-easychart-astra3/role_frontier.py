"""A source-interpretation correction to the existing objective observations.

Trend Line p5 and the ETH 4226.40 trade in the supplied transaction notes name
broken support as an overhead objective. The old target book only accepts
unspent HIGH pivots for a long (LOW for a short). That is not the same concept.

This observer retains the latest accepted role-change frontier on each scale.
A wick does not change a role. A close through a level followed by a completed
bar opening and closing beyond it confirms the change, using the source's
channel-acceptance description as a research translation for horizontal levels.
No R-multiple target, holding limit, new entry rule or future data is used.
"""
from __future__ import annotations
from dataclasses import dataclass,replace
from collections import Counter
from policy import Frame
from auction_reuse_policy import Observation
from astra_policy import MINUTE

@dataclass(slots=True)
class Role:
    level:object
    kind:int
    pending:int=0
    changed:int=0

class RoleBook:
    def __init__(self,tf):
        self.tf=tf;self.frame=Frame(tf);self.roles={};self.frontiers={}
    def observe(self,b):
        bar=self.frame.append(b)
        if bar is None:return
        for z in self.frame.levels:
            if z.key not in self.roles:self.roles[z.key]=Role(z,z.kind)
        changes=[]
        for key,r in self.roles.items():
            price=r.level.price
            if r.level.born>=bar.ts:continue
            if r.pending:
                side=r.pending
                if side*(bar.open-price)>0 and side*(bar.close-price)>0:
                    r.kind=-side;r.changed=bar.ts;changes.append(r)
                r.pending=0
            direction=1 if bar.close>price else -1 if bar.close<price else 0
            if direction and direction==r.kind:r.pending=direction
        for role in (-1,1):
            eligible=[r for r in changes if r.kind==role]
            if eligible:
                # The nearest newly accepted frontier is the active local
                # transfer, rather than a growing collection of obsolete lines.
                self.frontiers[role]=min(eligible,key=lambda r:abs(bar.close-r.level.price))
        live={z.key for z in self.frame.levels}
        self.roles={k:r for k,r in self.roles.items() if k in live}
        self.frontiers={k:r for k,r in self.frontiers.items() if r.level.key in live and r.kind==k}
    def obstacle(self,side,price,now):
        r=self.frontiers.get(side)
        if r is None or r.changed>=now or side*(r.level.price-price)<=0:return None
        return r.level.price,r.changed,r.level.key


def apply_frontiers(tape,plans):
    books={s:{tf:RoleBook(tf) for tf in (5,15,60,240)} for s in tape.symbols}
    arrays={s:d[['ts','open','high','low','close','volume','taker_buy_volume','quote_volume','count','taker_buy_quote_volume']].to_numpy() for s,d in tape.raw.items()}
    at={}
    for p in plans:at.setdefault(p.observed_time_ns,[]).append(p)
    output=[];stats=Counter()
    for i in range(len(next(iter(arrays.values())))):
        for s,a in arrays.items():
            t,o,h,l,c,v,b,q,n,bq=a[i];ts=int(t)
            bar=Observation(ts,o,h,l,c,v,b,q,int(n),bq)
            for book in books[s].values():book.observe(bar)
        for p in at.get(ts,[]):
            side=int(p.side.value);tick=tape.ticks[p.symbol]
            candidates=[book.obstacle(side,p.entry,ts) for book in books[p.symbol].values()]
            candidates=[x for x in candidates if x is not None and side*(x[0]-p.entry)>tick]
            nearer=[x for x in candidates if side*(x[0]-p.target)<0]
            if not nearer:output.append(p);stats['original_objective_retained']+=1;continue
            price,changed,key=min(nearer,key=lambda x:side*(x[0]-p.entry))
            target=price-side*tick;risk=abs(p.entry-p.stop);rr=side*(target-p.entry)/risk
            if rr<1:
                stats['nearer_role_frontier_leaves_insufficient_room']+=1;continue
            f=dict(p.features);f['planned_rr']=rr
            if 'target_range' in f:f['target_range']=f['risk_range']*rr
            output.append(replace(p,target=target,gross_rr=rr,features=f,
                target_kind='PREEXISTING_ACCEPTED_SR_FLIP_FRONTIER'))
            stats['earlier_role_based_objective']+=1
    return output,dict(stats)
