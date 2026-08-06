from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v4_signals import (
    EXTERNAL_RECLAIM_REVERSAL,
    VALUE_EDGE_CONTINUATION,
)
from derive_nt_lvcfr_v5_signals import RANGE_MIGRATION_RECLAIM_REVERSAL
from derive_nt_lvcfr_v6_signals import (
    DEALING_RANGE_EQUILIBRIUM_OBJECTIVE,
    EVENT_EXTREME_OBJECTIVE,
    attach_structural_targets,
)
from nt_lvcfr_data import CandidateConfig
from nt_lvcfr_strategy import signal_structural_target


def signal(state: str, direction: int) -> dict:
    return {
        "scenario_id": f"TEST-{state}",
        "scenario_kind": state,
        "direction": direction,
        "details": {
            "event_low": 90.0,
            "event_high": 110.0,
            "dealing_range_low": 80.0,
            "dealing_range_high": 120.0,
        },
    }


class StructuralObjectiveScheduleTests(unittest.TestCase):
    def test_migration_reclaim_long_targets_event_high(self) -> None:
        item = signal(RANGE_MIGRATION_RECLAIM_REVERSAL, 1)
        counts = attach_structural_targets([item])
        self.assertEqual(item["structural_target"], 110.0)
        self.assertEqual(item["details"]["structural_objective"], EVENT_EXTREME_OBJECTIVE)
        self.assertEqual(counts[EVENT_EXTREME_OBJECTIVE], 1)

    def test_migration_reclaim_short_targets_event_low(self) -> None:
        item = signal(RANGE_MIGRATION_RECLAIM_REVERSAL, -1)
        attach_structural_targets([item])
        self.assertEqual(item["structural_target"], 90.0)

    def test_external_reclaim_targets_prior_equilibrium(self) -> None:
        item = signal(EXTERNAL_RECLAIM_REVERSAL, 1)
        counts = attach_structural_targets([item])
        self.assertEqual(item["structural_target"], 100.0)
        self.assertEqual(
            item["details"]["structural_objective"],
            DEALING_RANGE_EQUILIBRIUM_OBJECTIVE,
        )
        self.assertEqual(counts[DEALING_RANGE_EQUILIBRIUM_OBJECTIVE], 1)

    def test_value_edge_keeps_existing_objective(self) -> None:
        item = signal(VALUE_EDGE_CONTINUATION, 1)
        counts = attach_structural_targets([item])
        self.assertNotIn("structural_target", item)
        self.assertEqual(item["target_mode"], "EXISTING_NET_R_OBJECTIVE")
        self.assertEqual(counts["GENERIC_EXISTING_OBJECTIVE"], 1)


class StrategyObjectiveContractTests(unittest.TestCase):
    def test_structural_target_validation(self) -> None:
        self.assertIsNone(signal_structural_target({}))
        self.assertEqual(signal_structural_target({"structural_target": 101.25}), 101.25)
        with self.assertRaises(ValueError):
            signal_structural_target({"structural_target": -1.0})

    def test_strategy_uses_target_only_for_execution_not_pnl_reconstruction(self) -> None:
        source = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        self.assertIn("STRUCTURAL_TARGET", source)
        self.assertIn("CAUSAL_LIQUIDITY_OBJECTIVE_NOT_AHEAD_OF_EXECUTABLE_ENTRY", source)
        self.assertIn("self.portfolio.net_position", source)
        self.assertNotIn("synthetic_nav", source)

    def test_v6_keeps_fixed_project_risk_and_validation_order(self) -> None:
        config = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v6_config.json"))
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(
            config.validation_weeks,
            ("2024-01-08", "2025-06-23", "2022-05-16"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
