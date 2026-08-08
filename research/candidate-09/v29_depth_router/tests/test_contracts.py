from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

from data_loader import parse_depth_archive
from state_engine import EngineConfig, FlowBar, risk_based_quantity

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


class V29Contracts(unittest.TestCase):
    def test_ablations_remove_only_the_declared_layer(self):
        self.assertFalse(
            EngineConfig.from_mapping(CONFIG, ablation="no-depth").use_depth
        )
        self.assertFalse(
            EngineConfig.from_mapping(CONFIG, ablation="no-flow").use_flow
        )
        self.assertFalse(
            EngineConfig.from_mapping(
                CONFIG,
                ablation="no-replenishment",
            ).require_replenishment
        )

    def test_risk_budget_never_exceeds_three_percent(self):
        sizing = risk_based_quantity(
            nav=Decimal("100000"),
            risk_fraction=Decimal("0.03"),
            entry_price=Decimal("100"),
            stop_price=Decimal("99"),
            cost_rate_per_fill=Decimal("0.00075"),
            quantity_increment=Decimal("0.001"),
        )
        self.assertLessEqual(sizing.planned_loss, Decimal("3000"))

    def test_last_snapshot_in_minute_is_available_only_next_minute(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "depth.zip"
            csv_text = (
                "timestamp,percentage,depth,notional\n"
                "2024-10-14 00:00:07,-1,10,1000\n"
                "2024-10-14 00:00:07,1,20,2000\n"
                "2024-10-14 00:00:45,-1,11,1100\n"
                "2024-10-14 00:00:45,1,21,2100\n"
            )
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("sample.csv", csv_text)
            snapshots = parse_depth_archive(path)
            self.assertEqual(len(snapshots), 1)
            snapshot = snapshots[0]
            self.assertEqual(snapshot.bid_notional, 1100)
            self.assertEqual(snapshot.ask_notional, 2100)
            self.assertEqual(
                snapshot.available_ns - snapshot.observed_ns,
                15_000_000_000,
            )
            self.assertLess(snapshot.observed_ns, snapshot.available_ns)

    def test_flow_bar_depth_observation_is_strictly_prior(self):
        bar = FlowBar(
            120_000_000_000,
            100,
            101,
            99,
            100,
            10,
            5,
            10,
            100,
            100,
            1_000_000,
            1_000_000,
            59_000_000_000,
        )
        self.assertLess(bar.depth_observed_ns, bar.ts_ns)


if __name__ == "__main__":
    unittest.main()
