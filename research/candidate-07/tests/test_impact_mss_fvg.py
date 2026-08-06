from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from model_impact_mss_fvg import ImpactEvent, ImpactMSSFVGLogic, diagnose


class ImpactMSSFVGTests(unittest.TestCase):
    def _minutes(self) -> pd.DataFrame:
        base = 1_766_400_000
        count = 80
        timestamps = np.array(
            [
                (base + 60 * index) * 1_000_000_000
                for index in range(count)
            ],
            dtype=np.int64,
        )
        close = np.linspace(98.0, 99.0, count)
        open_ = close - 0.02
        high = np.maximum(open_, close) + 0.08
        low = np.minimum(open_, close) - 0.08

        # Causally confirmed swing high at index 62, confirmation at 64.
        high[62] = 100.0
        high[60:62] = [99.2, 99.4]
        high[63:65] = [99.5, 99.6]

        # Bars 65-67 create a bullish MSS displacement FVG at 67.
        open_[65], close[65], high[65], low[65] = 98.9, 99.1, 99.2, 98.8
        open_[66], close[66], high[66], low[66] = 99.1, 99.6, 99.7, 99.0
        open_[67], close[67], high[67], low[67] = 99.8, 100.6, 100.7, 99.75

        # First valid retest enters [99.2, 99.75], does not traverse 99.2,
        # and closes back above the proximal 99.75 edge.
        open_[68], close[68], high[68], low[68] = 100.0, 100.1, 100.2, 99.6
        open_[69], close[69], high[69], low[69] = 100.1, 100.5, 100.6, 100.0
        atr = np.ones(count)
        return pd.DataFrame(
            {
                "timestamp_ns": timestamps,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "atr": atr,
            }
        )

    def _event(self, minutes: pd.DataFrame) -> ImpactEvent:
        return ImpactEvent(
            event_id="impact-1",
            direction="LONG",
            event_end_ns=int(minutes.iloc[64]["timestamp_ns"]),
            source_pool_id="5ML-source",
            source_level=98.7,
            event_extreme=98.5,
        )

    @staticmethod
    def _logic() -> ImpactMSSFVGLogic:
        return ImpactMSSFVGLogic(displacement_rank_period=20)

    def test_complete_sequence_routes_first_fvg_retest(self) -> None:
        minutes = self._minutes()
        plans, diagnostics = diagnose(
            minutes,
            events=[self._event(minutes)],
            logic=self._logic(),
            require_fvg_retest=True,
        )
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.direction, "LONG")
        self.assertEqual(plan.mss_ns, int(minutes.iloc[67]["timestamp_ns"]))
        self.assertEqual(plan.retest_ns, int(minutes.iloc[68]["timestamp_ns"]))
        self.assertTrue(plan.retest_required)
        self.assertLess(plan.fvg_lower, plan.fvg_upper)
        self.assertEqual(diagnostics[-1].outcome, "ENTRY_READY")

    def test_full_fvg_traversal_invalidates_episode(self) -> None:
        minutes = self._minutes()
        minutes.loc[68, "low"] = 99.0
        plans, diagnostics = diagnose(
            minutes,
            events=[self._event(minutes)],
            logic=self._logic(),
            require_fvg_retest=True,
        )
        self.assertEqual(plans, [])
        self.assertIn(
            "RETEST_INVALIDATED",
            [item.outcome for item in diagnostics],
        )

    def test_controlled_ablation_enters_on_mss_close_only(self) -> None:
        minutes = self._minutes()
        plans, _ = diagnose(
            minutes,
            events=[self._event(minutes)],
            logic=self._logic(),
            require_fvg_retest=False,
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(
            plans[0].observed_ns,
            int(minutes.iloc[67]["timestamp_ns"]),
        )
        self.assertIsNone(plans[0].retest_ns)
        self.assertFalse(plans[0].retest_required)

    def test_no_displacement_mss_does_not_route(self) -> None:
        minutes = self._minutes()
        minutes.loc[67, ["open", "close", "high", "low"]] = [
            99.8,
            99.9,
            100.05,
            99.75,
        ]
        plans, diagnostics = diagnose(
            minutes,
            events=[self._event(minutes)],
            logic=self._logic(),
            require_fvg_retest=True,
        )
        self.assertEqual(plans, [])
        self.assertIn(
            "MSS_FVG_NOT_CONFIRMED_WITHIN_WINDOW",
            [item.outcome for item in diagnostics],
        )


if __name__ == "__main__":
    unittest.main()
