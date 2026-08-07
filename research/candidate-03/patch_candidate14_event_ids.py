#!/usr/bin/env python3
"""Uniquify diagnostic-only AMBIGUOUS event IDs without changing strategy semantics.

Candidate 14 can emit more than one both-sides-swept diagnostic in a week.  The
frozen detector used the literal scenario ID ``AMBIGUOUS`` for every occurrence,
so the event-ledger validator incorrectly joined independent terminal chains and
aborted H04.  This patch changes only the audit identifier to
``<instrument>-AMBIGUOUS-<causal bar close ns>``.  Sweep detection, state,
candidate generation, orders, prices, costs, sizing and Nautilus execution are
untouched.
"""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = '''            self._event(\n                "AMBIGUOUS", "AMBIGUOUS_SWEEP", bar.ts_ns, bar.ts_ns,\n                "ARMED", "TERMINAL", "BAR_PATH_UNRESOLVABLE", bar.close,\n'''
NEW = '''            ambiguous_scenario_id = f"{self.instrument_id}-AMBIGUOUS-{bar.ts_ns}"\n            self._event(\n                ambiguous_scenario_id, "AMBIGUOUS_SWEEP", bar.ts_ns, bar.ts_ns,\n                "ARMED", "TERMINAL", "BAR_PATH_UNRESOLVABLE", bar.close,\n'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW in source:
        return False
    occurrences = source.count(OLD)
    if occurrences != 1:
        raise RuntimeError(f"expected exactly one ambiguous-event block, found {occurrences}")
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    changed = apply(args.path)
    print(f"candidate14 unique ambiguous event-id patch applied={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
