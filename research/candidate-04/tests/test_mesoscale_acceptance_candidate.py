from __future__ import annotations

from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "mesoscale_acceptance_candidate.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v8_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MesoscaleAcceptanceTests(unittest.TestCase):
    def config(self) -> SimpleNamespace:
        return SimpleNamespace(
            trend_structure_minutes=5,
            stress_inventory_quantile_window_minutes=720,
            stress_inventory_quantile_min_periods=240,
            basis_stress_threshold_bps=0.0,
        )

    def test_cutoff_is_past_only(self) -> None:
        index = pd.date_range("2024-01-01", periods=1000, freq="1min", tz="UTC")
        close = 100.0 + np.sin(np.arange(1000) / 8.0) + np.arange(1000) * 0.001
        data = pd.DataFrame({"close": close}, index=index)
        _, cutoff_before, _ = MODULE.mesoscale_acceptance_series(data, self.config())

        extended = pd.concat(
            [
                data,
                pd.DataFrame(
                    {"close": [500.0, 50.0, 600.0]},
                    index=pd.date_range(
                        index[-1] + pd.Timedelta(minutes=1),
                        periods=3,
                        freq="1min",
                    ),
                ),
            ],
        )
        _, cutoff_after, _ = MODULE.mesoscale_acceptance_series(extended, self.config())
        pd.testing.assert_series_equal(cutoff_before, cutoff_after.iloc[: len(data)])

    def test_efficient_five_minute_auction_exceeds_past_cutoff(self) -> None:
        index = pd.date_range("2024-01-01", periods=1000, freq="1min", tz="UTC")
        close = 100.0 + 0.15 * np.sin(np.arange(1000) / 2.0)
        close[-5:] = [100.2, 100.4, 100.6, 100.8, 101.0]
        data = pd.DataFrame({"close": close}, index=index)
        efficiency, cutoff, raw_return = MODULE.mesoscale_acceptance_series(
            data,
            self.config(),
        )
        self.assertGreater(raw_return.iloc[-1], 0.0)
        self.assertGreaterEqual(efficiency.iloc[-1], cutoff.iloc[-1])

    def test_choppy_terminal_path_fails_acceptance(self) -> None:
        index = pd.date_range("2024-01-01", periods=1000, freq="1min", tz="UTC")
        close = 100.0 + np.arange(1000) * 0.001
        close[-6:] = [100.0, 101.0, 99.4, 101.1, 99.5, 100.2]
        data = pd.DataFrame({"close": close}, index=index)
        efficiency, cutoff, raw_return = MODULE.mesoscale_acceptance_series(
            data,
            self.config(),
        )
        self.assertGreater(raw_return.iloc[-1], 0.0)
        self.assertLess(efficiency.iloc[-1], cutoff.iloc[-1])


if __name__ == "__main__":
    unittest.main()
