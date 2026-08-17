from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import pandas as pd

from build_ml2_dataset import build_dataset
from ml2_features import FEATURE_NAMES


class DatasetBuildTest(unittest.TestCase):
    def test_exact_identity_and_runtime_consistent_ambiguous_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event: dict[str, object] = {
                "kind": "ml2_plan",
                "plan_id": "p1",
                "causal_event_id": "episode-1",
                "ts_ns": int(pd.Timestamp("2025-01-01T00:00:00Z").value),
                "symbol": "BTCUSDT",
                "family": "HORIZONTAL_FLIP_CONTINUATION",
                "side": "LONG",
                "scenario_path": "ACCEPTANCE",
                "ml2_causal_family": "ACCEPTED_BREAK",
                "ml2_win_net_r": 1.4,
                "ml2_loss_net_r": -1.05,
                "ml2_required_log_probability": 0.44,
            }
            event.update({f"ml2f_{name}": 0.0 for name in FEATURE_NAMES})
            events = root / "decision_events.csv"
            pd.DataFrame([event]).to_csv(events, index=False)
            labels = root / "counterfactual_plans.csv"
            pd.DataFrame(
                [
                    {
                        "plan_id": "p1",
                        "counterfactual_outcome": "AMBIGUOUS_SAME_MINUTE",
                        "counterfactual_resolution_time": "2025-01-01T00:03:00+00:00",
                        "counterfactual_minutes_to_resolution": 3.0,
                    },
                ],
            ).to_csv(labels, index=False)
            output = root / "dataset.csv"
            summary = build_dataset(
                events_path=events,
                counterfactual_path=labels,
                output_path=output,
                summary_path=root / "summary.json",
            )
            frame = pd.read_csv(output)
            self.assertEqual(len(frame), 1)
            self.assertEqual(frame.loc[0, "label"], 0.0)
            self.assertEqual(frame.loc[0, "observed_outcome_net_r"], -1.05)
            self.assertEqual(frame.loc[0, "event_group_id"], "BTCUSDT|episode-1")
            self.assertGreater(
                int(frame.loc[0, "label_end_ns"]),
                int(frame.loc[0, "event_time_ns"]),
            )
            self.assertEqual(summary["stop_or_ambiguous"], 1)


if __name__ == "__main__":
    unittest.main()
