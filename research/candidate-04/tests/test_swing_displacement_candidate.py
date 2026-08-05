from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import pandas as pd

MODULE_PATH = Path(__file__).parents[1] / "swing_displacement_candidate.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v5", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class Candidate04V5Tests(unittest.TestCase):
    def test_cluster_uses_latest_entry_and_widest_invalidation(self) -> None:
        intents = [
            MODULE.Intent("A", 1, 10, 11, 99.0, (10,), {}),
            MODULE.Intent("A", 1, 12, 13, 101.0, (12,), {}),
        ]
        result = MODULE.cluster_intents(intents, minutes=5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].entry_index, 13)
        self.assertEqual(result[0].stop_level, 99.0)
        self.assertEqual(result[0].event_indices, (10, 12))

    def test_target_price_covers_costs_and_planned_funding(self) -> None:
        config = MODULE.Config()
        entry = 100.0
        stop = 99.0
        stop_fill = stop * (1.0 - config.stop_slippage_bps / 10_000.0)
        loss = (
            entry - stop_fill
            + config.fee_bps / 10_000.0 * (entry + stop_fill)
            + entry * config.planned_funding_bps / 10_000.0
        )
        trigger = MODULE.target_trigger(entry, 1, loss, config.target_net_r, config)
        exit_fill = trigger * (1.0 - config.market_exit_slippage_bps / 10_000.0)
        net = (
            exit_fill - entry
            - config.fee_bps / 10_000.0 * (entry + exit_fill)
            - entry * config.planned_funding_bps / 10_000.0
        )
        self.assertAlmostEqual(net / loss, config.target_net_r, places=9)

    def test_cross_day_burst_does_not_reset_at_midnight(self) -> None:
        index = pd.date_range("2024-01-01 22:00", periods=180, freq="1min", tz="UTC")
        rich = pd.DataFrame(index=index)
        rich["observed_time"] = index + pd.Timedelta(minutes=1)
        rich["mark_open"] = 100.0
        rich["mark_high"] = 100.5
        rich["mark_low"] = 99.5
        rich["trade_close"] = 100.0
        rich["metric_sum_open_interest"] = 1_000.0
        for window in (15, 30, 60):
            rich[f"notional_{window}s"] = 100.0
            rich[f"trade_count_{window}s"] = 10.0
        rich.loc[index[-1], "notional_60s"] = 200.0
        klines = pd.DataFrame(
            {
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.0,
                "volume": 1.0,
                "quote_volume": 100.0,
            },
            index=index,
        )
        data = MODULE.prepare_data(rich, klines, MODULE.Config())
        self.assertAlmostEqual(data["notional_burst_xday_60s"].iloc[-1], 2.0)

    def test_entry_is_after_signal(self) -> None:
        intent = MODULE.Intent("A", -1, 20, 21, 105.0, (10,), {})
        self.assertGreater(intent.entry_index, intent.signal_index)


if __name__ == "__main__":
    unittest.main()
