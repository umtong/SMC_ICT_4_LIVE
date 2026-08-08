#!/usr/bin/env python3
"""Repair long-interval event identity without changing alpha or execution.

Frozen Candidate 11 records every bar whose path sweeps both sides under the
literal scenario id ``AMBIGUOUS``.  More than one such observation in a long
continuous run is therefore interpreted by the event-log validator as repeated
transitions of one scenario, even though each bar is terminal and independent.

This deterministic patch changes only that diagnostic identity to
``<instrument>-AMBIGUOUS-<event timestamp>``.  Detection, prices, state tests,
orders, fills, costs, risk sizing, portfolio state and NAV are untouched.
"""
from __future__ import annotations

import argparse
from pathlib import Path


OLD = '''            self._event(
                "AMBIGUOUS", "AMBIGUOUS_SWEEP", bar.ts_ns, bar.ts_ns,
'''
NEW = '''            self._event(
                f"{self.instrument_id}-AMBIGUOUS-{bar.ts_ns}",
                "AMBIGUOUS_SWEEP", bar.ts_ns, bar.ts_ns,
'''


def patch(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(OLD)
    if count != 1:
        raise RuntimeError(
            f"ambiguous event identity marker: expected one, found {count}",
        )
    path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
