from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import importlib.util
import tempfile
import unittest

import numpy as np
import pandas as pd

from ml2_features import FEATURE_NAMES


@unittest.skipUnless(importlib.util.find_spec("catboost") is not None, "catboost not installed")
class TrainingTest(unittest.TestCase):
    def test_chronological_catboost_round_trip(self) -> None:
        from ml2_model import CatBoostProbabilityModel
        from train_ml2 import train

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows: list[dict[str, object]] = []
            start = pd.Timestamp("2025-01-01T00:00:00Z")
            rng = np.random.default_rng(42)
            symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
            families = ("SWEEP_RECLAIM", "ACCEPTED_BREAK", "RANGE_ROTATION", "OTHER")
            for index in range(420):
                event = start + pd.Timedelta(hours=index)
                signal = np.sin(index / 7.0) + rng.normal(0.0, 0.35)
                label = int(signal > 0.0)
                row: dict[str, object] = {
                    "plan_id": f"plan-{index}",
                    "event_group_id": f"event-{index // 2}",
                    "decision_bucket_id": str(int(event.value)),
                    "symbol": symbols[index % len(symbols)],
                    "family": f"family-{index % 5}",
                    "side": "LONG" if index % 2 == 0 else "SHORT",
                    "event_time_ns": int(event.value),
                    "label_end_ns": int((event + pd.Timedelta(minutes=10)).value),
                    "label": label,
                    "event_date": event.strftime("%Y-%m-%d"),
                    "ml2_causal_family": families[index % len(families)],
                    "counterfactual_minutes_to_resolution": 10.0,
                    "observed_outcome_net_r": 1.5 if label else -1.0,
                    "ml2_win_net_r": 1.5,
                    "ml2_loss_net_r": -1.0,
                }
                for name in FEATURE_NAMES:
                    value = rng.normal(0.0, 0.1)
                    if name == "tf1_side_return_z":
                        value = signal
                    row[f"ml2f_{name}"] = float(value)
                rows.append(row)
            dataset = root / "dataset.csv"
            pd.DataFrame(rows).to_csv(dataset, index=False)
            metadata, report = train(
                Namespace(
                    dataset=dataset,
                    model_output=root / "model.cbm",
                    metadata_output=root / "model.json",
                    report_output=root / "report.json",
                    train_fraction=0.60,
                    calibration_fraction=0.20,
                    embargo_minutes=0,
                    minimum_samples=300,
                    minimum_split_samples=60,
                    iterations=20,
                    depth=4,
                    learning_rate=0.08,
                    l2_leaf_reg=5.0,
                    random_strength=0.2,
                    subsample=0.9,
                    random_seed=1729,
                    risk_fraction=0.03,
                ),
            )
            runtime = CatBoostProbabilityModel(root / "model.json")
            runtime.assert_selectable()
            self.assertEqual(runtime.model_id, metadata["model_id"])
            self.assertGreater(report["runtime_parity_rows"], 0)
            self.assertGreaterEqual(
                report["splits"]["test"]["calibrated_prediction"]["rows"],
                60,
            )


if __name__ == "__main__":
    unittest.main()
