#!/usr/bin/env python3
"""Generate the M4-M6 runner from the tested M1-M3 Nautilus harness."""
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
        "from microstructure import FlowBar, MicroPlan\nfrom microstructure_v2 import CombinedMicrostructureEngine as AggressorImpactAuctionEngine\n",
        "combined engine import",
    )
    source = source.replace(
        '"candidate": "candidate-11-btc-aggressor-impact-auction",',
        '"candidate": "candidate-11-btc-impact-plus-vwap-exhaustion",',
        1,
    )
    source = source.replace(
        'default=ROOT / "microstructure_protocol.json"',
        'default=ROOT / "microstructure_v2_protocol.json"',
        1,
    )
    source = replace_once(
        source,
        'parser.add_argument("--week", choices=("M1", "M2", "M3"), default="M1")',
        'parser.add_argument("--week", choices=("M4", "M5", "M6"), default="M4")',
        "v2 week choices",
    )
    source = source.replace(
        'default=ROOT / "results" / "MICRO_M1"',
        'default=ROOT / "results" / "MICRO_V2_M4"',
        1,
    )
    destination = root / "run_microstructure_v2_nautilus.py"
    destination.write_text(source, encoding="utf-8")

    audit = (root / "audit_microstructure.py").read_text(encoding="utf-8")
    audit = replace_once(
        audit,
        'parser.add_argument("--week", choices=("M1", "M2", "M3"), required=True)',
        'parser.add_argument("--week", choices=("M4", "M5", "M6"), required=True)',
        "v2 audit choices",
    )
    (root / "audit_microstructure_v2.py").write_text(audit, encoding="utf-8")
    print("microstructure-v2 runner and audit materialized")


if __name__ == "__main__":
    main()
