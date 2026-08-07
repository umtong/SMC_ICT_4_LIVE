#!/usr/bin/env python3
"""Generate the M7-M9 gated evaluator from the M4-M6 evidence harness."""
from __future__ import annotations

from pathlib import Path


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
    destination = root / "run_microstructure_v3_generated.sh"
    destination.write_text(source, encoding="utf-8")
    destination.chmod(0o755)
    print("microstructure-v3 gated evaluator materialized")


if __name__ == "__main__":
    main()
