#!/usr/bin/env python3
"""Apply idempotent correctness fixes to the microstructure detector."""
from __future__ import annotations

from pathlib import Path

MARKER = "C11_MICRO_POST_CLASSIFICATION_AGE"


def apply(root: Path) -> int:
    path = root / "microstructure.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return 0
    old = '''        if self.active is not None:
            if self.active.phase == "PROBING":
                self._update_probe(bar, atr, return_rms, flow_rms)
            plan = self._maybe_absorption_plan(bar, atr)
'''
    new = '''        if self.active is not None:
            if self.active.phase == "PROBING":
                self._update_probe(bar, atr, return_rms, flow_rms)
            else:
                # C11_MICRO_POST_CLASSIFICATION_AGE: reclaim/retest expiry is
                # measured in completed seconds after classification as well.
                self.active.bars += 1
            plan = self._maybe_absorption_plan(bar, atr)
'''
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"microstructure event-age anchor count={count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    print(f"microstructure fixes applied: {apply(root)}")


if __name__ == "__main__":
    main()
