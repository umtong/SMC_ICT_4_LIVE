from __future__ import annotations

import unittest

from agg_trade_profile_data import _timestamp_ms, _value_area


class AggTradeProfileDataTests(unittest.TestCase):
    def test_value_area_expands_around_poc_by_adjacent_volume(self):
        poc, val, vah, fraction, concentration, lower, upper = _value_area(
            {98.0: 5.0, 99.0: 20.0, 100.0: 40.0, 101.0: 25.0, 102.0: 10.0},
            0.70,
        )
        self.assertEqual(poc, 100.0)
        self.assertEqual((val, vah), (99.0, 101.0))
        self.assertAlmostEqual(fraction, 0.85)
        self.assertAlmostEqual(concentration, 0.40)
        self.assertAlmostEqual(lower, 0.05)
        self.assertAlmostEqual(upper, 0.10)

    def test_timestamp_unit_is_explicit(self):
        self.assertEqual(_timestamp_ms("1700000000000"), 1700000000000)
        self.assertEqual(_timestamp_ms("1700000000000000"), 1700000000000)


if __name__ == "__main__":
    unittest.main()
