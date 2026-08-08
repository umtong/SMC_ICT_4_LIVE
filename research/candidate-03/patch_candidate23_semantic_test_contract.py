#!/usr/bin/env python3
"""Update the one frozen assertion intentionally changed by Candidate 23."""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = '''    def test_generic_trend_resumption_is_rejected_in_scdam_core(self):\n        scores = {symbol: 0.5 for symbol in SYMBOLS}\n        result = self.classify(self.decision(directional_trend_scores=scores))\n        self.assertFalse(result.approved)\n        self.assertEqual(result.reason, "SEMANTIC_FAR_NOT_COUNTERTREND")\n'''
NEW = '''    def test_generic_trend_resumption_is_distinct_scdam_role(self):\n        scores = {symbol: 0.5 for symbol in SYMBOLS}\n        result = self.classify(self.decision(directional_trend_scores=scores))\n        self.assertTrue(result.approved)\n        self.assertEqual(result.reason, "SEMANTIC_FAR_TREND_RESUMPTION_SYNCHRONIZED")\n'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    source = args.path.read_text(encoding="utf-8")
    if NEW in source:
        print("candidate23 semantic test contract already updated")
        return 0
    if source.count(OLD) != 1:
        raise RuntimeError(f"expected one frozen trend-resumption block, found {source.count(OLD)}")
    args.path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    print("candidate23 semantic test contract updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
