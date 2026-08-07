#!/usr/bin/env python3
"""Generate the M7-M9 runner from the tested one-second Nautilus harness."""
from __future__ import annotations

from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


def main() -> None:
    root = Path(__file__).resolve().parent
    base = (root / "run_microstructure_nautilus.py").read_text(encoding="utf-8")
    source = replace_once(
        base,
        "from microstructure import AggressorImpactAuctionEngine, FlowBar, MicroPlan\n",
        "from microstructure import FlowBar, MicroPlan\nfrom microstructure_v3 import CombinedMicrostructureV3Engine as AggressorImpactAuctionEngine\n",
        "combined v3 engine import",
    )
    source = source.replace(
        '"candidate": "candidate-11-btc-aggressor-impact-auction",',
        '"candidate": "candidate-11-btc-balance-acceptance-suite",',
        1,
    )
    source = source.replace(
        'default=ROOT / "microstructure_protocol.json"',
        'default=ROOT / "microstructure_v3_protocol.json"',
        1,
    )
    source = replace_once(
        source,
        'parser.add_argument("--week", choices=("M1", "M2", "M3"), default="M1")',
        'parser.add_argument("--week", choices=("M7", "M8", "M9"), default="M7")',
        "v3 week choices",
    )
    source = source.replace(
        'default=ROOT / "results" / "MICRO_M1"',
        'default=ROOT / "results" / "MICRO_V3_M7"',
        1,
    )
    (root / "run_microstructure_v3_nautilus.py").write_text(source, encoding="utf-8")

    audit = (root / "audit_microstructure.py").read_text(encoding="utf-8")
    audit = replace_once(
        audit,
        'parser.add_argument("--week", choices=("M1", "M2", "M3"), required=True)',
        'parser.add_argument("--week", choices=("M7", "M8", "M9"), required=True)',
        "v3 audit choices",
    )
    (root / "audit_microstructure_v3.py").write_text(audit, encoding="utf-8")
    print("microstructure-v3 runner and audit materialized")


if __name__ == "__main__":
    main()
