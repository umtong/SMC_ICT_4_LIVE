from pathlib import Path
import csv,json
here=Path(__file__).resolve().parent
p=here/'research.py';s=p.read_text()
old="1 if p.side.value=='LONG' else -1"
assert s.count(old)==2
s=s.replace(old,'int(p.side.value)')
old="raise AssertionError('account NAV and actual fills/funding differ')"
new="raise AssertionError(f'account residual={nav-starting_nav-trades.pnl.sum():.12f}; wallet={nav}; attributed={trades.pnl.sum()}; funding={summary[\"funding_cash\"]}')"
assert s.count(old)==1;s=s.replace(old,new)
p.write_text(s)
out=Path('research_results/candidate_ml_easychart_astra3/v1_raw_aug16_24')
trades=list(csv.DictReader((out/'trades.csv').open()))
meta=json.loads((out/'summary.json').read_text())
residual=meta['final_nav']-meta['initial_nav']-sum(float(t['pnl']) for t in trades)
transitions=[]
for a,b in zip(trades,trades[1:]):
    delta=float(b['nav_before'])-float(a['nav_before'])-float(a['pnl'])
    if abs(delta)>.01:transitions.append({'plan':a['plan_id'],'pnl':a['pnl'],'funding':a['funding'],'closed':a['closed'],'next_opened':b['opened'],'difference':delta})
(out/'account_residual.json').write_text(json.dumps({'residual':residual,'transitions':transitions},indent=2))
print('ACCOUNT_RESIDUAL',residual,transitions)
Path(__file__).unlink()
