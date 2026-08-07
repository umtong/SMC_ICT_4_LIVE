#!/usr/bin/env python3
"""Assign a bar ending on a block boundary to the block it completed."""
from __future__ import annotations

from pathlib import Path

MARKER = "C11_IRX_COMPLETED_BAR_BLOCK_ID"


def apply(root: Path) -> int:
    path = root / "internal_reclaim.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return 0
    old = "        block_id = bar.ts_ns // (minutes * MINUTE_NS)\n"
    new = (
        "        # C11_IRX_COMPLETED_BAR_BLOCK_ID: a bar stamped exactly at the\n"
        "        # boundary belongs to the auction interval it just completed.\n"
        "        block_id = (bar.ts_ns - 1) // (minutes * MINUTE_NS)\n"
    )
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"completed-bar block anchor count={count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    print(f"internal-reclaim block fix applied: {apply(root)}")


if __name__ == "__main__":
    main()
