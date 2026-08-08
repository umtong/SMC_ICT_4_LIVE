#!/usr/bin/env python3
"""Extend the independent evidence audit to frozen W7-W9 intervals."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATH = ROOT / "evidence_audit.py"
OLD = 'parser.add_argument("--week", choices=("W1", "W2", "W3", "W4", "W5", "W6", "LONG"), required=True)'
NEW = 'parser.add_argument("--week", choices=("W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9", "LONG"), required=True)'


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    if NEW in source:
        print("evidence-audit frozen-week migration applied: 0")
        return
    count = source.count(OLD)
    if count != 1:
        raise SystemExit(f"evidence-audit week anchor: expected one, found {count}")
    PATH.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    print("evidence-audit frozen-week migration applied: 1")


if __name__ == "__main__":
    main()
