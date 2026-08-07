from __future__ import annotations

import unittest

from strategy_global_slot import PortfolioGlobalSlot


class PortfolioGlobalSlotTests(unittest.TestCase):
    def test_first_signal_reserves_and_other_instrument_cannot_enter(self) -> None:
        slot = PortfolioGlobalSlot()
        self.assertTrue(
            slot.reserve(
                instrument_id="BTCUSDT-PERP.BINANCE",
                scenario_id="BTCUSDT:one",
                timestamp_ns=10,
            )
        )
        self.assertFalse(
            slot.reserve(
                instrument_id="XRPUSDT-PERP.BINANCE",
                scenario_id="XRPUSDT:two",
                timestamp_ns=11,
            )
        )
        self.assertEqual(slot.owner_instrument_id, "BTCUSDT-PERP.BINANCE")
        self.assertEqual(slot.owner_scenario_id, "BTCUSDT:one")

    def test_only_owner_can_release_slot(self) -> None:
        slot = PortfolioGlobalSlot()
        slot.reserve(
            instrument_id="BTCUSDT-PERP.BINANCE",
            scenario_id="BTCUSDT:one",
            timestamp_ns=10,
        )
        self.assertFalse(
            slot.release(
                instrument_id="XRPUSDT-PERP.BINANCE",
                timestamp_ns=12,
                reason="NOT_OWNER",
            )
        )
        self.assertFalse(slot.is_free)
        self.assertTrue(
            slot.release(
                instrument_id="BTCUSDT-PERP.BINANCE",
                timestamp_ns=13,
                reason="POSITION_CLOSED",
            )
        )
        self.assertTrue(slot.is_free)

    def test_nav_is_one_portfolio_clock_not_two_symbol_series(self) -> None:
        slot = PortfolioGlobalSlot()
        slot.record_nav(
            timestamp_ns=10,
            nav=100_000.0,
            observer_instrument_id="BTCUSDT-PERP.BINANCE",
        )
        slot.record_nav(
            timestamp_ns=10,
            nav=100_100.0,
            observer_instrument_id="XRPUSDT-PERP.BINANCE",
        )
        slot.record_nav(
            timestamp_ns=11,
            nav=100_200.0,
            observer_instrument_id="BTCUSDT-PERP.BINANCE",
        )
        self.assertEqual(len(slot.nav_series), 2)
        self.assertEqual(slot.nav_series[0]["nav"], 100_100.0)
        self.assertEqual(slot.nav_series[-1]["timestamp_ns"], 11)

    def test_combined_trades_preserve_instrument_identity(self) -> None:
        slot = PortfolioGlobalSlot()
        slot.record_trades(
            instrument_id="XRPUSDT-PERP.BINANCE",
            new_trades=[{"scenario_id": "x", "opened_ns": 1, "closed_ns": 2}],
        )
        self.assertEqual(
            slot.trades[0]["instrument_id"],
            "XRPUSDT-PERP.BINANCE",
        )


if __name__ == "__main__":
    unittest.main()
