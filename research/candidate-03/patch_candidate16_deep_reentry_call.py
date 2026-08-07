#!/usr/bin/env python3
"""Fix one accidental extra engine argument in Candidate 16 development code."""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = "if deep_reentry(self, state, bar, atr, self.config.acceptance_retest_atr):"
NEW = "if deep_reentry(state, bar, atr, self.config.acceptance_retest_atr):"


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW in source:
        return False
    if source.count(OLD) != 1:
        raise RuntimeError(f"expected one deep_reentry call, found {source.count(OLD)}")
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate16 deep_reentry call patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
