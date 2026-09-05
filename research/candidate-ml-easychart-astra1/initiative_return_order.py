"""The same initiative-defeat policy with a precommitted first-return order.

v16 confused two distinct roles: it demanded higher-timeframe liquidity both as
context AND as every entry's event, then waited for another one-minute breakout
after direction had already changed. The resulting entry was generally too
late for the whole-event stop and nearest objective.

Here a local adverse liquidity challenge is meaningful inside an already known
higher directional context. Paired displacement still earns direction. Its
reclaimed price zone is the entry location, not a request for a second breakout.
Entry, whole-event stop, first-wave/opposing target, and pending cancellation
price are all frozen before submitting a passive first-return order.

No net-R target cap, better-priced assumed market fill, outcome-based candidate
filter or trade quota is used. Actual limit fill and stop/target execution are
owned by the existing Nautilus passive-order implementation.
"""
import math
from astra_policy import Plan,MINUTE
from domain import Side
from paired_initiative_reclaim import ReclaimMarket,PairedInitiativePolicy,Challenge,FEATURES

class FirstReturnMarket(ReclaimMarket):
    def _observe_liquidity(self,b):
        candidates={1:[],-1:[]}
        for tf in (5,15,60,240):
            for p in self.books[tf].pivots[-24:]:
                if p.observed_time_ns>=b.ts or p.pivot_id in self.touched:continue
                hit=b.high>p.price+self.tick if p.side=='HIGH' else b.low<p.price-self.tick
                if not hit:continue
                self.touched[p.pivot_id]=b.ts;s=-1 if p.side=='HIGH' else 1
                location=any(z.live and z.side==s and z.observed<b.ts and z.tf>=60
                    and b.low<=z.high and b.high>=z.low for z in self.zones)
                # The location of a countertrend reversal must have higher
                # authority; a local turn alone cannot defeat higher direction.
                if tf==5 and self.bias!=s and not location:continue
                candidates[s].append((p,tf,location))
        for s,values in candidates.items():
            if not values:continue
            p,tf,location=max(values,key=lambda x:(x[1],x[0].strength_ratio))
            active=self.challenges.get(s)
            if active is not None and not active.claimed and s*(b.close-active.level)<=0:
                active.extreme=min(active.extreme,b.low) if s>0 else max(active.extreme,b.high)
                active.context_location|=location
                if tf>active.scale:active.level=p.price;active.scale=tf;active.strength=p.strength_ratio
                continue
            self.challenges[s]=Challenge(f'{self.symbol}:LIQUIDITY:{p.pivot_id}:{b.ts}',s,b.ts,p.price,tf,
                p.strength_ratio,b.low if s>0 else b.high,self.bias==s,location)
            self.stats['contextual_liquidity_challenged']+=1

    def observe(self,b):
        super().observe(b)
        output=[]
        for s,r in self.reclaims.items():
            if r.observed!=b.ts or r.consumed:continue
            r.consumed=True
            entry=(r.low+r.high)/2
            if s*(b.close-entry)<=self.tick:continue
            risk=s*(entry-r.stop)
            objectives=[(r.peak,'FIRST_RECLAIM_WAVE')];opposing_inside=False
            for z in self.zones:
                if not z.live or z.side!=-s:continue
                if z.low<=entry<=z.high:opposing_inside=True
                objectives.append((z.low if s>0 else z.high,'OPPOSING_HIGHER_FOOTPRINT'))
            for tf in (15,60,240):
                for p in self.books[tf].pivots[-24:]:
                    if p.pivot_id not in self.touched and p.observed_time_ns<b.ts and p.side==('HIGH' if s>0 else 'LOW'):
                        objectives.append((p.price,f'{tf}M_OPPOSING_LIQUIDITY'))
            objectives=[(p,k) for p,k in objectives if s*(p-entry)>self.tick]
            if risk<=self.tick or opposing_inside or not objectives:
                self.stats['preentry_opposing_structure']+=1;continue
            target,kind=min(objectives,key=lambda x:s*(x[0]-entry));target-=s*self.tick
            rr=s*(target-entry)/risk
            if rr<1:
                self.stats['preentry_first_wave_no_room']+=1
                self.explanations.append({'ts':b.ts,'symbol':self.symbol,'reason':'preentry_first_wave_no_room',
                    'entry':entry,'stop':r.stop,'target':target,'rr':rr,'event':r.key})
                continue
            f=dict(r.features);f.update(return_age=0.,return_flow=0.,return_activity=0.,
                risk_bps=risk/entry*10000,risk_range=risk/r.unit,cost_r=.0008*entry/risk,planned_rr=rr,
                pending_cancel_price=r.peak)
            self.stats['passive_plans']+=1
            output.append(Plan(r.key+f':LIMIT:{b.ts}',r.key,self.symbol,Side.LONG if s>0 else Side.SHORT,
                b.ts,r.started,entry,r.stop,target,rr,(r.low+r.high)/2,5,r.key,kind,r.low,r.high,
                max(entry,r.peak),min(entry,r.peak),f,family='PAIRED_INITIATIVE_RECLAIM'))
        return output

class InitiativeReturnPolicy(PairedInitiativePolicy):
    def __init__(self,ticks):self.markets={s:FirstReturnMarket(s,t) for s,t in ticks.items()}
