from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from derive_nt_lvcfr_v17_signals import (
    SPOT_LED_OI_EXPANSION_ACCEPTANCE,
    derive_expansion_signals,
)
from nt_lvcfr_data import CandidateConfig, MinuteFact, NS_PER_MINUTE


def minute(
    index: int,
    *,
    open_: float = 100.0,
    high: float = 100.1,
    low: float = 99.9,
    close: float = 100.0,
    notional: float = 1_000.0,
    signed: float = 0.0,
) -> MinuteFact:
    return MinuteFact(
        minute_index=index,
        open=open_,
        high=high,
        low=low,
        close=close,
        notional=notional,
        signed_notional=signed,
    )


class DualInventoryAuctionTests(unittest.TestCase):
    def test_spot_led_oi_expansion_requires_break_hold_and_cross_market_flow(self) -> None:
        futures = [minute(index) for index in range(300)]
        spot = [minute(index) for index in range(300)]
        for offset, index in enumerate(range(250, 255)):
            close = 100.06 + 0.06 * offset
            futures[index] = minute(
                index,
                open_=100.0 if offset == 0 else futures[index - 1].close,
                high=close + 0.03,
                low=99.98,
                close=close,
                notional=2_000.0,
                signed=500.0,
            )
            spot[index] = minute(
                index,
                open_=100.0 if offset == 0 else spot[index - 1].close,
                high=close + 0.03,
                low=99.98,
                close=close,
                notional=2_000.0,
                signed=500.0,
            )
        for offset, index in enumerate(range(255, 260)):
            close = 100.36 + 0.06 * offset
            futures[index] = minute(
                index,
                open_=futures[index - 1].close,
                high=close + 0.03,
                low=100.25,
                close=close,
                notional=2_000.0,
                signed=500.0,
            )
            spot[index] = minute(
                index,
                open_=spot[index - 1].close,
                high=close + 0.03,
                low=100.25,
                close=close,
                notional=2_000.0,
                signed=500.0,
            )
        futures[260] = minute(
            260,
            open_=100.60,
            high=100.80,
            low=100.55,
            close=100.75,
            notional=2_000.0,
            signed=600.0,
        )
        spot[260] = minute(
            260,
            open_=100.60,
            high=100.80,
            low=100.55,
            close=100.75,
            notional=2_000.0,
            signed=600.0,
        )
        oi = {
            end * NS_PER_MINUTE: 100.0
            for end in range(5, 301, 5)
        }
        oi[250 * NS_PER_MINUTE] = 100.0
        oi[255 * NS_PER_MINUTE] = 101.0
        oi[260 * NS_PER_MINUTE] = 102.0

        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            for name in ("futures_kline", "spot_kline", "open_interest"):
                (raw / name).mkdir()
                (raw / name / "dummy.zip").touch()
            with (
                patch(
                    "derive_nt_lvcfr_v17_signals.load_kline_minutes",
                    side_effect=[futures, spot],
                ),
                patch(
                    "derive_nt_lvcfr_v17_signals.load_open_interest",
                    return_value=oi,
                ),
            ):
                signals, counters = derive_expansion_signals(
                    raw_root=raw,
                    evaluation_start_ns=200 * NS_PER_MINUTE,
                    evaluation_end_ns=290 * NS_PER_MINUTE,
                    local_range_minutes=30,
                    waypoint_minutes=60,
                    activity_baseline_5m=4,
                    activity_min_periods=2,
                    atr_minutes=5,
                )
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal["scenario_kind"], SPOT_LED_OI_EXPANSION_ACCEPTANCE)
        self.assertEqual(signal["direction"], 1)
        self.assertEqual(signal["confirm_time_ns"], 261 * NS_PER_MINUTE)
        self.assertGreater(signal["details"]["total_oi_increase_bp"], 10.0)
        self.assertGreater(signal["details"]["hold_directional_spot_flow"], 0.0)
        self.assertEqual(counters["emitted_expansion_signals"], 1)

    def test_v17_source_discards_midpoint_only_failure_from_deleveraging_branch(self) -> None:
        source = Path(__file__).with_name("derive_nt_lvcfr_v17_signals.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("FIRST_BREAK_CHOCH_REVERSAL", source)
        self.assertIn("MEASURED_ACCEPTANCE_CONTINUATION", source)
        self.assertNotIn("MIDPOINT_FAILURE_CHOCH_REVERSAL,", source)
        self.assertIn("total_oi_increase", source)
        self.assertIn("hold_spot_flow > 0.0", source)

    def test_project_risk_and_native_execution_contract_remain_fixed(self) -> None:
        config = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v17_config.json"))
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(
            config.validation_weeks,
            ("2024-01-08", "2025-06-23", "2022-05-16"),
        )
        strategy = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("self.portfolio.net_position", strategy)
        self.assertNotIn("simulate_fill", strategy)
        self.assertNotIn("synthetic_nav", strategy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
