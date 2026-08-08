from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

from v9_liquidation_event_study import Archive
from v9_liquidation_event_study import LIQUIDATION_COLUMNS
from v9_liquidation_event_study import _read_zip_csv
from v9_liquidation_event_study import decluster_events
from v9_liquidation_event_study import read_liquidation


class Candidate16V9StudyTests(unittest.TestCase):
    def _zip(self, text: str, name: str = "sample.csv") -> Path:
        root = Path(tempfile.mkdtemp())
        path = root / "sample.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(name, text)
        return path

    def test_external_archive_contract_matches_binance_vision(self) -> None:
        self.assertEqual(
            Archive(
                "um",
                "daily",
                "liquidationSnapshot",
                "BTCUSDT",
                "2023-10-01",
            ).url,
            "https://data.binance.vision/data/futures/um/daily/"
            "liquidationSnapshot/BTCUSDT/"
            "BTCUSDT-liquidationSnapshot-2023-10-01.zip",
        )
        self.assertEqual(
            Archive(
                "spot",
                "monthly",
                "klines",
                "ETHUSDT",
                "2023-10",
                "1m",
            ).url,
            "https://data.binance.vision/data/spot/monthly/klines/"
            "ETHUSDT/1m/ETHUSDT-1m-2023-10.zip",
        )

    def test_liquidation_parser_handles_header_and_prefers_filled_quantity(self) -> None:
        path = self._zip(
            "time,side,order_type,time_in_force,original_quantity,price,"
            "average_price,order_status,last_filled_quantity,"
            "accumulated_filled_quantity\n"
            "1696118400123,SELL,LIMIT,IOC,5,100,101,FILLED,2,3\n",
        )
        frame = read_liquidation([path])
        self.assertEqual(len(frame), 1)
        self.assertAlmostEqual(float(frame.iloc[0]["long_liq_notional"]), 303.0)
        self.assertEqual(float(frame.iloc[0]["short_liq_notional"]), 0.0)

    def test_headerless_liquidation_schema_is_supported(self) -> None:
        path = self._zip(
            "1696118400123,BUY,LIMIT,IOC,5,100,101,FILLED,2,3\n",
        )
        raw = _read_zip_csv(path, LIQUIDATION_COLUMNS)
        self.assertEqual(tuple(raw.columns), LIQUIDATION_COLUMNS)
        frame = read_liquidation([path])
        self.assertAlmostEqual(float(frame.iloc[0]["short_liq_notional"]), 303.0)

    def test_same_direction_events_are_declusted(self) -> None:
        rows = 100
        panel = pd.DataFrame(
            {
                "event_candidate": np.zeros(rows, dtype=bool),
                "event_direction": np.full(rows, -1),
                "oi_change_15m": np.full(rows, -0.01),
                "perp_basis_z_directional": np.full(rows, 2.0),
                "mark_basis_z_directional": np.full(rows, 2.0),
                "futures_lead_return": np.full(rows, 0.001),
                "directional_spot_return": np.full(rows, 0.0005),
                "perp_close": np.linspace(100.0, 101.0, rows),
                "perp_high": np.linspace(100.1, 101.1, rows),
                "perp_low": np.linspace(99.9, 100.9, rows),
                "rolling_vwap_4h": np.full(rows, 100.5),
                "symbol": np.full(rows, "BTCUSDT"),
            },
        )
        panel.loc[[10, 20, 45], "event_candidate"] = True
        events = decluster_events(panel)
        self.assertEqual(events["panel_position"].tolist(), [10, 45])
        self.assertTrue((events["regime"] == "FORCED_BASIS_DISLOCATION").all())


if __name__ == "__main__":
    unittest.main()
