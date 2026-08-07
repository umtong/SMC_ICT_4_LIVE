#!/usr/bin/env python3
"""Generate C4-C6 runner and audit from the frozen C1-C3 harness."""
from __future__ import annotations

from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


def main() -> None:
    root = Path(__file__).resolve().parent
    runner = (root / "run_cross_market_nautilus.py").read_text(encoding="utf-8")
    runner = replace_once(
        runner,
        'default=ROOT / "cross_market_protocol.json"',
        'default=ROOT / "cross_market_holdout_protocol.json"',
        "holdout protocol default",
    )
    runner = replace_once(
        runner,
        'parser.add_argument("--week", choices=("C1", "C2", "C3"), default="C1")',
        'parser.add_argument("--week", choices=("C4", "C5", "C6"), default="C4")',
        "holdout week choices",
    )
    runner = replace_once(
        runner,
        'default=ROOT / "results" / "CROSS_C1"',
        'default=ROOT / "results" / "CROSS_HOLDOUT_C4"',
        "holdout output default",
    )
    (root / "run_cross_market_holdout_nautilus.py").write_text(runner, encoding="utf-8")

    audit = (root / "audit_cross_market.py").read_text(encoding="utf-8")
    audit = replace_once(
        audit,
        'parser.add_argument("--week", choices=("C1", "C2", "C3"), required=True)',
        'parser.add_argument("--week", choices=("C4", "C5", "C6"), required=True)',
        "holdout audit choices",
    )
    (root / "audit_cross_market_holdout.py").write_text(audit, encoding="utf-8")
    print("cross-market holdout runner and audit materialized")


if __name__ == "__main__":
    main()
