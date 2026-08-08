#!/usr/bin/env python3
"""Generate a C1-C3 audit from the tested microstructure evidence auditor."""
from __future__ import annotations

from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    source = (root / "audit_microstructure.py").read_text(encoding="utf-8")
    old = 'parser.add_argument("--week", choices=("M1", "M2", "M3"), required=True)'
    new = 'parser.add_argument("--week", choices=("C1", "C2", "C3"), required=True)'
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"cross-market audit week anchor count={count}")
    source = source.replace(old, new, 1)
    source = source.replace(
        '"schema": "candidate-11-microstructure-audit-v1",',
        '"schema": "candidate-11-cross-market-audit-v1",',
        1,
    )
    (root / "audit_cross_market.py").write_text(source, encoding="utf-8")
    print("cross-market audit materialized")


if __name__ == "__main__":
    main()
