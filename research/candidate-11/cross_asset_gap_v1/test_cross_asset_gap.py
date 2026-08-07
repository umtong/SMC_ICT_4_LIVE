from __future__ import annotations

import json
from pathlib import Path
import unittest

import pandas as pd

from diagnose_cross_asset_gap import (
    ADVERSE_TICKS,
    BAR_SECONDS,
    ENTRY_TAKER_RATE,
    HORIZON_SECONDS,
    MAXIMUM_BETA,
    META,
    MINIMUM_BETA,
    MINIMUM_CORRELATION,
    MINIMUM_NET_R,
    PEER_FACTOR_Z,
    PEER_RETURN_Z,
    ROLLING_BARS,
    STOP_TAKER_RATE,
    TARGET_MAKER_RATE,
    costed_geometry,
    path_outcome,
)

ROOT = Path(__file__).resolve().parent


class CrossAssetGapTests(unittest.TestCase):
    def test_long_costed_geometry_reserves_fees_and_adverse_ticks(self) -> None:
        result = costed_geometry(
            direction="LONG",
            entry_open=100.0,
            target_raw=100.8,
            stop_trigger=99.8,
            tick=META["BTCUSDT"]["tick"],
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["entry"], 100.2)
        self.assertEqual(result["target"], 100.8)
        self.assertEqual(result["stop_trigger"], 99.6)
        self.assertEqual(result["stop_execution"], 99.4)
        self.assertGreater(result["loss_per_unit"], 0.8)
        self.assertGreater(result["gain_per_unit"], 0.4)

    def test_same_second_target_and_stop_is_conservatively_a_loss(self) -> None:
        index = pd.date_range("2024-01-01T00:00:01Z", periods=2, freq="1s")
        frame = pd.DataFrame(
            {
                "open": [100.0, 100.0],
                "high": [101.0, 100.0],
                "low": [99.0, 100.0],
                "close": [100.0, 100.0],
            },
            index=index,
        )
        frame.attrs["symbol"] = "BTCUSDT"
        outcome = path_outcome(
            frame=frame,
            entry_ts=index[0],
            direction="LONG",
            geometry={
                "entry": 100.0,
                "target": 100.8,
                "stop_trigger": 99.2,
                "stop_execution": 99.0,
                "gain_per_unit": 0.6,
                "loss_per_unit": 1.0,
                "net_r": 0.6,
            },
        )
        self.assertEqual(outcome["outcome"], "BOTH_STOP_FIRST")
        self.assertEqual(outcome["realized_r"], -1.0)

    def test_protocol_is_exactly_bound_to_detector_constants(self) -> None:
        protocol = json.loads((ROOT / "protocol.json").read_text(encoding="utf-8"))
        frozen = protocol["frozen_detector"]
        self.assertEqual(frozen["bar_seconds"], BAR_SECONDS)
        self.assertEqual(frozen["rolling_bars"], ROLLING_BARS)
        self.assertEqual(frozen["peer_factor_z"], PEER_FACTOR_Z)
        self.assertEqual(frozen["peer_return_z"], PEER_RETURN_Z)
        self.assertEqual(frozen["minimum_beta"], MINIMUM_BETA)
        self.assertEqual(frozen["maximum_beta"], MAXIMUM_BETA)
        self.assertEqual(frozen["minimum_correlation"], MINIMUM_CORRELATION)
        self.assertEqual(frozen["minimum_costed_net_r"], MINIMUM_NET_R)
        self.assertEqual(frozen["entry_taker_rate"], ENTRY_TAKER_RATE)
        self.assertEqual(frozen["target_maker_rate"], TARGET_MAKER_RATE)
        self.assertEqual(frozen["stop_taker_rate"], STOP_TAKER_RATE)
        self.assertEqual(frozen["adverse_ticks"], ADVERSE_TICKS)
        self.assertEqual(frozen["maximum_horizon_seconds"], HORIZON_SECONDS)
        self.assertFalse(protocol["success_claim"])
        self.assertFalse(protocol["account_return_claim"])


if __name__ == "__main__":
    unittest.main()
