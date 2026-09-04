from pathlib import Path
here=Path(__file__).resolve().parent
p=here/'research.py'
s=p.read_text()
old='    def plans(self):\n'
new='''    def feature_mark_at(self,s,t):
        # Missing explanatory data stays missing. The learner supports NaN.
        # NAV/funding retain mark_at's strict actual-observation requirement.
        stamps,prices=self.mark_arrays[s]
        i=np.searchsorted(stamps,t,side='right')-1
        return float(prices[i]) if i>=0 and t-stamps[i]<=MINUTE else float('nan')
    def plans(self):
'''
assert s.count(old)==1
s=s.replace(old,new)
old='policy=LiquidityPolicy(self.ticks,self.mark_at)'
assert s.count(old)==1
s=s.replace(old,'policy=LiquidityPolicy(self.ticks,self.feature_mark_at)')
p.write_text(s)
Path(__file__).unlink()
