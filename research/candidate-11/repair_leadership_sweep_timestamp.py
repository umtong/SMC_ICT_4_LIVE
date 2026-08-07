#!/usr/bin/env python3
"""Bind leadership evidence to the initial source sweep, not the final raid bar."""
from __future__ import annotations

from pathlib import Path

LEGACY = '                "sweep_ts_ns": a.sweep.ts_ns,\n'
CORRECT = '                "sweep_ts_ns": self.bars[a.sweep_index].ts_ns,\n'


def replace(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    if CORRECT in source:
        return 0
    count = source.count(LEGACY)
    if count != 1:
        raise SystemExit(f"initial-sweep timestamp anchor mismatch in {path.name}: {count}")
    path.write_text(source.replace(LEGACY, CORRECT, 1), encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    changed = replace(root / "logic.py") + replace(root / "apply_market_leadership.py")
    print(f"initial-sweep timestamp repairs applied: {changed}")


if __name__ == "__main__":
    main()
