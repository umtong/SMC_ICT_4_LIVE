"""New liquidity/control mechanism, unchanged three-percent account execution."""
from pathlib import Path
import hashlib,traceback
import run_control_v3
import run_control_v2 as base
from liquidity_control import LiquidityPolicy,FEATURES

base.ControlPolicy=LiquidityPolicy
base.FEATURES=FEATURES
base.OUT=Path('research_results/astra1_control_v4');base.OUT.mkdir(parents=True,exist_ok=True)
source=(base.HERE/'liquidity_control.py').read_bytes()
base.CACHE=Path('astra_control_cache')/('liquidity-'+hashlib.sha256(source).hexdigest()[:20])
base.CACHE.mkdir(parents=True,exist_ok=True)

if __name__=='__main__':
    try:base.main()
    except Exception:
        (base.OUT/'error.txt').write_text(traceback.format_exc());raise
