from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nt_lvcfr_data import CandidateConfig, MinuteFact, NS_PER_MINUTE, detect_signals, merge_windows, select_second_extrema
from nt_lvcfr_strategy import expected_funding_debit_per_unit, native_equity_amount


class FundingRiskTests(unittest.TestCase):
    def test_no_prorated_cost_when_max_hold_ends_before_boundary(self) -> None:
        self.assertEqual(
            expected_funding_debit_per_unit(
                entry_price=40_000.0, direction=1, funding_rate=0.0001,
                entry_time_ns=2 * 60 * NS_PER_MINUTE, max_holding_minutes=240,
                next_funding_ns=8 * 60 * NS_PER_MINUTE, funding_interval_minutes=480,
            ),
            0.0,
        )

    def test_full_adverse_rate_when_boundary_is_crossed(self) -> None:
        self.assertEqual(
            expected_funding_debit_per_unit(
                entry_price=40_000.0, direction=1, funding_rate=0.0001,
                entry_time_ns=5 * 60 * NS_PER_MINUTE, max_holding_minutes=240,
                next_funding_ns=8 * 60 * NS_PER_MINUTE, funding_interval_minutes=480,
            ),
            4.0,
        )

    def test_favorable_funding_is_not_used_to_increase_risk_quantity(self) -> None:
        self.assertEqual(
            expected_funding_debit_per_unit(
                entry_price=40_000.0, direction=-1, funding_rate=0.0001,
                entry_time_ns=5 * 60 * NS_PER_MINUTE, max_holding_minutes=240,
                next_funding_ns=8 * 60 * NS_PER_MINUTE, funding_interval_minutes=480,
            ),
            0.0,
        )


class NativeEquityTests(unittest.TestCase):
    class Portfolio:
        def __init__(self, values):
            self.values = values

        def equity(self, venue):
            return self.values

    def test_reads_requested_currency_from_native_map(self) -> None:
        portfolio = self.Portfolio({"BTC": 1.0, "USDT": 100_123.5})
        self.assertEqual(native_equity_amount(portfolio, "BINANCE", "USDT"), 100_123.5)

    def test_single_currency_native_map_is_unambiguous(self) -> None:
        portfolio = self.Portfolio({"USDT": 99_999.25})
        self.assertEqual(native_equity_amount(portfolio, "BINANCE", object()), 99_999.25)


class PositionEventContractTests(unittest.TestCase):
    def test_strategy_uses_nautilus_1230_position_closed_fields(self) -> None:
        source = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        self.assertIn("event.peak_quantity", source)
        self.assertIn("event.duration", source)
        self.assertNotIn("event.peak_qty", source)
        self.assertNotIn("event.duration_ns", source)
        self.assertIn("event.ts_closed is not None", source)
        self.assertIn("event.avg_px_close is not None", source)


class ConfigTests(unittest.TestCase):
    def test_backtest_node_uses_funding_compatible_oneshot_loader(self) -> None:
        source = Path(__file__).with_name("run_nt_lvcfr.py").read_text(encoding="utf-8")
        self.assertIn("chunk_size=None", source)
        self.assertNotIn("chunk_size=1_000_000", source)

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


class QuoteCompressionTests(unittest.TestCase):
    def test_original_time_second_envelope_preserves_price_extrema(self) -> None:
        rows = [
            (1, 100.0, 1.0, 101.0, 1.0, 1, 1),
            (2, 100.0, 2.0, 101.0, 2.0, 2, 2),
            (3, 99.0, 3.0, 101.0, 3.0, 3, 3),
            (4, 101.0, 4.0, 102.0, 4.0, 4, 4),
            (5, 100.0, 5.0, 100.0, 5.0, 5, 5),
        ]
        selected = select_second_extrema(rows)
        self.assertEqual([row[0] for row in selected], [1, 3, 4, 5])
        self.assertEqual(selected, sorted(selected, key=lambda row: row[6]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
