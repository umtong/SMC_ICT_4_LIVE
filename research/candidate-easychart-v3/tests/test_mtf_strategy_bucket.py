from __future__ import annotations

import unittest

from mtf_strategy import EasyChartMTFStrategy


class EasyChartMTFBucketTest(unittest.TestCase):
    def ts(self, minute: int) -> int:
        return minute * EasyChartMTFStrategy.NS_PER_MINUTE

    def test_expected_composite_count_for_four_symbols(self) -> None:
        self.assertEqual(EasyChartMTFStrategy.expected_composite_count(self.ts(5), 4), 4)
        self.assertEqual(EasyChartMTFStrategy.expected_composite_count(self.ts(15), 4), 8)
        self.assertEqual(EasyChartMTFStrategy.expected_composite_count(self.ts(60), 4), 12)


if __name__ == "__main__":
    unittest.main()
