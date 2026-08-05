from __future__ import annotations

from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "auction_activity_router_candidate.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v11_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AuctionActivityRouterTests(unittest.TestCase):
    def router(self) -> MODULE.RouterParameters:
        return MODULE.RouterParameters(
            activity_lookback_minutes=240,
            low_activity_path_bps_max=600.0,
            impact_requires_negative_basis=True,
        )

    def frame(self, step: float, basis: float) -> pd.DataFrame:
        index = pd.date_range("2024-01-01", periods=400, freq="1min", tz="UTC")
        signs = np.where(np.arange(400) % 2 == 0, 1.0, -1.0)
        close = 100.0 * np.cumprod(1.0 + signs * step)
        frame = pd.DataFrame(index=index)
        frame["close"] = close
        frame["trade_index_basis_bps"] = basis
        return frame

    def intent(self, scenario: str, index: int = 300) -> MODULE.Intent:
        return MODULE.Intent(
            scenario=scenario,
            side=1,
            signal_index=index,
            entry_index=index + 1,
            stop_level=99.0,
            event_indices=(index,),
            details={},
        )

    def test_activity_path_is_invariant_to_appended_future(self) -> None:
        frame = self.frame(0.0001, -2.0)
        before = MODULE.auction_path_bps(frame, self.router())
        extension = self.frame(0.0050, 20.0).iloc[:10].copy()
        extension.index = pd.date_range(
            frame.index[-1] + pd.Timedelta(minutes=1),
            periods=10,
            freq="1min",
        )
        after = MODULE.auction_path_bps(
            pd.concat([frame, extension]),
            self.router(),
        )
        pd.testing.assert_series_equal(before, after.iloc[: len(frame)])

    def test_path_threshold_separates_low_and_high_activity(self) -> None:
        low = MODULE.auction_path_bps(
            self.frame(0.0001, -2.0),
            self.router(),
        ).iloc[-1]
        high = MODULE.auction_path_bps(
            self.frame(0.0004, -2.0),
            self.router(),
        ).iloc[-1]
        self.assertLess(low, self.router().low_activity_path_bps_max)
        self.assertGreaterEqual(high, self.router().low_activity_path_bps_max)

    def test_route_details_do_not_change_signal_or_entry_time(self) -> None:
        parent = self.intent("IMPACT_EXHAUSTION_FAILED_PRICE_DISCOVERY")
        routed = MODULE._with_route_details(
            parent,
            route="LOW_ACTIVITY_NEGATIVE_BASIS_V10",
            path_bps=250.0,
            basis_bps=-3.0,
        )
        self.assertEqual(routed.signal_index, parent.signal_index)
        self.assertEqual(routed.entry_index, parent.entry_index)
        self.assertEqual(routed.stop_level, parent.stop_level)
        self.assertEqual(
            routed.details["auction_route"],
            "LOW_ACTIVITY_NEGATIVE_BASIS_V10",
        )


if __name__ == "__main__":
    unittest.main()
