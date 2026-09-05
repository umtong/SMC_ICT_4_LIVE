"""Two-sided auction cells, not arbitrary swing-level reversal bets.

The cell exists before the liquidity event: independently confirmed upper and
lower wick turns locate both sides of an affine auction. An excursion is tradable
only on its first return to that auction, after above-normal participation.
A completed context bar outside the cell retires it. Failure to return through
value does not manufacture another trade ID at the same boundary. A new cell
requires newly observed turns on both sides after retirement.

The robust affine fit is a generalized channel interpretation. Its middle is a
pre-entry value objective, not a target chosen to manufacture a fixed reward/R.
One ordinary pre-event minute range beyond the actual excursion extreme allows
for observed intrabar price noise; quantity still risks exactly 3% of NAV.
"""
from collections import Counter, deque
from dataclasses import dataclass, replace
import math
import numpy as np
from scipy.stats import theilslopes
from astra_policy import Observation, Plan, WickMap, MINUTE
from domain import Side
from auction_control_survival import aggregate

FEATURES=('cell_slope','cell_width_bps','cell_age','cell_turns','edge_divergence',
          'event_activity','event_flow','event_duration','penetration','reclaim',
          'approach_efficiency','response_flow','response_activity','risk_range',
          'risk_bps','cost_r','planned_rr','market_move','relative_move')

@dataclass
class Cell:
    key:str;observed:int;origin:int;slope:float;low:float;high:float;turns:int;divergence:float
    def bounds(self,t):
        shift=self.slope*(t-self.origin)/MINUTE
        return self.low+shift,self.high+shift

@dataclass
class Excursion:
    key:str;side:int;started:int;extreme:float;reference:float;noise:float
    volume:float=0.;delta:float=0.;bars:int=0;expected_volume:float=0.

