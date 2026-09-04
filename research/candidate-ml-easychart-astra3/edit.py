from pathlib import Path
import json
here=Path(__file__).resolve().parent
p=here/'hierarchy_policy.py'
s=p.read_text()
s=s.replace('    returns: list\n','    returns: list\n    departing_pivot: int=0\n')
old='''        if side*(extreme-c.peak)>0:
            c.peak=extreme;c.peak_ts=b.ts
'''
new='''        if side*(extreme-c.peak)>0:
            c.peak=extreme;c.peak_ts=b.ts
            # Completing the prior initiative leg ends that corrective auction.
            # A new opposite leg, not another entry ID, creates a new episode.
            if c.episode and side*(extreme-c.episode_peak)>0:
                self._finish()
                c.episode=0;c.episode_start=0
'''
assert s.count(old)==1;s=s.replace(old,new)
old='''        if self.controller is not None:
            self.controller.episode=0;self.controller.episode_start=0
'''
assert s.count(old)==1;s=s.replace(old,'')
old='''        if b.ts<=o.born:return []
        if b.low<=o.stop if side>0 else b.high>=o.stop:
'''
new='''        if b.ts<=o.born:return []
        frame=self.frames[5]
        after=[z for z in frame.pivots if z.pivot_time>o.born]
        peaks=[z for z in after if z.kind==side]
        if peaks:
            first=min(peaks,key=lambda z:z.pivot_time)
            turns=[z for z in after if z.kind==-side and z.pivot_time>first.pivot_time]
            if turns:
                # A confirmed corrective trough followed by a new initiative
                # wave outside the old entry zone means its first return was
                # completed elsewhere. Do not revisit it hours later.
                self.stats['first_return_completed_elsewhere']+=1
                self._finish();return []
        if b.low<=o.stop if side>0 else b.high>=o.stop:
'''
assert s.count(old)==1;s=s.replace(old,new)
old='''        if not o.low<=b.close<=o.high:return []
        peak=o.departure_high if side>0 else o.departure_low
'''
new='''        if not o.low<=b.close<=o.high:return []
        prior=self.history[-2]
        # Location alone is not direction. The first local response must now
        # recover the prior completed minute's extreme while still at the
        # preselected defense; do not chase when it is already beyond it.
        if not (b.close>prior.high if side>0 else b.close<prior.low):return []
        peak=o.departure_high if side>0 else o.departure_low
'''
assert s.count(old)==1;s=s.replace(old,new)
old='''        if 15 in closed:self._control(closed[15])
'''
new='''        if 15 in closed:
            x=closed[15];c=self.controller
            if c is not None:
                # Breaking the latest protected swing defeats the current
                # structural thesis even before the distant initial extreme.
                protected=[z for z in self.frames[15].pivots if z.kind==-c.side and z.pivot_time>c.born]
                if protected:
                    last=max(protected,key=lambda z:z.pivot_time)
                    if c.side*(x.close-last.price)<0:
                        self.controller=None;self.defense=None
                        self.stats['current_control_defeated']+=1
            self._control(x)
'''
assert s.count(old)==1;s=s.replace(old,new)
p.write_text(s)
p=here/'request.json';r=json.loads(p.read_text())
for job in r['experiments']:job['name']=job['name'].replace('v10_hierarchy_','v11_fresh_response_')
p.write_text(json.dumps(r,indent=2)+'\n')
Path(__file__).unlink()
