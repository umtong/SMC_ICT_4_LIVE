#!/usr/bin/env python3
"""Persist the initial source-sweep timestamp as immutable auction state."""
from __future__ import annotations

from pathlib import Path

LEGACY = '                "sweep_ts_ns": a.sweep.ts_ns,\n'
INDEXED = '                "sweep_ts_ns": self.bars[a.sweep_index].ts_ns,\n'
FINAL = (
    '                "sweep_ts_ns": '
    '(a.initial_sweep_ts_ns if a.initial_sweep_ts_ns is not None else a.sweep.ts_ns),\n'
)
AUCTION_ANCHOR = '    cascade_count: int = 0\n'
AUCTION_FIELD = '    cascade_count: int = 0\n    initial_sweep_ts_ns: int | None = None\n'
CONSTRUCTOR_ANCHOR = '            sweep_index=self._index,\n            atr=atr,\n'
CONSTRUCTOR_FIELD = (
    '            sweep_index=self._index,\n'
    '            atr=atr,\n'
    '            initial_sweep_ts_ns=bar.ts_ns,\n'
)


def replace_one(source: str, alternatives: tuple[str, ...], final: str, label: str) -> tuple[str, int]:
    if final in source:
        return source, 0
    matches = [(old, source.count(old)) for old in alternatives if source.count(old)]
    if len(matches) != 1 or matches[0][1] != 1:
        raise SystemExit(f"{label}: expected one migration anchor, found={matches}")
    return source.replace(matches[0][0], final, 1), 1


def repair_logic(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    changed = 0
    source, count = replace_one(source, (LEGACY, INDEXED), FINAL, "plan initial-sweep timestamp")
    changed += count
    source, count = replace_one(source, (AUCTION_ANCHOR,), AUCTION_FIELD, "auction initial-sweep field")
    changed += count
    source, count = replace_one(source, (CONSTRUCTOR_ANCHOR,), CONSTRUCTOR_FIELD, "auction initial-sweep capture")
    changed += count
    path.write_text(source, encoding="utf-8")
    return changed


def repair_materializer(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    changed = 0
    # Keep the generated migration's target expression synchronized with logic.py.
    for old in (INDEXED.strip(), LEGACY.strip()):
        count = source.count(old)
        if count:
            source = source.replace(old, FINAL.strip())
            changed += count
    path.write_text(source, encoding="utf-8")
    return changed


def main() -> None:
    root = Path(__file__).resolve().parent
    changed = repair_logic(root / "logic.py")
    changed += repair_materializer(root / "apply_market_leadership.py")
    print(f"immutable initial-sweep timestamp repairs applied: {changed}")


if __name__ == "__main__":
    main()
