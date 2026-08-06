from __future__ import annotations

from dataclasses import dataclass
import unittest

from excursion_diagnostics import calculate_excursion_diagnostics


@dataclass(frozen=True)
class Observation:
    ts_ns: int
    high: float
    low: float
    close: float


class ExcursionDiagnosticsTests(unittest.TestCase):
    def base_trade(self, direction: str):
        return {
            "opened_ts_ns": 100,
            "actual_entry_price": 100.0,
            "direction": direction,
            "stop_price": 98.0 if direction == "LONG" else 102.0,
            "atr_at_signal": 2.0,
            "fee_rate_per_fill": 0.0,
            "loss_per_unit": 2.0,
        }

    def test_long_path_tracks_intrabar_and_close_mfe(self) -> None:
        result = calculate_excursion_diagnostics(
            self.base_trade("LONG"),
            [
                Observation(100, 101.0, 99.0, 100.5),
                Observation(200, 103.0, 98.5, 102.0),
                Observation(300, 102.5, 97.9, 98.0),
            ],
            closed_ts_ns=300,
            tick=0.0,
        )
        self.assertEqual(result["mfe_price_distance"], 3.0)
        self.assertAlmostEqual(result["mfe_stop_units"], 1.5)
        self.assertAlmostEqual(result["mae_stop_units"], 1.05)
        self.assertEqual(result["first_close_half_r_ts_ns"], 200)
        self.assertEqual(result["first_close_one_r_ts_ns"], 200)

    def test_short_is_symmetric(self) -> None:
        result = calculate_excursion_diagnostics(
            self.base_trade("SHORT"),
            [
                Observation(100, 101.0, 99.0, 99.5),
                Observation(200, 101.5, 97.0, 98.0),
                Observation(300, 102.1, 98.5, 102.0),
            ],
            closed_ts_ns=300,
            tick=0.0,
        )
        self.assertEqual(result["mfe_price_distance"], 3.0)
        self.assertAlmostEqual(result["mfe_stop_units"], 1.5)
        self.assertAlmostEqual(result["mae_stop_units"], 1.05)
        self.assertEqual(result["first_close_one_r_ts_ns"], 200)

    def test_observations_after_nautilus_close_are_excluded(self) -> None:
        result = calculate_excursion_diagnostics(
            self.base_trade("LONG"),
            [
                Observation(100, 101.0, 99.0, 100.5),
                Observation(400, 120.0, 90.0, 115.0),
            ],
            closed_ts_ns=200,
            tick=0.0,
        )
        self.assertEqual(result["path_observations"], 1)
        self.assertEqual(result["mfe_price_distance"], 1.0)


if __name__ == "__main__":
    unittest.main()
