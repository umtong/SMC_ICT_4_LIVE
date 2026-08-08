#!/usr/bin/env python3
"""Generate the authoritative cross-market evaluator with audit v2."""
from __future__ import annotations

from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    source = (root / "run_cross_market_gated.sh").read_text(encoding="utf-8")
    old = 'python "$CAND/materialize_cross_market_audit.py"\n'
    if source.count(old) != 1:
        raise SystemExit("cross-market audit materializer anchor missing")
    source = source.replace(old, "", 1)
    if '"$CAND/audit_cross_market.py"' not in source:
        raise SystemExit("cross-market audit path anchor missing")
    source = source.replace('"$CAND/audit_cross_market.py"', '"$CAND/audit_cross_market_v2.py"')
    destination = root / "run_cross_market_generated_v2.sh"
    destination.write_text(source, encoding="utf-8")
    destination.chmod(0o755)
    print("authoritative cross-market evaluator materialized")


if __name__ == "__main__":
    main()
