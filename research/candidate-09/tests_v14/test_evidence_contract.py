from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run import evidence_details_for_output


def detail(variant: str, segment: str):
    return SimpleNamespace(outcome=SimpleNamespace(variant=variant, segment=segment))


class LongEvidenceContractTest(unittest.TestCase):
    def test_fixed_baseline_and_long_baseline_are_persisted_without_ablations(self):
        week_a = detail("baseline", "week-a")
        week_b = detail("baseline", "week-b")
        long_btc = detail("baseline", "long-btc")
        ablation = detail("no-flow", "week-a")
        selected = evidence_details_for_output(
            [week_a, week_b],
            [week_a, week_b, ablation, long_btc],
        )
        self.assertEqual(selected, [week_a, week_b, long_btc])

    def test_direct_long_mode_does_not_duplicate_the_long_detail(self):
        long_btc = detail("baseline", "long-btc")
        self.assertEqual(evidence_details_for_output([long_btc], [long_btc]), [long_btc])


if __name__ == "__main__":
    unittest.main()
