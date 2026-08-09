from __future__ import annotations

import unittest

from entry_scenario_validity_logic import DAYTRADE_HORIZON_EXPIRED
from entry_scenario_validity_logic import EVALUATION_BOUNDARY
from entry_scenario_validity_logic import FUNDING_BOUNDARY
from entry_scenario_validity_logic import STRUCTURAL_STOP
from entry_scenario_validity_logic import TARGET_COMPLETED
from entry_scenario_validity_logic import TARGET_SOURCE_EXPIRED
from entry_scenario_validity_logic import scenario_entry_cancel_reason


class EntryScenarioValidityLogicTest(unittest.TestCase):
    def test_valid_scenario_has_no_fixed_bar_expiry(self) -> None:
        self.assertIsNone(
            scenario_entry_cancel_reason(
                side=1,
                high=101.0,
                low=99.0,
                structural_stop=95.0,
                target=110.0,
                target_source_active=True,
                bar_index=80,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=True,
            ),
        )

    def test_stop_and_target_are_mirror_symmetric(self) -> None:
        self.assertEqual(
            scenario_entry_cancel_reason(
                side=1,
                high=101.0,
                low=95.0,
                structural_stop=95.0,
                target=110.0,
                target_source_active=True,
                bar_index=1,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=True,
            ),
            STRUCTURAL_STOP,
        )
        self.assertEqual(
            scenario_entry_cancel_reason(
                side=-1,
                high=105.0,
                low=99.0,
                structural_stop=105.0,
                target=90.0,
                target_source_active=True,
                bar_index=1,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=True,
            ),
            STRUCTURAL_STOP,
        )
        self.assertEqual(
            scenario_entry_cancel_reason(
                side=1,
                high=110.0,
                low=99.0,
                structural_stop=95.0,
                target=110.0,
                target_source_active=True,
                bar_index=1,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=True,
            ),
            TARGET_COMPLETED,
        )
        self.assertEqual(
            scenario_entry_cancel_reason(
                side=-1,
                high=101.0,
                low=90.0,
                structural_stop=105.0,
                target=90.0,
                target_source_active=True,
                bar_index=1,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=True,
            ),
            TARGET_COMPLETED,
        )

    def test_structural_stop_precedes_same_bar_target(self) -> None:
        self.assertEqual(
            scenario_entry_cancel_reason(
                side=1,
                high=110.0,
                low=95.0,
                structural_stop=95.0,
                target=110.0,
                target_source_active=True,
                bar_index=1,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=True,
            ),
            STRUCTURAL_STOP,
        )

    def test_source_horizon_and_boundaries(self) -> None:
        common = dict(
            side=1,
            high=101.0,
            low=99.0,
            structural_stop=95.0,
            target=110.0,
        )
        self.assertEqual(
            scenario_entry_cancel_reason(
                **common,
                target_source_active=False,
                bar_index=10,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=True,
            ),
            TARGET_SOURCE_EXPIRED,
        )
        self.assertEqual(
            scenario_entry_cancel_reason(
                **common,
                target_source_active=True,
                bar_index=180,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=True,
            ),
            DAYTRADE_HORIZON_EXPIRED,
        )
        self.assertEqual(
            scenario_entry_cancel_reason(
                **common,
                target_source_active=True,
                bar_index=10,
                horizon_index=180,
                funding_blackout=True,
                in_evaluation=True,
            ),
            FUNDING_BOUNDARY,
        )
        self.assertEqual(
            scenario_entry_cancel_reason(
                **common,
                target_source_active=True,
                bar_index=10,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=False,
            ),
            EVALUATION_BOUNDARY,
        )

    def test_invalid_side_and_bar_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            scenario_entry_cancel_reason(
                side=0,
                high=101.0,
                low=99.0,
                structural_stop=95.0,
                target=110.0,
                target_source_active=True,
                bar_index=1,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=True,
            )
        with self.assertRaises(ValueError):
            scenario_entry_cancel_reason(
                side=1,
                high=98.0,
                low=99.0,
                structural_stop=95.0,
                target=110.0,
                target_source_active=True,
                bar_index=1,
                horizon_index=180,
                funding_blackout=False,
                in_evaluation=True,
            )


if __name__ == "__main__":
    unittest.main()
