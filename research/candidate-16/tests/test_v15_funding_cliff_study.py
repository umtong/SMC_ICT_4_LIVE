from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

from v15_funding_cliff_study import FundingEvent
from v15_funding_cliff_study import collapse_simultaneous_events
from v15_funding_cliff_study import detect_symbol_events
from v15_funding_cliff_study import read_funding


class Candidate16V15FundingCliffTests(unittest.TestCase):
    def _zip(self, text: str) -> Path:
        root = Path(tempfile.mkdtemp())
        path = root / "funding.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("funding.csv", text)
        return path

    def test_binance_funding_schema_is_normalized(self) -> None:
        path = self._zip(
            "calc_time,funding_interval_hours,last_funding_rate\n"
            "1672531200000,8,0.00010000\n",
        )
        frame = read_funding(path, "BTCUSDT")
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["funding_ts"], pd.Timestamp("2023-01-01T00:00:00Z"))
        self.assertAlmostEqual(float(frame.iloc[0]["funding_rate"]), 0.0001)

    def _panel(self, funding_ts: pd.Timestamp, *, unwind: bool) -> pd.DataFrame:
        index = pd.date_range(
            funding_ts - pd.Timedelta(minutes=40),
            funding_ts + pd.Timedelta(minutes=130),
            freq="min",
            tz="UTC",
        ).as_unit("ns")
        panel = pd.DataFrame(index=index)
        panel["perp_open"] = 101.0
        panel["perp_high"] = 101.1
        panel["perp_low"] = 100.9
        panel["perp_close"] = 101.0
        panel["spot_open"] = 100.0
        panel["spot_high"] = 100.1
        panel["spot_low"] = 99.9
        panel["spot_close"] = 100.0

        pre_index = pd.date_range(
            funding_ts - pd.Timedelta(minutes=30),
            funding_ts - pd.Timedelta(minutes=1),
            freq="min",
            tz="UTC",
        ).as_unit("ns")
        if unwind:
            perp_path = np.linspace(101.0, 99.99, len(pre_index))
            spot_path = np.linspace(100.0, 99.8, len(pre_index))
        else:
            perp_path = np.linspace(101.0, 101.5, len(pre_index))
            spot_path = np.linspace(100.0, 100.3, len(pre_index))
        panel.loc[pre_index, "perp_open"] = np.r_[perp_path[0], perp_path[:-1]]
        panel.loc[pre_index, "perp_close"] = perp_path
        panel.loc[pre_index, "perp_high"] = np.maximum(
            panel.loc[pre_index, "perp_open"],
            perp_path,
        ) + 0.02
        panel.loc[pre_index, "perp_low"] = np.minimum(
            panel.loc[pre_index, "perp_open"],
            perp_path,
        ) - 0.02
        panel.loc[pre_index, "spot_open"] = np.r_[spot_path[0], spot_path[:-1]]
        panel.loc[pre_index, "spot_close"] = spot_path
        panel.loc[pre_index, "spot_high"] = np.maximum(
            panel.loc[pre_index, "spot_open"],
            spot_path,
        ) + 0.02
        panel.loc[pre_index, "spot_low"] = np.minimum(
            panel.loc[pre_index, "spot_open"],
            spot_path,
        ) - 0.02

        entry = funding_ts + pd.Timedelta(minutes=1)
        post_index = pd.date_range(entry, periods=120, freq="min", tz="UTC").as_unit("ns")
        if unwind:
            post_path = np.linspace(float(perp_path[-1]), float(perp_path[-1]) * 1.01, 120)
        else:
            post_path = np.linspace(float(perp_path[-1]), float(perp_path[-1]) * 0.99, 120)
        panel.loc[post_index, "perp_open"] = np.r_[post_path[0], post_path[:-1]]
        panel.loc[post_index, "perp_close"] = post_path
        panel.loc[post_index, "perp_high"] = np.maximum(
            panel.loc[post_index, "perp_open"],
            post_path,
        ) + 0.02
        panel.loc[post_index, "perp_low"] = np.minimum(
            panel.loc[post_index, "perp_open"],
            post_path,
        ) - 0.02
        return panel

    def _funding(self, funding_ts: pd.Timestamp) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "funding_ts": [funding_ts],
                "funding_rate": [0.001],
                "abs_rate": [0.001],
                "abs_threshold": [0.0005],
                "extreme_ratio": [2.0],
                "symbol": ["BTCUSDT"],
            },
        )

    def test_derivatives_led_pre_unwind_routes_to_post_settlement_rebound(self) -> None:
        funding_ts = pd.Timestamp("2023-06-01T08:00:00Z")
        events = detect_symbol_events(
            "BTCUSDT",
            self._panel(funding_ts, unwind=True),
            self._funding(funding_ts),
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.state, "PRE_SETTLEMENT_UNWIND_EXHAUSTED")
        self.assertEqual(event.policy_side, 1)
        self.assertEqual(event.entry_ts, funding_ts + pd.Timedelta(minutes=1))
        self.assertGreater(event.net_return_60m, 0.0)

    def test_intact_crowding_routes_to_funding_sign_fade(self) -> None:
        funding_ts = pd.Timestamp("2023-06-01T16:00:00Z")
        events = detect_symbol_events(
            "BTCUSDT",
            self._panel(funding_ts, unwind=False),
            self._funding(funding_ts),
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.state, "CROWDED_FUNDING_FADE")
        self.assertEqual(event.policy_side, -1)
        self.assertGreater(event.net_return_60m, 0.0)

    def test_simultaneous_funding_events_are_one_causal_episode(self) -> None:
        timestamp = pd.Timestamp("2023-06-01T08:00:00Z")
        common = dict(
            funding_ts=timestamp,
            entry_ts=timestamp + pd.Timedelta(minutes=1),
            funding_rate=0.001,
            funding_abs_threshold=0.0005,
            funding_sign=1,
            perp_spot_basis=0.001,
            pre_perp_return=-0.01,
            pre_spot_return=-0.002,
            pre_unwind_return=0.01,
            pre_futures_lead=0.008,
            state="PRE_SETTLEMENT_UNWIND_EXHAUSTED",
            policy_side=1,
            return_30m=0.005,
            return_60m=0.01,
            return_120m=0.012,
            net_return_30m=0.003,
            net_return_60m=0.008,
            net_return_120m=0.010,
            mfe_120m=0.014,
            mae_120m=-0.003,
        )
        weak = FundingEvent(symbol="BTCUSDT", funding_extreme_ratio=1.2, **common)
        strong = FundingEvent(symbol="SOLUSDT", funding_extreme_ratio=2.5, **common)
        selected = collapse_simultaneous_events([weak, strong])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].symbol, "SOLUSDT")


if __name__ == "__main__":
    unittest.main()
