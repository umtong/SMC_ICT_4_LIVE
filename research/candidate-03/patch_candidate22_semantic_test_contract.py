#!/usr/bin/env python3
"""Update the one frozen assertion intentionally changed by Candidate 22."""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = '''    def test_unanimous_moderate_countertrend_core(self):\n        result = self.classify(self.decision())\n        self.assertTrue(result.approved)\n        self.assertEqual(result.reason, "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS")\n'''
NEW = '''    def test_unanimous_moderate_countertrend_core_is_contested(self):\n        result = self.classify(self.decision())\n        self.assertFalse(result.approved)\n        self.assertEqual(result.reason, "SEMANTIC_FAR_MODERATE_COUNTERTREND_CONTESTED")\n'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    source = args.path.read_text(encoding="utf-8")
    if NEW in source:
        print("candidate22 semantic test contract already updated")
        return 0
    if source.count(OLD) != 1:
        raise RuntimeError(f"expected one frozen assertion block, found {source.count(OLD)}")
    args.path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    print("candidate22 semantic test contract updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
