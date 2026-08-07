#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
WEEK="${1:-W1}"
OUT="$CAND/results_complex/$WEEK"
case "$WEEK" in W1|W2|W3);; *) exit 64;; esac

smc4 doctor
python -m py_compile \
  "$CAND/market_complex.py" "$CAND/global_allocator.py" \
  "$CAND/complex_engine.py" "$CAND/run_complex_nautilus.py" \
  "$CAND/evidence_audit.py"
python -m unittest discover -s "$CAND" -p 'test_*.py' -v

if [[ "$WEEK" != W1 ]]; then
  test -s "$CAND/results_complex/W1/audit.json"
  python - "$CAND/results_complex/W1/audit.json" <<'PY'
import json
import sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
if result.get("advance_allowed") is not True:
    raise SystemExit("complex W1 did not authorize an independent week")
PY
fi

rm -rf "$OUT"
mkdir -p "$OUT"
python "$CAND/run_complex_nautilus.py" \
  --config "$CAND/complex_config.json" \
  --week "$WEEK" \
  --output "$OUT"
python "$CAND/evidence_audit.py" "$OUT" \
  --week "$WEEK" \
  --output "$OUT/audit.json"
