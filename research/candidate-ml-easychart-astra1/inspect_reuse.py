"""Small research console for inspecting already-completed experiments, not a scoring system."""
from pathlib import Path
import ast,json,hashlib
ROOT=Path('bundle')
m=json.loads((ROOT/'inputs.json').read_text())
print('MARKET ERRORS', [x for x in m['market'] if 'error' in x])
for ref,meta in m['branches'].items():
    print('\nBRANCH',ref,meta)
    base=ROOT/'branches'/ref
    files=[]
    for p in base.rglob('*.json'):
        if not any(x in str(p).lower() for x in ['result','summary','diagnos','metric','report']):continue
        if p.stat().st_size>200000:continue
        try:
            d=json.loads(p.read_text())
        except Exception:continue
        records=[]
        def walk(x,path=''):
            if isinstance(x,dict):
                row={k:v for k,v in x.items() if any(t in k.lower() for t in ['win_rate','expectancy','profit_factor','mean_net','trades_per_day','nav_multiple','total_trades','closed_trades','return_pct','max_drawdown']) and isinstance(v,(int,float))}
                if row:records.append((path,row))
                for k,v in x.items():
                    if isinstance(v,(dict,list)):walk(v,path+'/'+str(k))
            elif isinstance(x,list):
                for i,v in enumerate(x[:30]):walk(v,path+'/'+str(i))
        walk(d)
        if records:files.append((str(p.relative_to(base)),records))
    # Repeated inherited reports have the same digest across branches; show only distinct ones.
    for name,rows in files:
        p=base/name;digest=hashlib.sha256(p.read_bytes()).hexdigest()
        if digest in globals().setdefault('seen',set()):continue
        seen.add(digest)
        print('RESULT',name,json.dumps(rows[:12],ensure_ascii=False))
    for p in list(base.rglob('README.md')):
        s=str(p)
        if any(x in s for x in ['candidate-ml-','candidate-4-','candidate-3b','ml3_breakthrough','causal-alpha','inventory']):
            print('NOTE',str(p.relative_to(base)),p.read_text()[:7000])
base=ROOT/'branches/research_candidate_ML_easychart_c'
for name in ['mtf_strategy_v5.py','mtf_strategy.py','instruments.py','domain.py','mtf_data_re1_flow.py','mtf_data.py','fee_profiles_v5.py']:
    found=list(base.rglob(name))
    print('\nSOURCE',name, [str(x.relative_to(base)) for x in found])
    if found:
        p=found[0];text=p.read_text()
        try:
            tree=ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node,(ast.FunctionDef,ast.ClassDef)):
                    print('DEF',node.name,node.lineno,node.end_lineno)
            wanted=['_quantity','_current_nav','on_start','on_bar','_flush_bar_bucket','_candle','make_instrument','make_instrument_with_fee_profile','add_symbol_mtf_flow_data','Candle','EasyChartMTFConfig']
            for node in ast.walk(tree):
                if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in wanted:
                    print('\n'.join(text.splitlines()[node.lineno-1:node.end_lineno]))
            if name in ['instruments.py','fee_profiles_v5.py']:print(text[:15000])
        except Exception as e:print(repr(e))
