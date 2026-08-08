from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import unittest

from features_v4 import DATASET_COMMIT
from features_v4 import DATASET_ROWS
from features_v4 import DATASET_SHA256
from features_v4 import DATASET_SIZE
from features_v4 import L1_COLUMNS


ROOT = Path(__file__).resolve().parents[1]


class Candidate16V4ContractTests(unittest.TestCase):
    def test_v4_does_not_change_v2_parameters_or_gate(self) -> None:
        v2 = json.loads((ROOT / "config_v2.json").read_text(encoding="utf-8"))
        v4 = json.loads((ROOT / "config_v4.json").read_text(encoding="utf-8"))
        self.assertEqual(v4, v2)
        self.assertEqual(v4["risk_fraction"], 0.03)

    def test_dataset_identity_is_immutable(self) -> None:
        self.assertEqual(DATASET_COMMIT, "2c8dce40261855c7b57113f5a157bbeb82280bb8")
        self.assertEqual(
            DATASET_SHA256,
            "274eb8e87c7d7185a0162271144b30a0e387ae496fe657c6af83833448f08624",
        )
        self.assertEqual(DATASET_SIZE, 28_423_067)
        self.assertEqual(DATASET_ROWS, 460_265)
        self.assertEqual(
            L1_COLUMNS,
            (
                "timestamp",
                "bt_spread_bps_close",
                "bt_spread_bps_twap",
                "bt_bid_qty_close",
                "bt_ask_qty_close",
                "bt_imbalance_close",
                "bt_imbalance_twap",
                "bt_microprice_close",
                "bt_microprice_premium_close",
                "bt_update_rate",
            ),
        )

    def test_features_reuse_candidate05_and_do_not_simulate_execution(self) -> None:
        source = (ROOT / "features_v4.py").read_text(encoding="utf-8").lower()
        self.assertIn("candidate05_features.load_range", source)
        self.assertIn("pd.read_parquet", source)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("matching_engine", source)
        self.assertNotIn("portfolio =", source)

    def test_strategy_reuses_v2_temporal_and_protection_state_machine(self) -> None:
        source = (ROOT / "strategy_v4.py").read_text(encoding="utf-8")
        self.assertIn("Candidate16V2Strategy", source)
        self.assertIn("failure_pressure_transition", source)
        self.assertIn("pressure_persistence", source)
        self.assertNotIn("risk_multiplier", source.lower())
        self.assertNotIn("pnl_filter", source.lower())
        self.assertNotIn("symbol_whitelist", source.lower())

    def test_runner_remains_candidate05_nautilus_path(self) -> None:
        source = (ROOT / "candidate_v4.py").read_text(encoding="utf-8")
        self.assertIn("research/candidate-05/backtest.py", source)
        self.assertIn("NautilusTrader BacktestNode", source)
        self.assertIn('"risk_fraction": 0.03', source)
        self.assertIn('"max_global_entry_or_position": 1', source)

    def test_pre_registered_week_is_coverage_constrained_and_deterministic(self) -> None:
        seed = (
            "candidate16-v4-l1-pressure-persistence|"
            "d6da25d44168a67a630e82093bffaec146845578|"
            "coverage-constrained-week"
        )
        mondays: list[date] = []
        cursor = date(2023, 5, 22)
        while cursor <= date(2024, 3, 18):
            build_start = cursor - timedelta(days=3)
            build_end = cursor + timedelta(days=6)
            if (
                build_start.year == cursor.year == build_end.year
                and build_start.month == cursor.month == build_end.month
            ):
                mondays.append(cursor)
            cursor += timedelta(days=7)
        digest = sha256(seed.encode("utf-8")).hexdigest()
        index = int(digest, 16) % len(mondays)
        self.assertEqual(len(mondays), 32)
        self.assertEqual(
            digest,
            "fc6f6286693f25e184a7283703cf41432c80af5d200a32dace24e3dc12737ef2",
        )
        self.assertEqual(index, 18)
        self.assertEqual(mondays[index], date(2023, 11, 20))


if __name__ == "__main__":
    unittest.main()
