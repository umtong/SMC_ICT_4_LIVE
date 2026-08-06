from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nt_lvcfr_data import CandidateConfig, MinuteFact, NS_PER_MINUTE, detect_signals, merge_windows


class ConfigTests(unittest.TestCase):
    def test_frozen_validation_order_and_risk(self) -> None:
        config = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_config.json"))
        self.assertEqual(config.validation_weeks, ("2024-01-08", "2025-06-23", "2022-05-16"))
        self.assertEqual(config.risk_fraction, 0.03)


class DetectorTests(unittest.TestCase):
    def _minutes(self, spot: bool = False) -> list[MinuteFact]:
        rows: list[MinuteFact] = []
        price = 100.0
        for minute in range(480):
            open_price = price
            close = price * 1.00001
            notional = 1000.0
            signed = 0.0
            # First 5m impulse ends at minute 365, second confirmation at 370.
            if 360 <= minute < 365:
                close = open_price * 1.00030
                signed = 500.0
                notional = 1500.0
            elif 365 <= minute < 370:
                close = open_price * 1.00010
                signed = 50.0 if not spot else 200.0
                notional = 1200.0
            high = max(open_price, close) + 0.01
            low = min(open_price, close) - 0.01
            rows.append(MinuteFact(minute, open_price, high, low, close, notional, signed))
            price = close
        return rows

    def test_signal_is_known_at_confirmation_and_entry_is_delayed(self) -> None:
        config = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_config.json"))
        futures = self._minutes(False)
        spot = self._minutes(True)
        oi = {}
        base = 100_000.0
        for end_minute in range(5, 481, 5):
            value = base
            if end_minute == 360:
                value = 100_000.0
            elif end_minute == 365:
                value = 99_900.0
            elif end_minute == 370:
                value = 99_700.0
            elif end_minute > 370:
                value = 99_700.0
            oi[end_minute * NS_PER_MINUTE] = value
        signals, _ = detect_signals(
            futures,
            spot,
            oi,
            start_ns=350 * NS_PER_MINUTE,
            end_ns=400 * NS_PER_MINUTE,
            config=config,
        )
        self.assertGreaterEqual(len(signals), 1)
        target = next(signal for signal in signals if signal.confirm_time_ns == 370 * NS_PER_MINUTE)
        self.assertEqual(target.eligible_time_ns, target.confirm_time_ns + NS_PER_MINUTE)
        self.assertLess(target.first_start_time_ns, target.first_end_time_ns)
        self.assertLess(target.first_end_time_ns, target.confirm_time_ns)

    def test_catalog_windows_cover_maximum_native_exposure(self) -> None:
        config = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_config.json"))
        from nt_lvcfr_data import Signal
        signal = Signal("x", 100 * NS_PER_MINUTE, 101 * NS_PER_MINUTE, 1, 99.0, 1.0, 90 * NS_PER_MINUTE, 95 * NS_PER_MINUTE, {})
        windows = merge_windows([signal], config, 1_000 * NS_PER_MINUTE)
        self.assertEqual(windows[0][0], signal.confirm_time_ns)
        minimum = config.continuation_max_holding_minutes * NS_PER_MINUTE
        self.assertGreaterEqual(windows[0][1] - windows[0][0], minimum)


if __name__ == "__main__":
    unittest.main(verbosity=2)
