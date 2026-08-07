#!/usr/bin/env python3
"""Remove only V30's simultaneous original-extreme break requirement.

A failed fair-value reversion is first identified by the basis re-exiting the
same frozen fence with spot/futures flow in the original direction. Price must
still close beyond and defend the original event extreme on the later completed
retest bar. All other V30 logic, windows and native execution remain unchanged.
"""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = '''        beyond_extreme = (
            bar.futures.close > event_extreme
            if original_direction > 0
            else bar.futures.close < event_extreme
        )
        body = original_direction * (bar.futures.close - bar.futures.open) > 0.0
        flows = (
            original_direction * bar.futures.flow > 0.0
            and original_direction * bar.spot.flow > 0.0
        )
        if outside and beyond_extreme and body and flows:
'''
NEW = '''        body = original_direction * (bar.futures.close - bar.futures.open) > 0.0
        flows = (
            original_direction * bar.futures.flow > 0.0
            and original_direction * bar.spot.flow > 0.0
        )
        if outside and body and flows:
'''
POLICY_OLD = '''            "fence re-break, original extreme break, futures/spot flow agreement "
            "and completed failed-boundary retest are required"
'''
POLICY_NEW = '''            "fence re-break and futures/spot flow agreement identify failed reversion; "
            "the original extreme must still be crossed and defended on the completed retest"
'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    changed = False
    for old, new in ((OLD, NEW), (POLICY_OLD, POLICY_NEW)):
        if new in source:
            continue
        if old not in source:
            raise RuntimeError("V30 extreme-break block not found")
        source = source.replace(old, new, 1)
        changed = True
    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path("research/candidate-03/derive_nt_lvcfr_v30_signals.py"),
    )
    args = parser.parse_args()
    print(f"V30 failure-extreme patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
