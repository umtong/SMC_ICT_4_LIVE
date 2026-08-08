import math
import unittest

from features import _longest_run


class Candidate33FootprintContracts(unittest.TestCase):
    def test_three_consecutive_ticks_are_one_stack(self):
        length, low, high = _longest_run(
            ticks=[1000, 1001, 1002, 1004],
            flags=[True, True, True, True],
        )
        self.assertEqual(length, 3)
        self.assertAlmostEqual(low, 100.0)
        self.assertAlmostEqual(high, 100.2)

    def test_gaps_break_a_stack(self):
        length, low, high = _longest_run(
            ticks=[1000, 1002, 1004],
            flags=[True, True, True],
        )
        self.assertEqual(length, 1)
        self.assertAlmostEqual(low, 100.0)
        self.assertAlmostEqual(high, 100.0)

    def test_no_stack_returns_nan_zone(self):
        length, low, high = _longest_run(
            ticks=[1000, 1001],
            flags=[False, False],
        )
        self.assertEqual(length, 0)
        self.assertTrue(math.isnan(low))
        self.assertTrue(math.isnan(high))


if __name__ == "__main__":
    unittest.main()
