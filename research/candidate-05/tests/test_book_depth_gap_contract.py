from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import urllib.error
from unittest.mock import patch

import numpy as np
import pandas as pd

import book_depth_gap_contract as contract
import features


class BookDepthGapContractTests(unittest.TestCase):
    def setUp(self) -> None:
        contract.install()

    def test_only_book_depth_404_becomes_checksum_verified_gap(self) -> None:
        error = urllib.error.HTTPError(
            "https://example.invalid/depth.zip",
            404,
            "Not Found",
            hdrs=None,
            fp=None,
        )
        with TemporaryDirectory() as directory:
            with patch.object(contract, "_BASE_DOWNLOAD_CHECKED", side_effect=error):
                archive, checksum, evidence = contract.download_checked(
                    "bookDepth",
                    "BTCUSDT",
                    date(2024, 4, 18),
                    Path(directory),
                )
            self.assertTrue(archive.exists())
            self.assertTrue(checksum.exists())
            self.assertEqual(evidence.endpoint, "bookDepth_missing_404")
            payload = json.loads(archive.read_text(encoding="utf-8"))
            self.assertEqual(payload["http_status"], 404)
            self.assertEqual(payload["policy"], "NO_IMPUTATION_FEATURE_UNREADY_NO_NEW_ENTRY")
            self.assertEqual(features.sha256_file(archive), evidence.sha256)

    def test_non_depth_or_non_404_errors_are_not_hidden(self) -> None:
        errors = (
            ("aggTrades", 404),
            ("bookDepth", 500),
        )
        with TemporaryDirectory() as directory:
            for endpoint, code in errors:
                error = urllib.error.HTTPError(
                    "https://example.invalid/archive.zip",
                    code,
                    "error",
                    hdrs=None,
                    fp=None,
                )
                with patch.object(contract, "_BASE_DOWNLOAD_CHECKED", side_effect=error):
                    with self.assertRaises(urllib.error.HTTPError):
                        contract.download_checked(
                            endpoint,
                            "BTCUSDT",
                            date(2024, 4, 18),
                            Path(directory),
                        )

    def test_gap_day_and_first_five_resumed_minutes_are_feature_unready(self) -> None:
        with TemporaryDirectory() as directory:
            sentinel, _, _ = contract._write_gap_sentinel(
                symbol="BTCUSDT",
                day=date(2024, 4, 18),
                cache=Path(directory),
                source_url="https://example.invalid/depth.zip",
            )
            gap = contract._gap_depth_frame(sentinel)

        next_index = pd.date_range(
            start=pd.Timestamp("2024-04-19T00:00:00Z"),
            periods=10,
            freq="min",
            name="minute",
        )
        resumed = pd.DataFrame(
            {
                "depth_snapshot_time": next_index + pd.Timedelta(seconds=30),
                "bid_depth_1": np.linspace(100.0, 109.0, len(next_index)),
                "ask_depth_1": np.linspace(90.0, 99.0, len(next_index)),
                "bid_depth_2": np.linspace(200.0, 209.0, len(next_index)),
                "ask_depth_2": np.linspace(180.0, 189.0, len(next_index)),
                "depth_data_gap": False,
            },
            index=next_index,
        )
        depth = pd.concat([gap, resumed])

        result_index = depth.index
        baseline = pd.DataFrame(
            {
                "feature_ready": True,
                "depth_imbalance_1": 0.1,
                "depth_imbalance_2": 0.1,
                "bid_depth_change_1_1m": 0.0,
                "ask_depth_change_1_1m": 0.0,
                "bid_depth_change_1_5m": 0.0,
                "ask_depth_change_1_5m": 0.0,
                "bid_depth_change_2_1m": 0.0,
                "ask_depth_change_2_1m": 0.0,
                "bid_depth_change_2_5m": 0.0,
                "ask_depth_change_2_5m": 0.0,
            },
            index=result_index,
        )
        with patch.object(contract, "_BASE_BUILD_FEATURES", return_value=baseline):
            result = contract.build_features(pd.DataFrame(), pd.DataFrame(), depth)

        gap_mask = result.index.date == date(2024, 4, 18)
        self.assertFalse(result.loc[gap_mask, "feature_ready"].any())
        self.assertTrue(result.loc[gap_mask, "depth_data_gap"].all())
        resumed_first_five = result.loc[
            pd.Timestamp("2024-04-19T00:00:00Z"):pd.Timestamp("2024-04-19T00:04:00Z")
        ]
        self.assertFalse(resumed_first_five["feature_ready"].any())
        self.assertTrue(resumed_first_five["depth_data_gap"].all())
        self.assertTrue(result.loc[pd.Timestamp("2024-04-19T00:05:00Z"), "feature_ready"])
        self.assertFalse(result.loc[pd.Timestamp("2024-04-19T00:05:00Z"), "depth_data_gap"])


if __name__ == "__main__":
    unittest.main()
