#!/usr/bin/env python3
"""Generate the isolated M7-M9 balance-acceptance evaluator.

The evaluator reuses the tested one-second Nautilus harness while limiting
preflight tests and evidence to the balance-acceptance family plus the shared
bar and risk contracts it actually depends on.  Unrelated candidate families
cannot block or contaminate the screening result.
"""
from __future__ import annotations

from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


def main() -> None:
    root = Path(__file__).resolve().parent
    source = (root / "run_microstructure_v2_gated.sh").read_text(encoding="utf-8")
    replacements = (
        ("MICROSTRUCTURE_V2", "MICROSTRUCTURE_V3"),
        ("MICRO_V2_", "MICRO_V3_"),
        ("microstructure_v2_protocol.json", "microstructure_v3_protocol.json"),
        ("materialize_microstructure_v2_runner.py", "materialize_microstructure_v3_runner.py"),
        ("run_microstructure_v2_nautilus.py", "run_microstructure_v3_nautilus.py"),
        ("audit_microstructure_v2.py", "audit_microstructure_v3.py"),
        ("candidate-11-microstructure-v2-data", "candidate-11-microstructure-v3-data"),
        ("candidate-11-microstructure-v2-summary-v1", "candidate-11-microstructure-v3-summary-v1"),
        ("M6", "M9"),
        ("M5", "M8"),
        ("M4", "M7"),
        ("m4_screening_gate_passed", "m7_screening_gate_passed"),
    )
    for old, new in replacements:
        if old not in source:
            raise SystemExit(f"v3 gated materializer anchor missing: {old}")
        source = source.replace(old, new)

    source = replace_once(
        source,
        'python "$CAND/apply_microstructure_lifecycle_fix.py"\n',
        'python "$CAND/apply_microstructure_lifecycle_fix.py"\n'
        'python "$CAND/apply_balance_acceptance_safety.py"\n',
        "balance target-consumption guard",
    )
    source = replace_once(
        source,
        "python -m unittest discover -s \"$CAND\" -p 'test_*.py' -v\n",
        "python -m unittest discover -s \"$CAND\" -p 'test_microstructure.py' -v\n"
        "python -m unittest discover -s \"$CAND\" -p 'test_microstructure_v3*.py' -v\n"
        "python -m unittest discover -s \"$CAND\" -p 'test_bar_adapter.py' -v\n"
        "python -m unittest discover -s \"$CAND\" -p 'test_logic.py' -v\n",
        "focused balance-acceptance preflight",
    )

    destination = root / "run_microstructure_v3_generated.sh"
    destination.write_text(source, encoding="utf-8")
    destination.chmod(0o755)
    print("isolated microstructure-v3 gated evaluator materialized")


if __name__ == "__main__":
    main()
