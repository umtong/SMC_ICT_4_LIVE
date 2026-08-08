#!/usr/bin/env python3
"""Fail-closed source patch for the cascade-end second boundary."""
from __future__ import annotations

from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_causal_boundary.py SCREEN_PATH")
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    old = '''        start_i = _nearest_index(index, cascade.start, "left")
        end_i = _nearest_index(index, cascade.end, "right")
        if start_i is None or end_i is None or start_i <= 0 or end_i <= start_i:
            continue
        end_i = min(end_i, len(trades) - 1)
'''
    new = '''        start_i = _nearest_index(index, cascade.start, "left")
        end_i = int(index.searchsorted(cascade.end.floor("s"), side="right")) - 1
        if start_i is None or end_i < 0 or start_i <= 0 or end_i < start_i:
            continue
        end_i = min(end_i, len(trades) - 1)
'''
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"cascade boundary contract drifted: {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
