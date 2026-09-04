from pathlib import Path
import json
here=Path(__file__).resolve().parent
p=here/'policy.py';s=p.read_text()
s=s.replace("    + ['body'", "    + ['context_scale','context_age','source_strength','body'")
s=s.replace('        self.bases=[]; self.last_channel_keys=set(); self.channel_levels=[]',
'''        self.bases=[]; self.last_channel_keys=set(); self.channel_levels=[]
        self.zones=[]; self.contexts={}; self.completed_reclaims=set()''')
a=s.index('    def targets(self,side,entry,ts):');b=s.index('    def destination(',a)
s=s[:a]+'''    def targets(self,side,entry,ts):
        # The first opposing structure includes the execution-scale swing and
        # opposing footprints; it is not just a remote hourly liquidity pool.
        levels=[z.price for tf in (5,15,60) for z in self.frames[tf].levels
                if not z.consumed and z.kind==side and z.born<ts and side*(z.price-entry)>self.tick]
        for z in self.zones:
            price=z['low'] if side>0 else z['high']
            if z['alive'] and z['side']==-side and z['born']<ts and side*(price-entry)>self.tick:levels.append(price)
        return levels
    def _new_zones(self,tf):
        f=self.frames[tf].bars
        if len(f)<3:return
        a,p,b=f[-3:]
        typical=max(float(np.median([abs(v.close-v.open) for v in f[-48:]])),self.tick)
        for side in (-1,1):
            engulf=(side*(p.close-p.open)<0 and side*(b.close-p.open)>0
                    and side*(b.close-b.open)>=2*abs(p.close-p.open)
                    and abs(p.close-p.open)>.1*typical)
            gap=(b.low>a.high if side>0 else b.high<a.low)
            gap=gap and side*(p.close-p.open)>=2*max(abs(a.close-a.open),abs(b.close-b.open),self.tick)
            for kind,exists in [('OB',engulf),('FVG',gap)]:
                if not exists:continue
                low,high=sorted((p.open,p.close)) if kind=='OB' else ((a.high,b.low) if side>0 else (b.high,a.low))
                bars=(p,b) if kind=='OB' else (a,p,b)
                self.zones.append(dict(key=f'{kind}:{tf}:{b.ts}:{side}',side=side,tf=tf,born=b.ts,low=low,high=high,
                     stop=min(v.low for v in bars)-self.tick if side>0 else max(v.high for v in bars)+self.tick,
                     extreme=b.high if side>0 else b.low,first_test=0,alive=True))
        self.zones=self.zones[-128:]
    def _update_zones(self,b):
        for z in self.zones:
            if not z['alive'] or b.ts<=z['born']:continue
            side=z['side']
            if b.low<=z['stop'] if side>0 else b.high>=z['stop']:
                z['alive']=False;continue
            touch=b.low<=z['high'] and b.high>=z['low']
            if not z['first_test']:
                if touch:z['first_test']=b.ts
                else:z['extreme']=max(z['extreme'],b.high) if side>0 else min(z['extreme'],b.low)
            elif b.high>=z['extreme'] if side>0 else b.low<=z['extreme']:
                # The footprint's first return has completed its wave.
                z['alive']=False
    def _context(self,c):
        candidates=[z for z in self.zones if z['alive'] and z['tf']>=15 and z['born']<c.started
                    and z['side']==-c.level.kind and c.low<=z['high'] and c.high>=z['low']]
        return max(candidates,key=lambda z:(z['tf'],z['born'])) if candidates else None
''' +s[b:]
old="        out.update(body=side*(b.close-b.open)/rng,wick="
new="""        z=self.contexts.get(c.key)
        out.update(context_scale=math.log2(z['tf']/5) if z else 0.,
                   context_age=math.log1p((b.ts-z['born'])/(z['tf']*MINUTE)) if z else 0.,
                   source_strength=c.level.strength,
                   body=side*(b.close-b.open)/rng,wick="""
assert s.count(old)==1;s=s.replace(old,new)
old="            self.pending[kind]=c; self.recent.append(c); self.stats['boundary_challenge']+=1"
new="""            z=self._context(c)
            if z is None:
                self.stats['no_prior_directional_footprint']+=1;continue
            c.key=z['key']
            c.started=z['first_test'] or c.started
            self.contexts[c.key]=z
            self.pending[kind]=c;self.recent.append(c);self.stats['boundary_challenge']+=1"""
assert s.count(old)==1;s=s.replace(old,new)
old='''            response=b.close>prev.high if side>0 else b.close<prev.low
            if inside and response and not c.emitted:
                c.emitted=True
'''
new='''            if b.ts//MINUTE%5 or not self.five:continue
            prior=self.five[-1]
            response=(b.close>prior.high if side>0 else b.close<prior.low)
            z=self.contexts.get(c.key)
            if not z or not z['alive']:del self.pending[kind];continue
            if inside and response and not c.emitted and c.key not in self.completed_reclaims:
                c.emitted=True;self.completed_reclaims.add(c.key)
                # A five-minute transfer must invalidate at the full formation,
                # not an artificially tight one-minute noise extreme.
                c.low=min(c.low,prior.low);c.high=max(c.high,prior.high)
'''
assert s.count(old)==1;s=s.replace(old,new)
old="        candidates=[c for c in self.recent if x.ts-c.started<=3*c.level.tf*MINUTE and side*(x.close-c.level.price)>0]"
new="""        candidates=[c for c in self.recent if x.ts-c.started<=3*c.level.tf*MINUTE
                    and side==-c.level.kind and side*(x.close-c.level.price)>0
                    and c.key in self.contexts and self.contexts[c.key]['alive']]"""
assert s.count(old)==1;s=s.replace(old,new)
old="        if side*(b.close-(high if side>0 else low))<=0:return"
new="""        stop=min(stop,c.low-self.tick) if side>0 else max(stop,c.high+self.tick)
        if side*(b.close-(high if side>0 else low))<=0:return"""
assert s.count(old)==1;s=s.replace(old,new)
old='''        plans=[]
        if len(self.history)>240:
'''
new='''        plans=[]
        self._update_zones(b)
        if len(self.history)>240:
'''
assert s.count(old)==1;s=s.replace(old,new)
old='''            if x is None:continue
            if tf==5:self._new_origin(x)
'''
new='''            if x is None:continue
            self._new_zones(tf)
            if tf==5:self._new_origin(x)
'''
assert s.count(old)==1;s=s.replace(old,new)
p.write_text(s)
p=here/'request.json';r=json.loads(p.read_text())
for job in r['experiments']:job['name']=job['name'].replace('v1_','v2_context_')
p.write_text(json.dumps(r,indent=2)+'\n')
Path(__file__).unlink()
