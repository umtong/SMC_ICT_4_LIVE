from pathlib import Path
import json
here=Path(__file__).resolve().parent
p=here/'policy.py'
s=p.read_text()
old='sources=[z for tf in (15,60) for z in self.frames[tf].levels]+self.channel_levels'
assert s.count(old)==1
s=s.replace(old,'sources=[z for tf in (5,15,60) for z in self.frames[tf].levels]+self.channel_levels')
old="self.zones.append(dict(key=f'{kind}:{tf}:{b.ts}:{side}',side=side,tf=tf,born=b.ts,low=low,high=high,"
assert s.count(old)==1
s=s.replace(old,"self.zones.append(dict(key=f'{kind}:{tf}:{b.ts}:{side}',side=side,tf=tf,born=b.ts,impulse_ts=p.ts if kind=='FVG' else b.ts,low=low,high=high,")
old='        self.zones=self.zones[-128:]'
assert s.count(old)==1
s=s.replace(old,"        self.zones=[z for tf in (5,15,60) for z in [v for v in self.zones if v['tf']==tf and v['alive']][-32:]]")
a=s.index('    def _new_origin(self,x):')
b=s.index('    def observe(self,b,market):',a)
s=s[:a]+'''    def _new_origin(self,x):
        born=[z for z in self.zones if z['tf']==5 and z['born']==x.ts]
        for side in (-1,1):
            footprints=[z for z in born if z['side']==side]
            if not footprints:continue
            candidates=[(c,z) for c in self.recent for z in footprints
                        if z['impulse_ts']>=c.started and side==-c.level.kind
                        and side*(x.close-c.level.price)>0 and c.key in self.contexts
                        and self.contexts[c.key]['alive']]
            if not candidates:continue
            c,z=max(candidates,key=lambda q:(q[0].started,q[1]['low'] if side>0 else -q[1]['high']))
            low,high=z['low'],z['high']
            stop=min(z['stop'],c.low-self.tick) if side>0 else max(z['stop'],c.high+self.tick)
            if side*(x.close-(high if side>0 else low))<=0:continue
            self.origins[side]=Origin(c.key,c,side,x.ts,low,high,stop,x.high,x.low,
                                      self.destination(side,x.close,x.ts),len(footprints))
            self.stats['liquidity_displacement_origin']+=1
''' +s[b:]
p.write_text(s)
p=here/'request.json'
r=json.loads(p.read_text())
for job in r['experiments']:job['name']=job['name'].replace('v2_context_','v3_context_')
p.write_text(json.dumps(r,indent=2)+'\n')
Path(__file__).unlink()
