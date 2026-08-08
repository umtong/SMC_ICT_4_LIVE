#!/usr/bin/env python3
"""Apply a numerical-only tolerance to Candidate 26 ATR penetration bounds.

The market contract is unchanged.  Decimal-looking exchange prices are held as
binary floats in the detector, so an exact 0.10 ATR penetration can evaluate as
0.09999999999999.  The tolerance prevents a causal boundary event from being
rejected solely by representation error; it does not widen the economic range.
"""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = """        if crossed and self.config.sweep_min_atr <= penetration <= self.config.sweep_max_atr:\n            eligible.append((int(candidate_ts_ns), int(known_ts_ns), float(level), penetration))\n"""
NEW = """        numerical_tolerance = 1e-12\n        within_penetration_contract = (\n            self.config.sweep_min_atr - numerical_tolerance\n            <= penetration\n            <= self.config.sweep_max_atr + numerical_tolerance\n        )\n        if crossed and within_penetration_contract:\n            eligible.append((int(candidate_ts_ns), int(known_ts_ns), float(level), penetration))\n"""


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW in source:
        return False
    if source.count(OLD) != 1:
        raise RuntimeError("Candidate 26 penetration anchor changed")
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate26 penetration tolerance patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
