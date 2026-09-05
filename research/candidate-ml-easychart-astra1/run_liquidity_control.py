"""Execute a source-derived liquidity mechanism through the existing account."""
from pathlib import Path
import hashlib,importlib,json,traceback
import run_control_v3
import run_control_v2 as base

request=json.loads((base.HERE/'control_request.json').read_text())
module=importlib.import_module(request.get('policy_module','reclaimed_liquidity'))
base.ControlPolicy=getattr(module,request.get('policy_class','ReclaimedLiquidityPolicy'))
base.FEATURES=module.FEATURES
base.OUT=Path('research_results')/request.get('output','astra1_control_v5')
base.OUT.mkdir(parents=True,exist_ok=True)
names=('liquidity_control.py','reclaimed_liquidity.py','local_response.py',
       'auction_control_survival.py',Path(module.__file__).name)
source=b''.join((base.HERE/f).read_bytes() for f in dict.fromkeys(names))
base.CACHE=Path('astra_control_cache')/('liquidity-'+hashlib.sha256(source).hexdigest()[:20])
base.CACHE.mkdir(parents=True,exist_ok=True)

if __name__=='__main__':
    try:
        method=request.get('entry_method')
        experiments={'pressure':'pressure_model_experiment',
                     'inventory_direction':'inventory_direction_experiment',
                     'chart_sequence':'chart_sequence_clock'}
        experiment=request.get('experiment')
        if experiment in experiments:
            importlib.import_module(experiments[experiment]).execute(base,request)
        elif method=='passive':
            from passive_experiment import execute
            execute(base,request)
        elif method=='micro':
            from micro_experiment import execute
            execute(base,request)
        else:
            if all(job.get('learned',True) is False for job in request['experiments']):
                base.fit=lambda *args:None
            elif request.get('model_module'):
                learned=importlib.import_module(request['model_module'])
                base.fit=lambda labels,train_end,cal_end:learned.fit(base,labels,train_end,cal_end)
            base.main()
    except Exception:
        (base.OUT/'error.txt').write_text(traceback.format_exc());raise
