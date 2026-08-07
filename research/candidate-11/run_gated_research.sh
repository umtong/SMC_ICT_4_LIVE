#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
CAND="$ROOT/research/candidate-11"
RESULTS="$CAND/results"
WEEK="${1:-W1}"

case "$WEEK" in
  W1|W2|W3) ;;
  *) echo "week must be W1, W2, or W3" >&2; exit 64 ;;
esac

smc4 doctor

if [[ -f "$CAND/materialize_scdam.py" ]]; then
  python "$CAND/materialize_scdam.py"
fi

python -m compileall -q "$CAND"
python -m unittest discover -s "$CAND" -p 'test_*.py' -v

if [[ "$WEEK" != "W1" ]]; then
  test -s "$RESULTS/W1/audit.json"
  python - "$RESULTS/W1/audit.json" <<'PY'
import json
import sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
if result.get("advance_allowed") is not True:
    raise SystemExit("W1 did not authorize an independent-week evaluation")
PY
fi

rm -rf "$RESULTS/$WEEK"
mkdir -p "$RESULTS/$WEEK"

if [[ -x "$CAND/ci_validate.sh" ]]; then
  bash "$CAND/ci_validate.sh" "$WEEK"
elif [[ -f "$CAND/run_scdam.py" ]]; then
  python "$CAND/run_scdam.py" --week "$WEEK" --output "$RESULTS/$WEEK"
elif [[ -f "$CAND/run.py" ]]; then
  python "$CAND/run.py" --week "$WEEK" --output "$RESULTS/$WEEK"
else
  echo "No Candidate 11 Nautilus runner found" >&2
  exit 66
fi

python "$CAND/evidence_audit.py" "$RESULTS/$WEEK" --week "$WEEK" --output "$RESULTS/$WEEK/audit.json"

python - "$RESULTS/$WEEK/audit.json" <<'PY'
import json
import sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({
    "classification": result.get("classification"),
    "advance_allowed": result.get("advance_allowed"),
    "success_claim_allowed": result.get("success_claim_allowed"),
}, indent=2))
if result.get("classification") == "IMPLEMENTATION_OR_EVIDENCE_FAILURE":
    raise SystemExit(2)
PY
