from datetime import date
import unittest
import pandas as pd
from run_complex_nautilus import build_contexts, local_bounds, SYMBOLS


class ContextTests(unittest.TestCase):
    def frame(self):
        index = pd.date_range(
            "2024-08-18T00:00:00Z",
            "2024-08-28T00:00:00Z",
            freq="1min",
            inclusive="left",
        )
        return pd.DataFrame(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1.0,
                "taker_buy_volume": 0.5,
            },
            index=index,
        )

    def test_first_evaluation_london_uses_prior_asia(self):
        frames = {symbol: self.frame() for symbol in SYMBOLS}
        contexts = build_contexts(frames, date(2024, 8, 20), date(2024, 8, 27))
        timestamp = int(pd.Timestamp("2024-08-20T06:30:00Z").value)
        self.assertIn(("BTCUSDT", timestamp), contexts)
        self.assertEqual(contexts[("BTCUSDT", timestamp)].target_session, "LONDON")

    def test_contexts_never_extend_beyond_evaluation_end(self):
        frames = {symbol: self.frame() for symbol in SYMBOLS}
        contexts = build_contexts(frames, date(2024, 8, 20), date(2024, 8, 27))
        end = int(pd.Timestamp("2024-08-27T00:00:00Z").value)
        self.assertTrue(all(timestamp < end for _, timestamp in contexts))

    def test_dst_aware_bounds(self):
        summer = local_bounds(date(2024, 8, 20), 7, 10)
        winter = local_bounds(date(2024, 1, 20), 7, 10)
        self.assertEqual(summer[0].hour, 11)
        self.assertEqual(winter[0].hour, 12)


if __name__ == "__main__":
    unittest.main()
