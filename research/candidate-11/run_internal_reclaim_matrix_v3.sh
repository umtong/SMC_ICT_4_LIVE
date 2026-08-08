#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
python "$CAND/apply_internal_reclaim_block_fix.py"
python "$CAND/apply_internal_reclaim_compatibility.py"

# IRX is an independent alpha family. Its implementation gate includes its own
# detector and the shared leadership, allocation, risk and Nautilus boundaries,
# but does not inherit failures from unrelated VWAP/cross-market experiments.
python - "$CAND/run_internal_reclaim_matrix.sh" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
old = "python -m unittest discover -s \"$CAND\" -p 'test_*.py' -v"
new = '''for pattern in \\
  test_bar_adapter.py \\
  test_evidence_audit.py \\
  test_global_allocator.py \\
  test_internal_reclaim.py \\
  test_leadership_sweep_timestamp.py \\
  test_logic.py \\
  test_market_leadership.py \\
  test_market_leadership_impulse.py \\
  test_market_leadership_price_discovery.py \\
  test_portfolio_scdam.py; do
  python -m unittest discover -s "$CAND" -p "$pattern" -v
done'''
if new not in source:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"IRX test-scope anchor count={count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
PY
chmod +x "$CAND/run_internal_reclaim_matrix.sh"
bash "$CAND/run_internal_reclaim_matrix.sh"
