from __future__ import annotations

import unittest

from fee_profiles_v5 import FEE_PROFILES, make_instrument_with_fee_profile


class FeeProfileTests(unittest.TestCase):
    def test_usd_m_profile_is_distinct_from_legacy_spot_like_control(self) -> None:
        legacy = FEE_PROFILES["legacy_7_5bps"]
        futures = FEE_PROFILES["usd_m_vip0"]
        self.assertEqual(str(legacy.maker_rate), "0.00075")
        self.assertEqual(str(legacy.taker_rate), "0.00075")
        self.assertEqual(str(futures.maker_rate), "0.00020")
        self.assertEqual(str(futures.taker_rate), "0.00050")
        self.assertLess(futures.maker_rate, futures.taker_rate)

    def test_instrument_carries_selected_maker_and_taker_fees(self) -> None:
        profile = FEE_PROFILES["usd_m_vip0"]
        instrument = make_instrument_with_fee_profile("BTCUSDT", profile)
        self.assertEqual(instrument.maker_fee, profile.maker_rate)
        self.assertEqual(instrument.taker_fee, profile.taker_rate)
        self.assertEqual(instrument.raw_symbol.value, "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
