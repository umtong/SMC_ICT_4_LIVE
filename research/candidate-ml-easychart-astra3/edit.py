from pathlib import Path
import json
here=Path(__file__).resolve().parent
p=here/'policy.py';s=p.read_text()
old="            c.started=z['first_test'] or c.started\n"
assert s.count(old)==1;s=s.replace(old,'')
old='Side.LONG if side>0 else Side.SHORT,b.ts,c.started,b.close,stop,target,reward/risk,'
new="Side.LONG if side>0 else Side.SHORT,b.ts,(self.contexts.get(c.key,{}).get('first_test') or c.started),b.close,stop,target,reward/risk,"
assert s.count(old)==1;s=s.replace(old,new)
old="extreme=b.high if side>0 else b.low,first_test=0,alive=True))"
new="extreme=b.high if side>0 else b.low,first_test=0,alive=True,invalidated=False))"
assert s.count(old)==1;s=s.replace(old,new)
old="                z['alive']=False;continue\n"
new="                z['alive']=False;z['invalidated']=True;continue\n"
assert s.count(old)==1;s=s.replace(old,new)
old="                        and self.contexts[c.key]['alive']]"
new="                        and not self.contexts[c.key]['invalidated']\n                        and x.ts-c.started<=3*c.level.tf*MINUTE]"
assert s.count(old)==1;s=s.replace(old,new)
a=s.index('    def _origin_returns(self,b,prev,market):');b=s.index('    def _new_origin(self,x):',a)
s=s[:a]+'''    def _origin_returns(self,b,prev,market):
        plans=[]
        for side,o in list(self.origins.items()):
            if b.ts<=o.born:continue
            if (b.low<=o.stop if side>0 else b.high>=o.stop):
                self.stats['origin_invalidated']+=1;del self.origins[side];continue
            if not o.returned:
                touch=b.low<=o.high if side>0 else b.high>=o.low
                if not touch:
                    o.departure_high=max(o.departure_high,b.high)
                    o.departure_low=min(o.departure_low,b.low)
                    continue
                o.returned=True;o.return_time=b.ts;self.stats['origin_first_return']+=1
                # A previously collected pool is no longer an obstacle. The
                # completed departure leg supplies a known return-wave target.
                peak=o.departure_high if side>0 else o.departure_low
                ahead=[v for v in self.targets(side,b.close,b.ts)+[peak] if side*(v-b.close)>self.tick]
                o.destination=min(ahead,key=lambda v:side*(v-b.close)) if ahead else None
            elif (b.high>=o.departure_high if side>0 else b.low<=o.departure_low):
                self.stats['return_wave_completed_without_entry']+=1;del self.origins[side];continue
            o.return_volume+=b.volume;o.return_count+=1
            response=b.close>prev.high if side>0 else b.close<prev.low
            if not response:continue
            target=o.destination
            if target is not None and side*(target-b.close)>self.tick:
                p=self._plan(o.parent,b,side,o.stop,target,market,o)
                if p is not None:plans.append(p);self._explain(o.parent,'origin_plan_emitted',b.ts)
            del self.origins[side]
        return plans
''' +s[b:]
p.write_text(s)
p=here/'request.json';r=json.loads(p.read_text())
for job in r['experiments']:job['name']=job['name'].replace('v3_context_','v4_control_')
p.write_text(json.dumps(r,indent=2)+'\n')
Path(__file__).unlink()
