from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from v14_two_hour_jump_reversion_study import JumpEvent
from v14_two_hour_jump_reversion_study import aggregate_two_hour
from v14_two_hour_jump_reversion_study import collapse_simultaneous_events
from v14_two_hour_jump_reversion_study import detect_symbol_events
from v14_two_hour_jump_reversion_study import records


class Candidate16V14TwoHourJumpTests(unittest.TestCase):
    def _panel(self) -> pd.DataFrame:
        bars = 362
        minutes = bars * 120
        index = pd.date_range(
            "2023-01-01T00:00:00Z",
            periods=minutes,
            freq="min",
        ).as_unit("ns")
        panel = pd.DataFrame(index=index)
        panel["perp_open"] = 100.0
        panel["perp_high"] = 100.1
        panel["perp_low"] = 99.9
        panel["perp_close"] = 100.0
        panel["perp_quote_volume"] = 100.0

        for bar in range(bars):
            start = bar * 120
            end = start + 119
            if bar < 360:
                close = 100.1 if bar % 2 == 0 else 99.9
                open_price = 100.0
            elif bar == 360:
                open_price = 100.0
                close = 110.0
            else:
                open_price = 110.0
                close = 107.8
            path = np.linspace(open_price, close, 120)
            panel.iloc[start : end + 1, panel.columns.get_loc("perp_open")] = np.r_[
                open_price,
                path[:-1],
            ]
            panel.iloc[start : end + 1, panel.columns.get_loc("perp_close")] = path
            panel.iloc[start : end + 1, panel.columns.get_loc("perp_high")] = np.maximum(
                path,
                np.r_[open_price, path[:-1]],
            ) + 0.05
            panel.iloc[start : end + 1, panel.columns.get_loc("perp_low")] = np.minimum(
                path,
                np.r_[open_price, path[:-1]],
            ) - 0.05
        return panel

    def test_completed_two_hour_clock_and_shifted_volatility(self) -> None:
        panel = self._panel()
        bars = aggregate_two_hour(panel)
        self.assertEqual(len(bars), 362)
        self.assertEqual(
            bars.iloc[0]["bar_end_ts"],
            pd.Timestamp("2023-01-01T01:59:00Z"),
        )
        event = bars.iloc[360]
        # The current 9.5% jump must not inflate its own baseline.  The prior
        # alternating returns are about 0.1%, so the shifted sigma stays tiny.
        self.assertLess(float(event["prior_sigma"]), 0.002)
        self.assertGreater(float(event["jump_z"]), 4.0)
        self.assertLess(float(event["next_log_return"]), 0.0)

    def test_four_sigma_jump_reverses_over_exact_next_period(self) -> None:
        panel = self._panel()
        bars = aggregate_two_hour(panel)
        events = detect_symbol_events("BTCUSDT", panel, bars)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_direction, 1)
        self.assertGreater(event.reversal_gross_return, 0.0)
        self.assertGreater(event.reversal_net_return, 0.0)
        self.assertEqual(
            event.next_start_ts,
            pd.Timestamp("2023-01-31T02:00:00Z"),
        )
        self.assertEqual(
            event.next_end_ts,
            pd.Timestamp("2023-01-31T03:59:00Z"),
        )
        self.assertGreater(event.next_period_mfe, 0.0)

    def test_simultaneous_cross_asset_jump_is_one_episode(self) -> None:
        timestamp = pd.Timestamp("2023-06-01T01:59:00Z")
        common = dict(
            bar_start_ts=timestamp - pd.Timedelta(hours=2) + pd.Timedelta(minutes=1),
            bar_end_ts=timestamp,
            next_start_ts=timestamp + pd.Timedelta(minutes=1),
            next_end_ts=timestamp + pd.Timedelta(hours=2),
            jump_return=0.05,
            prior_sigma=0.01,
            event_direction=1,
            next_return=-0.01,
            reversal_gross_return=0.01,
            reversal_net_return=0.008,
            next_period_mfe=0.012,
            next_period_mae=-0.004,
        )
        weak = JumpEvent(symbol="BTCUSDT", jump_z=5.0, **common)
        strong = JumpEvent(symbol="SOLUSDT", jump_z=7.0, **common)
        later = JumpEvent(
            symbol="ETHUSDT",
            bar_start_ts=common["bar_start_ts"] + pd.Timedelta(hours=2),
            bar_end_ts=timestamp + pd.Timedelta(hours=2),
            next_start_ts=common["next_start_ts"] + pd.Timedelta(hours=2),
            next_end_ts=common["next_end_ts"] + pd.Timedelta(hours=2),
            jump_z=5.5,
            jump_return=common["jump_return"],
            prior_sigma=common["prior_sigma"],
            event_direction=common["event_direction"],
            next_return=common["next_return"],
            reversal_gross_return=common["reversal_gross_return"],
            reversal_net_return=common["reversal_net_return"],
            next_period_mfe=common["next_period_mfe"],
            next_period_mae=common["next_period_mae"],
        )
        selected = collapse_simultaneous_events([weak, strong, later])
        self.assertEqual([event.symbol for event in selected], ["SOLUSDT", "ETHUSDT"])

    def test_slots_events_serialize_without_private_state(self) -> None:
        panel = self._panel()
        event = detect_symbol_events("BTCUSDT", panel, aggregate_two_hour(panel))[0]
        frame = records([event])
        self.assertEqual(frame.iloc[0]["symbol"], "BTCUSDT")
        self.assertIn("reversal_net_return", frame.columns)


if __name__ == "__main__":
    unittest.main()
