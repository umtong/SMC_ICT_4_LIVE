"""Fresh-liquidity raw diagnosis after separating role failure from trade stops."""
from pathlib import Path
import hashlib,json,traceback
import run_control_v3
import run_control_v2 as base
from reclaimed_liquidity import ReclaimedLiquidityPolicy,FEATURES

base.ControlPolicy=ReclaimedLiquidityPolicy
base.FEATURES=FEATURES
base.OUT=Path('research_results/astra1_control_v5');base.OUT.mkdir(parents=True,exist_ok=True)
source=b''.join((base.HERE/f).read_bytes() for f in ('liquidity_control.py','reclaimed_liquidity.py'))
base.CACHE=Path('astra_control_cache')/('liquidity-'+hashlib.sha256(source).hexdigest()[:20])
base.CACHE.mkdir(parents=True,exist_ok=True)

if __name__=='__main__':
    try:
        request=json.loads((base.HERE/'control_request.json').read_text())
        if all(job.get('learned',True) is False for job in request['experiments']):
            # No pretend training of a useful classifier on a handful of labels.
            base.fit=lambda *args:None
        base.main()
    except Exception:
        (base.OUT/'error.txt').write_text(traceback.format_exc());raise
