import unittest

from strategy import initiative_depth_support, initiative_passes


class Candidate32LogicTests(unittest.TestCase):
    def test_depth_symmetry(self):
        self.assertEqual(initiative_depth_support(1, 0.02, 0.00), 0.02)
        self.assertEqual(initiative_depth_support(-1, 0.00, 0.02), 0.02)
        self.assertEqual(initiative_depth_support(1, 0.00, -0.03), 0.03)
        self.assertEqual(initiative_depth_support(-1, -0.03, 0.00), 0.03)

    def test_later_initiative_contract(self):
        passed, evidence = initiative_passes(
            side=1,
            open_price=100.0,
            high=102.0,
            low=99.5,
            close_price=101.8,
            structure=101.0,
            atr=2.0,
            flow_60s=0.20,
            efficiency_60s=0.60,
            bid_depth_change_1m=0.02,
            ask_depth_change_1m=-0.01,
            use_displayed_depth=True,
            min_body_atr=0.20,
            min_flow=0.08,
            min_efficiency=0.25,
            min_close_location=0.60,
            min_depth_support=0.01,
        )
        self.assertTrue(passed)
        self.assertTrue(evidence["depth_pass"])

    def test_no_depth_is_exact_single_ablation(self):
        kwargs = dict(
            side=-1,
            open_price=100.0,
            high=100.5,
            low=98.0,
            close_price=98.2,
            structure=99.0,
            atr=2.0,
            flow_60s=-0.20,
            efficiency_60s=0.60,
            bid_depth_change_1m=0.02,
            ask_depth_change_1m=-0.02,
            min_body_atr=0.20,
            min_flow=0.08,
            min_efficiency=0.25,
            min_close_location=0.60,
            min_depth_support=0.01,
        )
        with_depth, _ = initiative_passes(
            use_displayed_depth=True,
            **kwargs,
        )
        without_depth, _ = initiative_passes(
            use_displayed_depth=False,
            **kwargs,
        )
        self.assertFalse(with_depth)
        self.assertTrue(without_depth)


if __name__ == "__main__":
    unittest.main()
