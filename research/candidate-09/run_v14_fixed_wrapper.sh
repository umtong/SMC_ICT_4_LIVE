#!/usr/bin/env bash
set -euo pipefail

root=research/candidate-09

# Correct one idempotency guard in the generated v14 account-exhaustion patch.
# The gate report already contains account_exhaustion_signals, so checking for the
# token globally prevented the same fields from being added to the long report.
python - <<'PY'
from pathlib import Path

path = Path('research/candidate-09/apply_v14_account_exhaustion_fix.py')
text = path.read_text(encoding='utf-8')
old = '''if '        "account_exhaustion_signals": account_exhaustion,\\n' not in run:
    if anchor not in run:
        raise SystemExit("long result anchor not found")
    run = run.replace(anchor, anchor + addition, 1)
'''
new = '''if anchor + addition not in run:
    if anchor not in run:
        raise SystemExit("long result anchor not found")
    run = run.replace(anchor, anchor + addition, 1)
'''
if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise SystemExit('account-exhaustion long-report guard not found')
path.write_text(text, encoding='utf-8')
PY

exec bash "$root/run_v14_fixed_ci.sh"
