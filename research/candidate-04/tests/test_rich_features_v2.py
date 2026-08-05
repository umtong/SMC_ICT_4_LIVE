from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "rich_features_v2.py"
SPEC = importlib.util.spec_from_file_location("candidate04_rich_features_v2_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RichDepthTests(unittest.TestCase):
    def test_elapsed_time_change_uses_seconds_not_snapshot_rows(self) -> None:
        timestamps = [
            "2024-01-01 00:00:00",
            "2024-01-01 00:00:30",
            "2024-01-01 00:01:00",
        ]
        records: list[dict[str, object]] = []
        for stamp, bid1, ask1 in zip(timestamps, [100.0, 110.0, 121.0], [100.0, 100.0, 100.0]):
            for band in range(1, 6):
                records.append({"timestamp": stamp, "percentage": -band, "notional": bid1 * band})
                records.append({"timestamp": stamp, "percentage": band, "notional": ask1 * band})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "depth.zip"
            pd.DataFrame(records).to_csv(path, index=False, compression="zip")
            features = MODULE.aggregate_depth(path)

        at_0100 = features.loc[pd.Timestamp("2024-01-01 00:01:00", tz="UTC")]
        self.assertAlmostEqual(float(at_0100["bid_chg_1_60s"]), 0.21, places=10)
        self.assertAlmostEqual(float(at_0100["ask_chg_1_60s"]), 0.0, places=10)
        self.assertEqual(float(at_0100["depth_snapshot_age_seconds"]), 0.0)


if __name__ == "__main__":
    unittest.main()
