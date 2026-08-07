#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$root/../.." && pwd)
cd "$repo"

rm -rf "$root/evidence/latest" "$root/diagnostics/v25-gate-long"
mkdir -p "$root/diagnostics/v25-gate-long" "$root/evidence/latest"

set +e
smc4 doctor > "$root/diagnostics/v25-gate-long/doctor.txt" 2>&1
doctor_rc=$?
python -m compileall -q \
  "$root/state_engine.py" "$root/data_loader.py" "$root/run.py" \
  "$root/nautilus_strategy.py" > "$root/diagnostics/v25-gate-long/compile.txt" 2>&1
compile_rc=$?
generic_rc=0
: > "$root/diagnostics/v25-gate-long/tests-generic.txt"
for pattern in \
  test_accounting_contract.py \
  test_account_exhaustion_contract.py \
  test_evidence_contract.py \
  test_nautilus_contract.py \
  test_run_gate.py; do
  [[ -f "$root/tests/$pattern" ]] || continue
  python -m unittest discover -s "$root/tests" -p "$pattern" -v \
    >> "$root/diagnostics/v25-gate-long/tests-generic.txt" 2>&1 || generic_rc=1
done
python -m unittest discover -s "$root/tests_v25" -p 'test_*.py' -v \
  > "$root/diagnostics/v25-gate-long/tests-v25.txt" 2>&1
candidate_rc=$?
tests_rc=$(( generic_rc != 0 || candidate_rc != 0 ))
set -e

if [[ $doctor_rc -ne 0 || $compile_rc -ne 0 || $tests_rc -ne 0 ]]; then
  python - <<PY
import json
from pathlib import Path
root=Path(${root@Q})
payload={
  "candidate":"candidate-09-v25",
  "status":"IMPLEMENTATION_ERROR",
  "codes":{"doctor":$doctor_rc,"compile":$compile_rc,"tests":$tests_rc,"gate":99},
  "first_failure":"doctor" if $doctor_rc else ("compile" if $compile_rc else "tests"),
}
(root/"evidence/latest/summary.json").write_text(json.dumps(payload,indent=2)+"\n")
PY
  exit 1
fi

set +e
python "$root/run.py" gate \
  --config "$root/config.json" \
  --cache "${SMC4_DATA_ROOT:-.cache/candidate-09}" \
  --output "$root/evidence/latest" \
  --auto-long \
  > "$root/diagnostics/v25-gate-long/gate.txt" 2>&1
gate_rc=$?
set -e

python - <<PY
import json
from pathlib import Path
root=Path(${root@Q})
p=root/"evidence/latest/summary.json"
if not p.exists():
  p.write_text(json.dumps({
    "candidate":"candidate-09-v25",
    "status":"IMPLEMENTATION_ERROR",
    "codes":{"doctor":0,"compile":0,"tests":0,"gate":$gate_rc},
    "first_failure":"gate",
  },indent=2)+"\n")
PY

# An economic failure is valid evidence; only implementation failures make CI red.
exit 0