class CellMarket:
    def __init__(self,symbol,tick):
        self.symbol=symbol;self.tick=tick;self.history=[];self.bucket=[];self.fifteen=[]
        self.book=WickMap(symbol,15,tick,pivot_spans=(2,))
        self.cell=None;self.event=None;self.retired=0;self.last_center=0;self.used_center=-1
        self.stats=Counter();self.explanations=[]
        self.volumes=deque(maxlen=60);self.ranges=deque(maxlen=60)

    def _new_cell(self,ts):
        if len(self.fifteen)<32:return
        p=[q for q in self.book.pivots if q.event_time_ns>max(self.retired,ts-8*60*MINUTE)]
        lows=[q for q in p if q.side=='LOW'];highs=[q for q in p if q.side=='HIGH']
        if len(lows)<2 or len(highs)<2:return
        origin=min(q.event_time_ns for q in p)
        def line(a):
            x=np.array([(q.event_time_ns-origin)/MINUTE for q in a]);y=np.array([q.price for q in a])
            slope=float(theilslopes(y,x)[0]);return slope,float(np.median(y-slope*x))
        sl,il=line(lows);sh,ih=line(highs);slope=(sl+sh)/2
        low=float(np.median([q.price-slope*(q.event_time_ns-origin)/MINUTE for q in lows]))
        high=float(np.median([q.price-slope*(q.event_time_ns-origin)/MINUTE for q in highs]))
        width=high-low
        if width<=4*self.tick:return
        span=(ts-origin)/MINUTE
        divergence=(sh-sl)*span/width
        # Intersecting edges are not a persistent two-sided auction.
        if abs(divergence)>=1:return
        c=Cell(f'{self.symbol}:CELL:{p[0].pivot_id}:{p[-1].pivot_id}',ts,origin,slope,low,high,len(p),divergence)
        lo,hi=c.bounds(ts);price=self.history[-1].close
        if not lo<price<hi:return
        self.cell=c;self.event=None;self.last_center=0;self.used_center=-1
        self.stats['two_sided_cell_observed']+=1

    def move(self,n=60):
        if len(self.history)<n:return 0.
        a=self.history[-n:];unit=max(float(np.mean([b.high-b.low for b in a])),self.tick*2)
        return (a[-1].close-a[0].open)/(unit*math.sqrt(n))

    def observe(self,b):
        if self.history and b.ts-self.history[-1].ts!=MINUTE:raise ValueError('non-contiguous observation')
        previous=self.history[-1] if self.history else b
        vbase=max(float(np.mean(self.volumes)) if self.volumes else b.volume,1e-12)
        noise=max(float(np.median(self.ranges)) if self.ranges else b.high-b.low,self.tick)
        self.history.append(b);self.bucket.append(b);self.volumes.append(b.volume);self.ranges.append(b.high-b.low)
        complete=b.ts//MINUTE%15==0
        if complete:
            a=self.bucket;self.bucket=[]
            if len(a)==15:
                candle=aggregate(a);self.fifteen.append(candle);self.book.observe(candle)
        c=self.cell
        if c is None:
            if complete:self._new_cell(b.ts)
            return None
        low,high=c.bounds(b.ts);middle=(low+high)/2;width=high-low
        # Closure outside is acceptance of a different auction, not a wick sweep.
        if complete and (b.close<low or b.close>high):
            self.retired=b.ts;self.cell=None;self.event=None
            self.stats['accepted_outside_cell']+=1;return None
        if b.low<=middle<=b.high:
            self.last_center=b.ts
        e=self.event
        if e is None:
            if not self.last_center or self.last_center<=self.used_center:return None
            lower=b.low<low-self.tick and previous.close>=low
            upper=b.high>high+self.tick and previous.close<=high
            if lower and upper:
                self.used_center=self.last_center;self.stats['ambiguous_whole_cell_bar']+=1;return None
            if not lower and not upper:return None
            s=1 if lower else -1
            e=Excursion(f'{c.key}:EXCURSION:{b.ts}:{s}',s,b.ts,b.low if s>0 else b.high,low if s>0 else high,noise)
            self.event=e;self.stats['outer_liquidity_excursion']+=1
        s=e.side;e.extreme=min(e.extreme,b.low) if s>0 else max(e.extreme,b.high)
        e.volume+=b.volume;e.delta+=b.delta;e.expected_volume+=vbase;e.bars+=1
        boundary=low if s>0 else high
        # First close returning inside is the event's decision; later weak
        # bounces cannot revive it merely because nominal RR has improved.
        if s*(b.close-boundary)<=self.tick:return None
        self.event=None;self.used_center=self.last_center
        if e.volume<e.expected_volume:
            self.stats['ordinary_low_participation_return']+=1;return None
        entry=b.close;stop=e.extreme-s*e.noise
        targets=[(middle,'AUCTION_VALUE')]
        for q in self.book.pivots[-20:]:
            if q.observed_time_ns<c.observed and q.side==('HIGH' if s>0 else 'LOW'):
                if 0<s*(q.price-entry)<s*(middle-entry):targets.append((q.price,'PREEXISTING_OPPOSING_TURN'))
        target,kind=min(targets,key=lambda x:s*(x[0]-entry));target-=s*self.tick
        risk=s*(entry-stop);rr=s*(target-entry)/max(risk,self.tick)
        if risk<=self.tick or rr<1:
            self.stats['first_response_geometry_no_trade']+=1
            self.explanations.append({'ts':b.ts,'symbol':self.symbol,'reason':'first_response_geometry_no_trade',
                'entry':entry,'stop':stop,'target':target,'rr':rr,'cell':c.key})
            return None
        a=self.history[-60:]
        f=dict(cell_slope=s*c.slope*60/width,cell_width_bps=width/entry*10000,
            cell_age=(b.ts-c.observed)/(60*MINUTE),cell_turns=c.turns,edge_divergence=c.divergence,
            event_activity=e.volume/e.expected_volume,event_flow=s*e.delta/max(e.volume,1e-12),
            event_duration=e.bars,penetration=s*(e.reference-e.extreme)/width,
            reclaim=s*(entry-boundary)/width,
            approach_efficiency=s*(previous.close-a[0].open)/max(sum(x.high-x.low for x in a),self.tick),
            response_flow=s*b.delta/max(b.volume,1e-12),response_activity=b.volume/vbase,
            risk_range=risk/width,risk_bps=risk/entry*10000,cost_r=.0012*entry/risk,planned_rr=rr)
        self.stats['plans']+=1
        return Plan(e.key,e.key,self.symbol,Side.LONG if s>0 else Side.SHORT,b.ts,e.started,
                    entry,stop,target,rr,boundary,15,c.key,kind,low,high,high,low,f,family='TWO_SIDED_AUCTION_RETURN')

class AuctionCellPolicy:
    def __init__(self,ticks):self.markets={s:CellMarket(s,t) for s,t in ticks.items()}
    def observe(self,bars):
        if len({b.ts for b in bars.values()})!=1:raise ValueError('unsynchronized clocks')
        candidates=[p for s,b in bars.items() if (p:=self.markets[s].observe(b)) is not None]
        moves={s:m.move() for s,m in self.markets.items()};factor=float(np.median(list(moves.values())))
        result=[]
        for p in candidates:
            f=dict(p.features);side=int(p.side.value)
            f.update(market_move=side*factor,relative_move=side*(moves[p.symbol]-factor))
            result.append(replace(p,features=f))
        return result
