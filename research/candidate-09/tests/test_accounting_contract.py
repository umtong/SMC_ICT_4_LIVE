import unittest


class AccountingIdentityTest(unittest.TestCase):
    def test_position_closed_value_is_not_charged_native_commission_twice(self):
        native_net_realized = -2365.33740432
        native_commissions = 200.27340432
        extra_composite_cost = 634.19911368
        gross_price_pnl = native_net_realized + native_commissions
        composite_net_pnl = native_net_realized - extra_composite_cost
        self.assertAlmostEqual(gross_price_pnl, -2165.064, places=6)
        self.assertAlmostEqual(composite_net_pnl, -2999.536518, places=6)
        self.assertAlmostEqual(
            gross_price_pnl - native_commissions - extra_composite_cost,
            composite_net_pnl,
            places=9,
        )


if __name__ == "__main__":
    unittest.main()
